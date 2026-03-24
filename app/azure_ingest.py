"""Azure SDK integration for resource discovery and ingestion."""

from azure.core.credentials import AccessToken
from azure.mgmt.subscription import SubscriptionClient
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.network import NetworkManagementClient
from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class AccessTokenCredential:
    """Simple credential class that uses a provided access token."""

    def __init__(self, token: str):
        self.token = token

    def get_token(self, *scopes, **kwargs):
        """Return the access token."""
        return AccessToken(self.token, expires_on=9999999999)


class AzureResourceIngestor:
    """Handles authentication and resource discovery from Azure."""

    def __init__(self, access_token: str):
        """Initialize with a Bearer token (from MSAL or CLI)."""
        self.credential = AccessTokenCredential(access_token)

    def get_subscriptions(self) -> List[Dict[str, Any]]:
        """Fetch available subscriptions."""
        try:
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
        except Exception as e:
            logger.error(f"Failed to fetch subscriptions: {e}")
            return []

    def get_vnets_and_subnets(self, subscription_id: str) -> Dict[str, Any]:
        """Fetch all VNets and subnets for a given subscription."""
        try:
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

        except Exception as e:
            logger.error(f"Failed to fetch VNets and subnets: {e}")
            return {}

    def get_vnet_peerings(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Fetch all VNet peerings for a given subscription."""
        try:
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

        except Exception as e:
            logger.error(f"Failed to fetch VNet peerings: {e}")
            return []

    def scan_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Perform a full scan of a subscription: VNets, subnets, and peerings."""
        return {
            "subscription_id": subscription_id,
            "vnets": self.get_vnets_and_subnets(subscription_id),
            "peerings": self.get_vnet_peerings(subscription_id),
        }
