"""Azure SDK integration for resource discovery and ingestion.

Authentication is server-side and ambient: DefaultAzureCredential picks up
whatever identity the host already has — a managed identity on an Azure VM, an
`az login` session, or service-principal environment variables — so a fresh
deployment needs no app registration, client ID, or secret baked into the app.
"""

import base64
import binascii
import json
import logging
from typing import Any, Dict, List, Optional

from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.subscription import SubscriptionClient

logger = logging.getLogger(__name__)

ARM_SCOPE = "https://management.azure.com/.default"

# One credential per process. DefaultAzureCredential caches tokens internally
# and refreshes them, so rebuilding it per request would just re-probe every
# credential source on every call.
_credential = None


def get_credential() -> DefaultAzureCredential:
    """Return the process-wide DefaultAzureCredential."""
    global _credential
    if _credential is None:
        logger.info("Initializing DefaultAzureCredential")
        _credential = DefaultAzureCredential()
    return _credential


def _decode_token_claims(token: str) -> Dict[str, Any]:
    """Read the claims out of our own access token, for display only.

    No signature verification — this is used to show the operator which
    identity the app is running as, never to make an authorization decision.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return {}


def describe_identity() -> Dict[str, Any]:
    """Acquire an ARM token and report which identity the app is running as.

    Raises whatever azure-identity raises when no credential source works —
    the caller surfaces that message, since it tells the operator exactly
    which sources were tried.
    """
    token = get_credential().get_token(ARM_SCOPE)
    claims = _decode_token_claims(token.token)
    identity = (
        claims.get("upn")
        or claims.get("unique_name")
        or claims.get("app_displayname")
        or claims.get("appid")
        or claims.get("oid")
    )
    return {
        "authenticated": True,
        "identity": identity,
        "tenant_id": claims.get("tid"),
    }


class AzureResourceIngestor:
    """Handles authentication and resource discovery from Azure."""

    def __init__(self, credential: Optional[Any] = None):
        """Initialize with a credential, defaulting to the ambient one."""
        self.credential = credential or get_credential()

    def get_subscriptions(self) -> List[Dict[str, Any]]:
        """Fetch available subscriptions."""
        sub_client = SubscriptionClient(self.credential)
        subscriptions = []
        for sub in sub_client.subscriptions.list():
            subscriptions.append({
                "id": sub.subscription_id,
                "name": sub.display_name,
                "state": sub.state,
            })
        logger.info(f"Found {len(subscriptions)} subscriptions")
        return subscriptions

    def get_vnets_and_subnets(self, subscription_id: str) -> Dict[str, Any]:
        """Fetch all VNets and subnets for a given subscription."""
        network_client = NetworkManagementClient(self.credential, subscription_id)
        vnets_data = {}

        # List all virtual networks
        for vnet in network_client.virtual_networks.list_all():
            rg_name = vnet.id.split("/")[4]
            vnet_key = vnet.name
            vnets_data[vnet_key] = {
                "id": vnet.id,
                "name": vnet.name,
                "resource_group": rg_name,
                "location": vnet.location,
                "address_prefixes": vnet.address_space.address_prefixes if vnet.address_space else [],
                "tags": vnet.tags or {},
                "subnets": [],
            }

            # Add subnets for this vnet
            if vnet.subnets:
                for subnet in vnet.subnets:
                    subnet_data = {
                        "id": subnet.id,
                        "name": subnet.name,
                        "address_prefix": subnet.address_prefix,
                        "vnet_name": vnet.name,
                    }
                    vnets_data[vnet_key]["subnets"].append(subnet_data)

        logger.info(f"Found {len(vnets_data)} VNets with subnets")
        return vnets_data

    def get_vnet_peerings(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Fetch all VNet peerings for a given subscription."""
        network_client = NetworkManagementClient(self.credential, subscription_id)
        peerings = []

        # List all virtual networks
        for vnet in network_client.virtual_networks.list_all():
            rg_name = vnet.id.split("/")[4]

            # Get peerings for this vnet
            if vnet.name:
                try:
                    vnet_peerings = network_client.virtual_network_peerings.list(
                        rg_name, vnet.name
                    )
                    for peering in vnet_peerings:
                        peering_data = {
                            "id": peering.id,
                            "name": peering.name,
                            "source_vnet": vnet.name,
                            "remote_vnet_id": peering.remote_virtual_network.id if peering.remote_virtual_network else None,
                            "allow_virtual_network_access": peering.allow_virtual_network_access,
                            "allow_forwarded_traffic": peering.allow_forwarded_traffic,
                            "peering_state": peering.peering_state,
                        }
                        peerings.append(peering_data)
                except Exception as e:
                    logger.warning(f"Failed to fetch peerings for {vnet.name}: {e}")
                    continue

        logger.info(f"Found {len(peerings)} VNet peerings")
        return peerings

    def get_subnet_resources(
        self, subscription_id: str, subnet_azure_id: str
    ) -> Dict[str, Any]:
        """Fetch NICs and VMs attached to a specific subnet."""
        network_client = NetworkManagementClient(
            self.credential, subscription_id
        )
        compute_client = ComputeManagementClient(
            self.credential, subscription_id
        )

        nics = []
        vm_ids_seen = set()
        vms = []

        # List all NICs and filter by subnet
        for nic in network_client.network_interfaces.list_all():
            if not nic.ip_configurations:
                continue
            for ip_config in nic.ip_configurations:
                if (ip_config.subnet and
                        ip_config.subnet.id.lower()
                        == subnet_azure_id.lower()):
                    nic_data = {
                        "id": nic.id,
                        "name": nic.name,
                        "private_ip": ip_config.private_ip_address,
                        "vm_id": None,
                        "vm_name": None,
                    }

                    # Resolve VM if attached
                    if nic.virtual_machine and nic.virtual_machine.id:
                        vm_id = nic.virtual_machine.id
                        nic_data["vm_id"] = vm_id
                        vm_name = vm_id.split("/")[-1]
                        nic_data["vm_name"] = vm_name

                        if vm_id.lower() not in vm_ids_seen:
                            vm_ids_seen.add(vm_id.lower())
                            rg = vm_id.split("/")[4]
                            try:
                                vm = compute_client.virtual_machines.get(
                                    rg, vm_name
                                )
                                vms.append({
                                    "id": vm.id,
                                    "name": vm.name,
                                    "vm_size": vm.hardware_profile.vm_size
                                    if vm.hardware_profile else "",
                                    "os_type": (
                                        vm.storage_profile.os_disk.os_type
                                        if vm.storage_profile
                                        and vm.storage_profile.os_disk
                                        else ""
                                    ),
                                })
                            except Exception as e:
                                logger.warning(
                                    f"Could not fetch VM {vm_name}: {e}"
                                )
                                vms.append({
                                    "id": vm_id,
                                    "name": vm_name,
                                    "vm_size": "",
                                    "os_type": "",
                                })

                    nics.append(nic_data)
                    break  # matched this NIC, move on

        logger.info(
            f"Subnet {subnet_azure_id}: "
            f"{len(nics)} NICs, {len(vms)} VMs"
        )
        return {"nics": nics, "vms": vms}

    @staticmethod
    def _os_type_name(vm: Any) -> str:
        """The VM's OS as a plain word.

        The SDK hands back an enum whose str() is ``OperatingSystemTypes.LINUX``
        — accurate, and not what anyone wants printed on a diagram.
        """
        profile = getattr(vm, "storage_profile", None)
        os_disk = getattr(profile, "os_disk", None) if profile else None
        os_type = getattr(os_disk, "os_type", None) if os_disk else None
        if not os_type:
            return ""
        name = getattr(os_type, "value", None) or str(os_type).split(".")[-1]
        return name.capitalize()

    def get_nics_and_vms(self, subscription_id: str) -> Dict[str, Any]:
        """Fetch every NIC in the subscription, resolved to its VM and subnet.

        This is the subscription-wide counterpart to ``get_subnet_resources``.
        Two paginated list calls cover the whole subscription, which is far
        cheaper than the per-subnet path: that one re-lists every NIC in the
        subscription on each drill-down and then issues a separate GET per VM.

        NICs are keyed by the subnet they attach to (lowercased, since ARM IDs
        vary in case), so the graph builder can hang them off the right subnet.
        """
        network_client = NetworkManagementClient(
            self.credential, subscription_id
        )
        compute_client = ComputeManagementClient(
            self.credential, subscription_id
        )

        # One pass over the VMs, so NIC -> VM resolution is a dict lookup
        # rather than a GET each.
        vms: Dict[str, Dict[str, Any]] = {}
        for vm in compute_client.virtual_machines.list_all():
            vms[vm.id.lower()] = {
                "id": vm.id,
                "name": vm.name,
                "resource_group": vm.id.split("/")[4],
                "vm_size": (
                    vm.hardware_profile.vm_size if vm.hardware_profile else ""
                ),
                "os_type": self._os_type_name(vm),
            }

        nics: List[Dict[str, Any]] = []
        for nic in network_client.network_interfaces.list_all():
            if not nic.ip_configurations:
                continue

            vm_id = (
                nic.virtual_machine.id if nic.virtual_machine else None
            )
            vm = vms.get(vm_id.lower()) if vm_id else None

            # A NIC can hold several ipconfigs, each potentially on its own
            # subnet. Emit one record per subnet-bearing ipconfig so the NIC
            # shows up under every subnet it actually touches.
            for ip_config in nic.ip_configurations:
                if not ip_config.subnet or not ip_config.subnet.id:
                    continue
                nics.append({
                    "id": nic.id,
                    "name": nic.name,
                    "subnet_id": ip_config.subnet.id.lower(),
                    "private_ip": ip_config.private_ip_address,
                    "vm_id": vm_id,
                    "vm_name": vm["name"] if vm else None,
                })

        logger.info(
            f"Found {len(nics)} NIC attachments across {len(vms)} VMs"
        )
        return {"nics": nics, "vms": vms}

    def get_load_balancers(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Fetch every load balancer, with its frontends and backend NICs.

        A backend pool member is an *ipConfiguration* ID; the NIC is its parent
        resource, so the ID is trimmed back to the NIC before it can be matched
        against anything else in the scan.
        """
        network_client = NetworkManagementClient(
            self.credential, subscription_id
        )
        balancers = []

        for lb in network_client.load_balancers.list_all():
            frontends = []
            for frontend in lb.frontend_ip_configurations or []:
                subnet = getattr(frontend, "subnet", None)
                frontends.append({
                    "name": frontend.name,
                    "private_ip": frontend.private_ip_address,
                    "subnet_id": subnet.id.lower() if subnet and subnet.id else None,
                    "public": bool(getattr(frontend, "public_ip_address", None)),
                })

            backend_nic_ids = set()
            for pool in lb.backend_address_pools or []:
                for config in getattr(pool, "backend_ip_configurations", None) or []:
                    if config and config.id:
                        backend_nic_ids.add(
                            config.id.split("/ipConfigurations/")[0].lower()
                        )

            balancers.append({
                "id": lb.id,
                "name": lb.name,
                "resource_group": lb.id.split("/")[4],
                "location": lb.location,
                "sku": lb.sku.name if getattr(lb, "sku", None) else "",
                "frontends": frontends,
                "backend_nic_ids": sorted(backend_nic_ids),
                "rule_count": len(lb.load_balancing_rules or []),
            })

        logger.info(f"Found {len(balancers)} load balancers")
        return balancers

    def scan_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Perform a full scan: VNets, subnets, peerings, NICs, VMs, and LBs."""
        compute = self.get_nics_and_vms(subscription_id)
        return {
            "subscription_id": subscription_id,
            "vnets": self.get_vnets_and_subnets(subscription_id),
            "peerings": self.get_vnet_peerings(subscription_id),
            "nics": compute["nics"],
            "vms": compute["vms"],
            "load_balancers": self.get_load_balancers(subscription_id),
        }
