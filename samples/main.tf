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
  name                 = "SAMPLE-VNET-SUB02"
  resource_group_name  = azurerm_resource_group.sample_rg.name
  virtual_network_name = azurerm_virtual_network.sample_vnet01.name
  address_prefixes     = ["172.19.102.0/24"]
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

# --- VM in SAMPLE-VNET02-SUB01 ---

resource "azurerm_network_interface" "vm_vnet02_nic" {
  name                = "sample-vm-vnet02-nic"
  location            = azurerm_resource_group.sample_rg.location
  resource_group_name = azurerm_resource_group.sample_rg.name
  tags                = local.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.sample_vnet02_sub01.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_linux_virtual_machine" "vm_vnet02" {
  name                            = "sample-vm-vnet02"
  resource_group_name             = azurerm_resource_group.sample_rg.name
  location                        = azurerm_resource_group.sample_rg.location
  size                            = "Standard_B1ls"
  admin_username                  = "azroot"
  admin_password                  = "S@mple-P4ssw0rd!"
  disable_password_authentication = false
  tags                            = local.tags

  network_interface_ids = [
    azurerm_network_interface.vm_vnet02_nic.id,
  ]

  os_disk {
    caching              = "None"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server-gen1"
    version   = "latest"
  }
}

# --- Second VM in SAMPLE-VNET02-SUB01 ---

resource "azurerm_network_interface" "vm_vnet02b_nic" {
  name                = "sample-vm-vnet02b-nic"
  location            = azurerm_resource_group.sample_rg.location
  resource_group_name = azurerm_resource_group.sample_rg.name
  tags                = local.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.sample_vnet02_sub01.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_linux_virtual_machine" "vm_vnet02b" {
  name                            = "sample-vm-vnet02b"
  resource_group_name             = azurerm_resource_group.sample_rg.name
  location                        = azurerm_resource_group.sample_rg.location
  size                            = "Standard_B1ls"
  admin_username                  = "azroot"
  admin_password                  = "S@mple-P4ssw0rd!"
  disable_password_authentication = false
  tags                            = local.tags

  network_interface_ids = [
    azurerm_network_interface.vm_vnet02b_nic.id,
  ]

  os_disk {
    caching              = "None"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server-gen1"
    version   = "latest"
  }
}

# --- VM in SAMPLE-VNET91-SUB01 ---

resource "azurerm_network_interface" "vm_vnet91_nic" {
  name                = "sample-vm-vnet91-nic"
  location            = azurerm_resource_group.sample2_rg.location
  resource_group_name = azurerm_resource_group.sample2_rg.name
  tags                = local.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.sample_vnet91_sub01.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_linux_virtual_machine" "vm_vnet91" {
  name                            = "sample-vm-vnet91"
  resource_group_name             = azurerm_resource_group.sample2_rg.name
  location                        = azurerm_resource_group.sample2_rg.location
  size                            = "Standard_B1ls"
  admin_username                  = "azroot"
  admin_password                  = "S@mple-P4ssw0rd!"
  disable_password_authentication = false
  tags                            = local.tags

  network_interface_ids = [
    azurerm_network_interface.vm_vnet91_nic.id,
  ]

  os_disk {
    caching              = "None"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server-gen1"
    version   = "latest"
  }
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
