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

    def scan_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Perform a full scan of a subscription: VNets, subnets, and peerings."""
        return {
            "subscription_id": subscription_id,
            "vnets": self.get_vnets_and_subnets(subscription_id),
            "peerings": self.get_vnet_peerings(subscription_id),
        }
