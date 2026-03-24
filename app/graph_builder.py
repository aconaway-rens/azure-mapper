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
        """Build graph from Azure scan data (VNets, subnets, peerings)."""
        vnets = scan_data.get("vnets", {})
        peerings = scan_data.get("peerings", [])

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
                self.add_node(
                    f"subnet_{vnet_name}_{subnet['name']}",
                    "subnet",
                    subnet_label,
                    parent=vnet_id,
                    data={
                        "azure_id": subnet["id"],
                        "address_prefix": addr,
                    },
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

    def to_cytoscape_format(self) -> Dict[str, List[Dict[str, Any]]]:
        """Export graph in Cytoscape.js format."""
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

        # Add edges
        for edge_id, edge in self.edges.items():
            elements.append({
                "data": {
                    "id": edge_id,
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.type,
                    **edge.data,
                }
            })

        return {"elements": elements}

    def to_dict(self) -> Dict[str, Any]:
        """Export graph as dictionary."""
        return {
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "edges": {eid: edge.to_dict() for eid, edge in self.edges.items()},
        }
