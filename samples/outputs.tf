output "resource_group_id" {
  description = "Resource Group ID"
  value       = azurerm_resource_group.sample_rg.id
}

output "resource_group_name" {
  description = "Resource Group name"
  value       = azurerm_resource_group.sample_rg.name
}

output "vnet01_id" {
  description = "SAMPLE-VNET01 ID"
  value       = azurerm_virtual_network.sample_vnet01.id
}

output "vnet01_name" {
  description = "SAMPLE-VNET01 name"
  value       = azurerm_virtual_network.sample_vnet01.name
}

output "vnet01_address_space" {
  description = "SAMPLE-VNET01 address space"
  value       = azurerm_virtual_network.sample_vnet01.address_space
}

output "vnet01_sub01_id" {
  description = "SAMPLE-VNET01-SUB01 subnet ID"
  value       = azurerm_subnet.sample_vnet01_sub01.id
}

output "vnet01_sub02_id" {
  description = "SAMPLE-VNET-SUB02 subnet ID"
  value       = azurerm_subnet.sample_vnet01_sub02.id
}

output "vnet02_id" {
  description = "SAMPLE-VNET02 ID"
  value       = azurerm_virtual_network.sample_vnet02.id
}

output "vnet02_name" {
  description = "SAMPLE-VNET02 name"
  value       = azurerm_virtual_network.sample_vnet02.name
}

output "vnet02_address_space" {
  description = "SAMPLE-VNET02 address space"
  value       = azurerm_virtual_network.sample_vnet02.address_space
}

output "vnet02_sub01_id" {
  description = "SAMPLE-VNET02-SUB01 subnet ID"
  value       = azurerm_subnet.sample_vnet02_sub01.id
}

output "peering_vnet01_to_vnet02_id" {
  description = "Peering from SAMPLE-VNET01 to SAMPLE-VNET02 ID"
  value       = azurerm_virtual_network_peering.vnet01_to_vnet02.id
}

output "peering_vnet02_to_vnet01_id" {
  description = "Peering from SAMPLE-VNET02 to SAMPLE-VNET01 ID"
  value       = azurerm_virtual_network_peering.vnet02_to_vnet01.id
}

output "resource_group2_id" {
  description = "SAMPLE2-RG Resource Group ID"
  value       = azurerm_resource_group.sample2_rg.id
}

output "resource_group2_name" {
  description = "SAMPLE2-RG Resource Group name"
  value       = azurerm_resource_group.sample2_rg.name
}

output "vnet91_id" {
  description = "SAMPLE-VNET91 ID"
  value       = azurerm_virtual_network.sample_vnet91.id
}

output "vnet91_name" {
  description = "SAMPLE-VNET91 name"
  value       = azurerm_virtual_network.sample_vnet91.name
}

output "vnet91_address_space" {
  description = "SAMPLE-VNET91 address space"
  value       = azurerm_virtual_network.sample_vnet91.address_space
}

output "vnet91_sub01_id" {
  description = "SAMPLE-VNET91-SUB01 subnet ID"
  value       = azurerm_subnet.sample_vnet91_sub01.id
}

output "peering_vnet91_to_vnet01_id" {
  description = "Peering from SAMPLE-VNET91 to SAMPLE-VNET01 ID"
  value       = azurerm_virtual_network_peering.vnet91_to_vnet01.id
}

output "peering_vnet01_to_vnet91_id" {
  description = "Peering from SAMPLE-VNET01 to SAMPLE-VNET91 ID"
  value       = azurerm_virtual_network_peering.vnet01_to_vnet91.id
}

output "peering_vnet91_to_vnet02_id" {
  description = "Peering from SAMPLE-VNET91 to SAMPLE-VNET02 ID"
  value       = azurerm_virtual_network_peering.vnet91_to_vnet02.id
}

output "peering_vnet02_to_vnet91_id" {
  description = "Peering from SAMPLE-VNET02 to SAMPLE-VNET91 ID"
  value       = azurerm_virtual_network_peering.vnet02_to_vnet91.id
}
