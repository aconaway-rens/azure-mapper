variable "location" {
  description = "Azure region for all resources"
  type        = string
  default     = "eastus"
}

variable "resource_group_name" {
  description = "Name of the resource group"
  type        = string
  default     = "SAMPLE-RG"
}

variable "vnet01_name" {
  description = "Name of the first virtual network"
  type        = string
  default     = "SAMPLE-VNET01"
}

variable "vnet01_address_space" {
  description = "Address space for the first virtual network"
  type        = list(string)
  default     = ["172.19.100.0/22"]
}

variable "vnet01_sub01_name" {
  description = "Name of the first subnet in VNET01"
  type        = string
  default     = "SAMPLE-VNET01-SUB01"
}

variable "vnet01_sub01_address" {
  description = "Address prefix for the first subnet in VNET01"
  type        = list(string)
  default     = ["172.19.101.0/24"]
}

variable "vnet01_sub02_name" {
  description = "Name of the second subnet in VNET01"
  type        = string
  default     = "SAMPLE-VNET-SUB02"
}

variable "vnet01_sub02_address" {
  description = "Address prefix for the second subnet in VNET01"
  type        = list(string)
  default     = ["172.19.102.0/24"]
}

variable "vnet02_name" {
  description = "Name of the second virtual network"
  type        = string
  default     = "SAMPLE-VNET02"
}

variable "vnet02_address_space" {
  description = "Address space for the second virtual network"
  type        = list(string)
  default     = ["172.19.200.0/22"]
}

variable "vnet02_sub01_name" {
  description = "Name of the first subnet in VNET02"
  type        = string
  default     = "SAMPLE-VNET02-SUB01"
}

variable "vnet02_sub01_address" {
  description = "Address prefix for the first subnet in VNET02"
  type        = list(string)
  default     = ["172.19.201.0/24"]
}

variable "resource_group2_name" {
  description = "Name of the second resource group"
  type        = string
  default     = "SAMPLE2-RG"
}

variable "vnet91_name" {
  description = "Name of VNET91"
  type        = string
  default     = "SAMPLE-VNET91"
}

variable "vnet91_address_space" {
  description = "Address space for VNET91"
  type        = list(string)
  default     = ["172.31.199.0/24"]
}

variable "vnet91_sub01_name" {
  description = "Name of the first subnet in VNET91"
  type        = string
  default     = "SAMPLE-VNET91-SUB01"
}

variable "vnet91_sub01_address" {
  description = "Address prefix for the first subnet in VNET91"
  type        = list(string)
  default     = ["172.31.199.0/25"]
}

variable "created_by_name" {
  description = "Name of the person/entity creating the resources"
  type        = string
  default     = "Aaron Conaway"
}
