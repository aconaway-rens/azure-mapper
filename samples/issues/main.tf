terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.0.0"
    }
  }
}

provider "azurerm" {
  features {}
}

locals {
  tags = {
    CreatedBy = "Aaron Conaway"
    Date      = "2026-06-19"
    Purpose   = "intentionally-flawed-topology"
  }
}

# =============================================================================
# DELIBERATELY-FLAWED LAB  (network-only)
#
# This topology plants classic Azure networking mistakes for the AI Network
# Review to catch. Every issue is detectable from topology alone (VNets,
# subnets, peerings) — no NSGs/route tables required. All resources still
# deploy cleanly: overlapping address spaces are only used between VNets that
# are NOT peered (Azure rejects peerings between overlapping VNets).
#
# See README.md in this directory for the full list of planted issues.
# =============================================================================

# --- Resource groups (note the inconsistent naming: UPPER-DASH vs lower_snake) ---

resource "azurerm_resource_group" "hub_rg" {
  name     = "LAB-HUB-RG"
  location = "eastus"
  tags     = local.tags
}

resource "azurerm_resource_group" "spokes_rg" {
  name     = "lab_spokes_rg"
  location = "eastus"
  tags     = local.tags
}

# --- Hub ---

resource "azurerm_virtual_network" "hub" {
  name                = "hub-vnet"
  address_space       = ["10.10.0.0/16"]
  location            = azurerm_resource_group.hub_rg.location
  resource_group_name = azurerm_resource_group.hub_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "hub_sub01" {
  name                 = "hub-sub-01"
  resource_group_name  = azurerm_resource_group.hub_rg.name
  virtual_network_name = azurerm_virtual_network.hub.name
  address_prefixes     = ["10.10.0.0/24"]
}

# --- Spoke A (inconsistent name/casing: "SpokeA_VNET") ---

resource "azurerm_virtual_network" "spoke_a" {
  name                = "SpokeA_VNET"
  address_space       = ["10.20.0.0/16"]
  location            = azurerm_resource_group.spokes_rg.location
  resource_group_name = azurerm_resource_group.spokes_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "spoke_a_sub01" {
  name                 = "spokea-sub01"
  resource_group_name  = azurerm_resource_group.spokes_rg.name
  virtual_network_name = azurerm_virtual_network.spoke_a.name
  address_prefixes     = ["10.20.0.0/24"]
}

# --- Spoke B ---

resource "azurerm_virtual_network" "spoke_b" {
  name                = "vnet-spoke-b"
  address_space       = ["10.30.0.0/16"]
  location            = azurerm_resource_group.spokes_rg.location
  resource_group_name = azurerm_resource_group.spokes_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "spoke_b_sub01" {
  name                 = "spokeb-sub01"
  resource_group_name  = azurerm_resource_group.spokes_rg.name
  virtual_network_name = azurerm_virtual_network.spoke_b.name
  address_prefixes     = ["10.30.0.0/24"]
}

# --- Spoke C (will be peered ONE-WAY from the hub only) ---

resource "azurerm_virtual_network" "spoke_c" {
  name                = "spoke-c-vnet"
  address_space       = ["10.40.0.0/16"]
  location            = azurerm_resource_group.spokes_rg.location
  resource_group_name = azurerm_resource_group.spokes_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "spoke_c_sub01" {
  name                 = "spokec-sub01"
  resource_group_name  = azurerm_resource_group.spokes_rg.name
  virtual_network_name = azurerm_virtual_network.spoke_c.name
  address_prefixes     = ["10.40.0.0/24"]
}

# --- Isolated VNet: OVERLAPS Spoke A (10.20.0.0/16) and has NO peerings ---

resource "azurerm_virtual_network" "isolated" {
  name                = "isolated-vnet"
  address_space       = ["10.20.0.0/24"] # overlaps SpokeA_VNET (10.20.0.0/16)
  location            = azurerm_resource_group.spokes_rg.location
  resource_group_name = azurerm_resource_group.spokes_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "isolated_sub01" {
  name                 = "isolated-sub01"
  resource_group_name  = azurerm_resource_group.spokes_rg.name
  virtual_network_name = azurerm_virtual_network.isolated.name
  address_prefixes     = ["10.20.0.0/25"]
}

# --- Empty VNet: address space but NO subnets, and NO peerings ---

resource "azurerm_virtual_network" "empty" {
  name                = "empty-vnet"
  address_space       = ["10.99.0.0/16"]
  location            = azurerm_resource_group.spokes_rg.location
  resource_group_name = azurerm_resource_group.spokes_rg.name
  tags                = local.tags
}

# --- Oversized VNet: a /12 holding a single /29 subnet (wasteful sizing) ---

resource "azurerm_virtual_network" "oversized" {
  name                = "oversized-vnet"
  address_space       = ["172.16.0.0/12"]
  location            = azurerm_resource_group.hub_rg.location
  resource_group_name = azurerm_resource_group.hub_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "oversized_sub01" {
  name                 = "oversized-sub01"
  resource_group_name  = azurerm_resource_group.hub_rg.name
  virtual_network_name = azurerm_virtual_network.oversized.name
  address_prefixes     = ["172.16.0.0/29"]
}

# =============================================================================
# Peerings
#
# Healthy (bidirectional): hub<->spoke_a, hub<->spoke_b, hub<->oversized
# Broken (one-way):        hub -> spoke_c only (no return) => "Initiated" state
# Transitive trap:         spoke_a and spoke_b are NOT peered to each other,
#                          though both reach the hub (Azure peering is not
#                          transitive, so A cannot reach B via the hub).
# =============================================================================

# hub <-> spoke_a (healthy)
resource "azurerm_virtual_network_peering" "hub_to_a" {
  name                         = "hub-to-spokeA"
  resource_group_name          = azurerm_resource_group.hub_rg.name
  virtual_network_name         = azurerm_virtual_network.hub.name
  remote_virtual_network_id    = azurerm_virtual_network.spoke_a.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

resource "azurerm_virtual_network_peering" "a_to_hub" {
  name                         = "spokeA-to-hub"
  resource_group_name          = azurerm_resource_group.spokes_rg.name
  virtual_network_name         = azurerm_virtual_network.spoke_a.name
  remote_virtual_network_id    = azurerm_virtual_network.hub.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

# hub <-> spoke_b (healthy)
resource "azurerm_virtual_network_peering" "hub_to_b" {
  name                         = "hub-to-spokeB"
  resource_group_name          = azurerm_resource_group.hub_rg.name
  virtual_network_name         = azurerm_virtual_network.hub.name
  remote_virtual_network_id    = azurerm_virtual_network.spoke_b.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

resource "azurerm_virtual_network_peering" "b_to_hub" {
  name                         = "spokeB-to-hub"
  resource_group_name          = azurerm_resource_group.spokes_rg.name
  virtual_network_name         = azurerm_virtual_network.spoke_b.name
  remote_virtual_network_id    = azurerm_virtual_network.hub.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

# hub -> spoke_c ONLY (the return peering is intentionally missing)
resource "azurerm_virtual_network_peering" "hub_to_c" {
  name                         = "hub-to-spokeC"
  resource_group_name          = azurerm_resource_group.hub_rg.name
  virtual_network_name         = azurerm_virtual_network.hub.name
  remote_virtual_network_id    = azurerm_virtual_network.spoke_c.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

# hub <-> oversized (healthy; the issue with this one is its sizing, not peering)
resource "azurerm_virtual_network_peering" "hub_to_oversized" {
  name                         = "hub-to-oversized"
  resource_group_name          = azurerm_resource_group.hub_rg.name
  virtual_network_name         = azurerm_virtual_network.hub.name
  remote_virtual_network_id    = azurerm_virtual_network.oversized.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

resource "azurerm_virtual_network_peering" "oversized_to_hub" {
  name                         = "oversized-to-hub"
  resource_group_name          = azurerm_resource_group.hub_rg.name
  virtual_network_name         = azurerm_virtual_network.oversized.name
  remote_virtual_network_id    = azurerm_virtual_network.hub.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}
