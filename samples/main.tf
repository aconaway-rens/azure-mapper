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
    CreatedBy = var.created_by_name
    Date      = formatdate("YYYY-MM-DD", timestamp())
  }
}

# NOTE: This sample is network-only (no VMs/NICs) to keep lab cost minimal.
# VM/NIC resources will be re-added when the analyzer grows to inspect them.

resource "azurerm_resource_group" "sample_rg" {
  name     = "SAMPLE-RG"
  location = "eastus"
  tags     = local.tags
}

resource "azurerm_virtual_network" "sample_vnet01" {
  name                = "SAMPLE-VNET01"
  address_space       = ["172.19.100.0/22"]
  location            = azurerm_resource_group.sample_rg.location
  resource_group_name = azurerm_resource_group.sample_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "sample_vnet01_sub01" {
  name                 = "SAMPLE-VNET01-SUB01"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet01.name
  address_prefixes     = ["172.19.101.0/24"]
}

resource "azurerm_subnet" "sample_vnet01_sub02" {
  name                 = "SAMPLE-VNET01-SUB02"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet01.name
  address_prefixes     = ["172.19.102.0/24"]
}

resource "azurerm_subnet" "sample_vnet01_sub03" {
  name                 = "SAMPLE-VNET01-SUB03"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet01.name
  address_prefixes     = ["172.19.100.0/24"]
}

resource "azurerm_subnet" "sample_vnet01_sub04" {
  name                 = "SAMPLE-VNET01-SUB04"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet01.name
  address_prefixes     = ["172.19.103.0/24"]
}

resource "azurerm_virtual_network" "sample_vnet02" {
  name                = "SAMPLE-VNET02"
  address_space       = ["172.19.200.0/22"]
  location            = azurerm_resource_group.sample_rg.location
  resource_group_name = azurerm_resource_group.sample_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "sample_vnet02_sub01" {
  name                 = "SAMPLE-VNET02-SUB01"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet02.name
  address_prefixes     = ["172.19.201.0/24"]
}

resource "azurerm_subnet" "sample_vnet02_sub02" {
  name                 = "SAMPLE-VNET02-SUB02"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet02.name
  address_prefixes     = ["172.19.200.0/24"]
}

resource "azurerm_subnet" "sample_vnet02_sub03" {
  name                 = "SAMPLE-VNET02-SUB03"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet02.name
  address_prefixes     = ["172.19.202.0/24"]
}

resource "azurerm_virtual_network_peering" "vnet01_to_vnet02" {
  name                      = "SAMPLE-VNET01-to-SAMPLE-VNET02"
  resource_group_name       = azurerm_resource_group.sample_rg.name
  virtual_network_name      = azurerm_virtual_network.sample_vnet01.name
  remote_virtual_network_id = azurerm_virtual_network.sample_vnet02.id

  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

resource "azurerm_virtual_network_peering" "vnet02_to_vnet01" {
  name                      = "SAMPLE-VNET02-to-SAMPLE-VNET01"
  resource_group_name       = azurerm_resource_group.sample_rg.name
  virtual_network_name      = azurerm_virtual_network.sample_vnet02.name
  remote_virtual_network_id = azurerm_virtual_network.sample_vnet01.id

  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

# --- SAMPLE2-RG resources ---

resource "azurerm_resource_group" "sample2_rg" {
  name     = "SAMPLE2-RG"
  location = "eastus"
  tags     = local.tags
}

resource "azurerm_virtual_network" "sample_vnet91" {
  name                = "SAMPLE-VNET91"
  address_space       = ["172.31.199.0/24"]
  location            = azurerm_resource_group.sample2_rg.location
  resource_group_name = azurerm_resource_group.sample2_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "sample_vnet91_sub01" {
  name                 = "SAMPLE-VNET91-SUB01"
  resource_group_name  = azurerm_resource_group.sample2_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet91.name
  address_prefixes     = ["172.31.199.0/25"]
}

resource "azurerm_subnet" "sample_vnet91_sub02" {
  name                 = "SAMPLE-VNET91-SUB02"
  resource_group_name  = azurerm_resource_group.sample2_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet91.name
  address_prefixes     = ["172.31.199.128/25"]
}

resource "azurerm_virtual_network_peering" "vnet91_to_vnet01" {
  name                      = "SAMPLE-VNET91-to-SAMPLE-VNET01"
  resource_group_name       = azurerm_resource_group.sample2_rg.name
  virtual_network_name      = azurerm_virtual_network.sample_vnet91.name
  remote_virtual_network_id = azurerm_virtual_network.sample_vnet01.id

  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

resource "azurerm_virtual_network_peering" "vnet91_to_vnet02" {
  name                      = "SAMPLE-VNET91-to-SAMPLE-VNET02"
  resource_group_name       = azurerm_resource_group.sample2_rg.name
  virtual_network_name      = azurerm_virtual_network.sample_vnet91.name
  remote_virtual_network_id = azurerm_virtual_network.sample_vnet02.id

  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

resource "azurerm_virtual_network_peering" "vnet02_to_vnet91" {
  name                      = "SAMPLE-VNET02-to-SAMPLE-VNET91"
  resource_group_name       = azurerm_resource_group.sample_rg.name
  virtual_network_name      = azurerm_virtual_network.sample_vnet02.name
  remote_virtual_network_id = azurerm_virtual_network.sample_vnet91.id

  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

resource "azurerm_virtual_network_peering" "vnet01_to_vnet91" {
  name                      = "SAMPLE-VNET01-to-SAMPLE-VNET91"
  resource_group_name       = azurerm_resource_group.sample_rg.name
  virtual_network_name      = azurerm_virtual_network.sample_vnet01.name
  remote_virtual_network_id = azurerm_virtual_network.sample_vnet91.id

  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

# --- Overlapping pair: SAMPLE-VNET03 and SAMPLE-VNET04 both use 10.50.0.0/16.
#     They are intentionally NOT peered (Azure rejects peerings between
#     overlapping VNets), so they deploy cleanly while overlapping. ---

resource "azurerm_virtual_network" "sample_vnet03" {
  name                = "SAMPLE-VNET03"
  address_space       = ["10.50.0.0/16"]
  location            = azurerm_resource_group.sample_rg.location
  resource_group_name = azurerm_resource_group.sample_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "sample_vnet03_sub01" {
  name                 = "SAMPLE-VNET03-SUB01"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet03.name
  address_prefixes     = ["10.50.1.0/24"]
}

resource "azurerm_subnet" "sample_vnet03_sub02" {
  name                 = "SAMPLE-VNET03-SUB02"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet03.name
  address_prefixes     = ["10.50.2.0/24"]
}

resource "azurerm_virtual_network" "sample_vnet04" {
  name                = "SAMPLE-VNET04"
  address_space       = ["10.50.0.0/16"] # overlaps SAMPLE-VNET03
  location            = azurerm_resource_group.sample_rg.location
  resource_group_name = azurerm_resource_group.sample_rg.name
  tags                = local.tags
}

resource "azurerm_subnet" "sample_vnet04_sub01" {
  name                 = "SAMPLE-VNET04-SUB01"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet04.name
  address_prefixes     = ["10.50.1.0/24"]
}

resource "azurerm_subnet" "sample_vnet04_sub02" {
  name                 = "SAMPLE-VNET04-SUB02"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet04.name
  address_prefixes     = ["10.50.2.0/24"]
}
