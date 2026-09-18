"""Build logical graph model from Azure resources."""

from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class GraphNode:
    """Represents a node in the topology graph."""

    def __init__(self, node_id: str, node_type: str, label: str, parent: str = None, data: Dict[str, Any] = None):
        self.id = node_id
        self.type = node_type
        self.label = label
        self.parent = parent
        self.data = data or {}

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "data": self.data,
        }
        if self.parent:
            result["parent"] = self.parent
        return result


class GraphEdge:
    """Represents an edge in the topology graph."""

    def __init__(self, edge_id: str, source: str, target: str, edge_type: str, data: Dict[str, Any] = None):
        self.id = edge_id
        self.source = source
        self.target = target
        self.type = edge_type
        self.data = data or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "data": self.data,
        }


def _nic_label(nic: Dict[str, Any]) -> str:
    """Caption for a NIC card: name, private IP, and a public one if it has it.

    A public IP on a NIC is worth reading off the diagram without clicking —
    it's usually a management interface, and it's the part of the topology
    facing the internet.
    """
    lines = [nic["name"], nic.get("private_ip") or "no IP"]
    if nic.get("public_ip"):
        lines.append(f"public {nic['public_ip']}")
    return "\n".join(lines)


class TopologyGraph:
    """Build and manage the logical topology graph."""

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: Dict[str, GraphEdge] = {}
        self.node_counter = 0
        self.edge_counter = 0

    def add_node(self, node_id: str, node_type: str, label: str, parent: str = None, data: Dict[str, Any] = None) -> GraphNode:
        """Add a node to the graph."""
        node = GraphNode(node_id, node_type, label, parent, data)
        self.nodes[node_id] = node
        return node

    def add_edge(self, source: str, target: str, edge_type: str, data: Dict[str, Any] = None) -> GraphEdge:
        """Add an edge to the graph."""
        edge_id = f"edge_{self.edge_counter}"
        self.edge_counter += 1
        edge = GraphEdge(edge_id, source, target, edge_type, data)
        self.edges[edge_id] = edge
        return edge

    def build_from_azure_scan(self, scan_data: Dict[str, Any]) -> None:
        """Build graph from Azure scan data (VNets, subnets, peerings, VMs).

        VM and NIC nodes are always built when the scan carries them. Whether
        they are *drawn* is the frontend's call — it collapses subnets busier
        than the render limit down to a click-to-drill-in summary. Holding the
        whole tree here means changing that limit re-renders instantly rather
        than forcing a rescan.
        """
        vnets = scan_data.get("vnets", {})
        peerings = scan_data.get("peerings", [])

        # Index NIC attachments by the subnet they land on. ARM IDs vary in
        # case between resources, so the ingest lowercases these keys.
        nics_by_subnet: Dict[str, List[Dict[str, Any]]] = {}
        for nic in scan_data.get("nics", []):
            nics_by_subnet.setdefault(nic["subnet_id"], []).append(nic)
        vms_by_id = scan_data.get("vms", {})

        # A VM whose NICs land in more than one subnet — a firewall or an NVA,
        # typically — belongs in no single subnet box, and drawing a copy of it
        # inside each one makes four firewalls out of one. Such a VM instead
        # gets a single node at the VNet level wired to a NIC in each subnet.
        # Azure requires every NIC on a VM to sit in the same VNet, so that
        # level always exists and is always the right home for it.
        subnets_per_vm: Dict[str, set] = {}
        for nic in scan_data.get("nics", []):
            if nic.get("vm_id"):
                subnets_per_vm.setdefault(
                    nic["vm_id"].lower(), set()
                ).add(nic["subnet_id"])
        spanning_vms = {
            vm_id for vm_id, subnets in subnets_per_vm.items()
            if len(subnets) > 1
        }
        # vm_id -> {vnet node, [nic node ids]}, filled in as subnets are walked
        spanning_attachments: Dict[str, Dict[str, Any]] = {}

        # Lookups the load-balancer pass needs. A backend NIC may have no node
        # of its own — a NIC on a single-subnet VM is folded into that VM's
        # card — so nic_nodes maps a NIC to whatever now stands for it.
        subnet_nodes: Dict[str, str] = {}
        nic_nodes: Dict[str, str] = {}
        node_vnets: Dict[str, str] = {}
        vnet_rgs: Dict[str, str] = {}

        # Track resource groups so we create each one only once
        resource_groups = set()

        # Create nodes for each VNet
        for vnet_name, vnet_info in vnets.items():
            rg_name = vnet_info["resource_group"]
            rg_id = f"rg_{rg_name}"

            # Create resource group compound node if needed
            if rg_name not in resource_groups:
                resource_groups.add(rg_name)
                self.add_node(
                    rg_id, "resource_group", rg_name,
                    data={"location": vnet_info["location"]},
                )

            # Create VNet as child of resource group
            vnet_id = f"vnet_{vnet_name}"
            vnet_rgs[vnet_id] = rg_id
            prefixes = vnet_info["address_prefixes"]
            vnet_label = (
                f"{vnet_name}\n{', '.join(prefixes)}" if prefixes
                else vnet_name
            )
            self.add_node(
                vnet_id, "vnet", vnet_label,
                parent=rg_id,
                data={
                    "azure_id": vnet_info["id"],
                    "resource_group": rg_name,
                    "address_prefixes": prefixes,
                    "location": vnet_info["location"],
                },
            )

            # Create subnets as children of VNet
            for subnet in vnet_info.get("subnets", []):
                addr = subnet["address_prefix"] or ""
                subnet_label = (
                    f"{subnet['name']}\n{addr}" if addr
                    else subnet["name"]
                )
                subnet_node_id = f"subnet_{vnet_name}_{subnet['name']}"
                subnet_nics = nics_by_subnet.get(
                    (subnet["id"] or "").lower(), []
                )
                vm_ids = {
                    n["vm_id"].lower() for n in subnet_nics if n.get("vm_id")
                }

                self.add_node(
                    subnet_node_id,
                    "subnet",
                    subnet_label,
                    parent=vnet_id,
                    data={
                        "azure_id": subnet["id"],
                        "address_prefix": addr,
                        # The frontend marks and collapses on these, so they
                        # stay accurate even when the children aren't drawn.
                        "nic_count": len(subnet_nics),
                        "vm_count": len(vm_ids),
                        "lb_count": 0,
                    },
                )

                subnet_nodes[(subnet["id"] or "").lower()] = subnet_node_id
                node_vnets[subnet_node_id] = vnet_id

                self._add_subnet_workloads(
                    subnet_node_id, vnet_id, subnet_nics, vms_by_id,
                    spanning_vms, spanning_attachments, nic_nodes, node_vnets,
                )

        self._add_spanning_vms(spanning_attachments, vms_by_id)
        self._add_load_balancers(
            scan_data.get("load_balancers", []),
            subnet_nodes, nic_nodes, node_vnets, vnet_rgs,
        )

        # Create peering edges
        for peering in peerings:
            source_vnet = peering.get("source_vnet")
            remote_vnet_id = peering.get("remote_vnet_id", "")
            remote_vnet_name = (
                remote_vnet_id.split("/")[-1] if remote_vnet_id else None
            )

            if source_vnet and remote_vnet_name:
                source_id = f"vnet_{source_vnet}"
                target_id = f"vnet_{remote_vnet_name}"

                if source_id in self.nodes and target_id in self.nodes:
                    self.add_edge(source_id, target_id, "peered_to", {
                        "peering_state": peering.get(
                            "peering_state", "Unknown"
                        ),
                        "peering_name": peering.get("name"),
                    })

    def _add_subnet_workloads(
        self,
        subnet_node_id: str,
        vnet_node_id: str,
        subnet_nics: List[Dict[str, Any]],
        vms_by_id: Dict[str, Dict[str, Any]],
        spanning_vms: set,
        spanning_attachments: Dict[str, Dict[str, Any]],
        nic_nodes: Dict[str, str],
        node_vnets: Dict[str, str],
    ) -> None:
        """Add the VM and NIC nodes living inside one subnet.

        A VM confined to this subnet becomes a compound node holding one detail
        child (size, OS, and its IPs here). A VM that spans subnets gets only
        its NIC drawn here; the VM itself is recorded for later, so one node can
        stand for it across every subnet it reaches. A NIC with no VM attached
        is drawn on its own, since an orphaned NIC is usually worth noticing.
        """
        vm_nics: Dict[str, List[Dict[str, Any]]] = {}

        for nic in subnet_nics:
            vm_id = (nic.get("vm_id") or "").lower()

            if vm_id and vm_id in spanning_vms:
                nic_node_id = f"nic_{subnet_node_id}_{nic['name']}"
                self.add_node(
                    nic_node_id, "nic", _nic_label(nic),
                    parent=subnet_node_id,
                    data={
                        "azure_id": nic["id"],
                        "private_ip": nic.get("private_ip"),
                        "public_ip": nic.get("public_ip"),
                        "vm_name": nic.get("vm_name"),
                        "orphan": False,
                    },
                )
                entry = spanning_attachments.setdefault(
                    vm_id, {"vnet": vnet_node_id, "nics": []}
                )
                entry["nics"].append(nic_node_id)
                nic_nodes[nic["id"].lower()] = nic_node_id
                node_vnets[nic_node_id] = vnet_node_id
            elif vm_id:
                vm_nics.setdefault(vm_id, []).append(nic)
            else:
                orphan_node_id = f"nic_{subnet_node_id}_{nic['name']}"
                nic_nodes[nic["id"].lower()] = orphan_node_id
                node_vnets[orphan_node_id] = vnet_node_id
                self.add_node(
                    orphan_node_id,
                    "nic",
                    _nic_label(nic),
                    parent=subnet_node_id,
                    data={
                        "azure_id": nic["id"],
                        "private_ip": nic.get("private_ip"),
                        "public_ip": nic.get("public_ip"),
                        "orphan": True,
                    },
                )

        for vm_id, nics in vm_nics.items():
            vm = vms_by_id.get(vm_id, {})
            vm_name = vm.get("name") or nics[0].get("vm_name") or "unknown-vm"
            vm_node_id = f"vm_{subnet_node_id}_{vm_name}"
            ips = ", ".join(n.get("private_ip") or "?" for n in nics)
            # This card stands for NICs that get no node of their own, so a
            # public IP on one of them would otherwise vanish from the diagram.
            public = ", ".join(
                n["public_ip"] for n in nics if n.get("public_ip")
            )

            self.add_node(
                vm_node_id, "vm", vm_name,
                parent=subnet_node_id,
                data={
                    "azure_id": vm.get("id") or vm_id,
                    "vm_size": vm.get("vm_size", ""),
                    "os_type": vm.get("os_type", ""),
                    "private_ips": ips,
                    "public_ips": public,
                    "nic_names": [n["name"] for n in nics],
                },
            )

            detail = [p for p in (
                vm.get("vm_size"), vm.get("os_type"), ips,
                f"public {public}" if public else "",
            ) if p]
            self.add_node(
                f"{vm_node_id}_detail", "vm_detail",
                "\n".join(detail),
                parent=vm_node_id,
                data={},
            )

            # A NIC folded into a VM card has no node of its own, so anything
            # pointing at that NIC — a load balancer's backend pool — must
            # point at the card instead.
            node_vnets[vm_node_id] = vnet_node_id
            for nic in nics:
                nic_nodes[nic["id"].lower()] = vm_node_id

    def _add_spanning_vms(
        self,
        spanning_attachments: Dict[str, Dict[str, Any]],
        vms_by_id: Dict[str, Dict[str, Any]],
    ) -> None:
        """Add one node per multi-subnet VM, wired to each of its NICs.

        These sit at the VNet level rather than inside a subnet, which is what
        keeps a four-armed firewall a single object in the diagram instead of
        four unrelated ones.
        """
        for vm_id, entry in spanning_attachments.items():
            vm = vms_by_id.get(vm_id, {})
            vm_name = vm.get("name") or vm_id.split("/")[-1]
            vm_node_id = f"vm_shared_{vm_name}"
            span = len(entry["nics"])

            self.add_node(
                vm_node_id, "vm", vm_name,
                parent=entry["vnet"],
                data={
                    "azure_id": vm.get("id") or vm_id,
                    "vm_size": vm.get("vm_size", ""),
                    "os_type": vm.get("os_type", ""),
                    "resource_group": vm.get("resource_group", ""),
                    "spans_subnets": span,
                },
            )

            detail = [p for p in (
                vm.get("vm_size"), vm.get("os_type"), f"{span} subnets",
            ) if p]
            self.add_node(
                f"{vm_node_id}_detail", "vm_detail",
                "\n".join(detail),
                parent=vm_node_id,
                data={},
            )

            for nic_node_id in entry["nics"]:
                self.add_edge(vm_node_id, nic_node_id, "attached_to", {})

    def _add_load_balancers(
        self,
        balancers: List[Dict[str, Any]],
        subnet_nodes: Dict[str, str],
        nic_nodes: Dict[str, str],
        node_vnets: Dict[str, str],
        vnet_rgs: Dict[str, str],
    ) -> None:
        """Add a node per load balancer, wired to what it balances.

        An internal LB has a frontend in a subnet and is drawn inside it. A
        public one is not part of any subnet — nor of the VNet, whatever its
        backend pool reaches into — so it is parented to the resource group and
        drawn above the VNet, outside its border. An LB whose members are
        absent from this scan has nowhere to sit and is skipped rather than
        left unparented, which would drop it out of the layout.
        """
        for lb in balancers:
            targets: List[str] = []
            home = None
            internal = False

            for frontend in lb.get("frontends", []):
                subnet_id = frontend.get("subnet_id")
                if subnet_id and subnet_id in subnet_nodes:
                    home = subnet_nodes[subnet_id]
                    internal = True
                    break

            for nic_id in lb.get("backend_nic_ids", []):
                node_id = nic_nodes.get(nic_id)
                if node_id and node_id not in targets:
                    targets.append(node_id)

            if home is None:
                # Public: sit in the resource group holding the VNet its
                # backends live in, which draws it above that VNet's box.
                vnet_id = next(
                    (node_vnets[t] for t in targets if t in node_vnets), None
                )
                home = vnet_rgs.get(vnet_id) if vnet_id else None
            if home is None:
                logger.warning(
                    f"Load balancer {lb['name']}: no frontend subnet and no "
                    f"backend NIC in this scan — not drawn"
                )
                continue

            frontend_ip = next(
                (f.get("private_ip") for f in lb.get("frontends", [])
                 if f.get("private_ip")), None
            )
            public_ip = next(
                (f.get("public_ip") for f in lb.get("frontends", [])
                 if f.get("public_ip")), None
            )
            has_public = any(f.get("public") for f in lb.get("frontends", []))

            if frontend_ip:
                caption = frontend_ip
            elif public_ip:
                caption = f"public {public_ip}"
            elif has_public:
                # Allocated but not yet assigned an address.
                caption = "public frontend"
            else:
                caption = "no frontend IP"

            lb_node_id = f"lb_{lb['resource_group']}_{lb['name']}"
            self.add_node(
                lb_node_id, "lb", f"{lb['name']}\n{caption}",
                parent=home,
                data={
                    "azure_id": lb["id"],
                    "sku": lb.get("sku", ""),
                    "internal": internal,
                    "frontend_ip": frontend_ip,
                    "public_ip": public_ip,
                    "rule_count": lb.get("rule_count", 0),
                    "backend_count": len(targets),
                    "resource_group": lb.get("resource_group", ""),
                },
            )
            node_vnets[lb_node_id] = node_vnets.get(home, home)

            # Count it on its subnet, so the caption can mark a subnet holding
            # a load balancer even when the cards aren't drawn.
            host = self.nodes.get(home)
            if host is not None and host.type == "subnet":
                host.data["lb_count"] = host.data.get("lb_count", 0) + 1

            for target in targets:
                self.add_edge(lb_node_id, target, "balances", {})

    def to_cytoscape_format(self) -> Dict[str, List[Dict[str, Any]]]:
        """Export graph in Cytoscape.js format.

        Peering edges are collapsed for display: Azure represents a
        bidirectional peering as two separate resources (A->B and B->A), which
        would otherwise draw two parallel arrows. We render one edge per VNet
        pair, flagged ``bidirectional`` when both directions exist and left as a
        single directional edge when only one does (a one-way peering worth
        highlighting). This is a display-only change — ``to_dict()`` still
        carries every peering so the analyzer can detect one-way peerings.
        """
        elements = []

        # Add nodes
        for node_id, node in self.nodes.items():
            node_data = {
                "id": node_id,
                "label": node.label,
                "type": node.type,
                **node.data,
            }
            if node.parent:
                node_data["parent"] = node.parent
            elements.append({"data": node_data})

        # Group peering edges by unordered VNet pair; pass other edges through.
        peering_groups: Dict[frozenset, List[GraphEdge]] = {}
        for edge in self.edges.values():
            if edge.type == "peered_to":
                key = frozenset((edge.source, edge.target))
                peering_groups.setdefault(key, []).append(edge)
            else:
                elements.append({
                    "data": {
                        "id": edge.id,
                        "source": edge.source,
                        "target": edge.target,
                        "type": edge.type,
                        **edge.data,
                    }
                })

        # Emit one edge per peering pair.
        for edges in peering_groups.values():
            rep = edges[0]
            bidirectional = len(edges) >= 2
            pair_id = "peer_" + "__".join(sorted((rep.source, rep.target)))
            elements.append({
                "data": {
                    "id": pair_id,
                    "source": rep.source,
                    "target": rep.target,
                    "type": "peered_to",
                    "bidirectional": bidirectional,
                    **rep.data,
                }
            })

        return {"elements": elements}

    def to_dict(self) -> Dict[str, Any]:
        """Export graph as dictionary."""
        return {
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "edges": {eid: edge.to_dict() for eid, edge in self.edges.items()},
        }
