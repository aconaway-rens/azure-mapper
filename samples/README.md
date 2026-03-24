# Azure Mapper Sample Deployment

This directory contains a sample OpenTofu/Terraform configuration that deploys a simple Azure infrastructure to use as a test case for the Azure Mapper application.

## Configuration Overview

The sample deploys:

- **Resource Group**: `SAMPLE-RG` in the `eastus` region
- **Virtual Network 1 (SAMPLE-VNET01)**: `172.19.100.0/22`
  - Subnet 1 (SAMPLE-VNET01-SUB01): `172.19.101.0/24`
  - Subnet 2 (SAMPLE-VNET-SUB02): `172.19.102.0/24`
- **Virtual Network 2 (SAMPLE-VNET02)**: `172.19.200.0/22`
  - Subnet 1 (SAMPLE-VNET02-SUB01): `172.19.201.0/24`
- **VNet Peering**: Bidirectional peering between SAMPLE-VNET01 and SAMPLE-VNET02

## Prerequisites

- OpenTofu or Terraform >= 1.0
- Azure CLI installed and authenticated (`az login`)
- Appropriate Azure subscription permissions to create resources

## Deployment Steps

### 1. Initialize OpenTofu

```bash
cd samples
opentofu init
```

### 2. Review the Plan

```bash
opentofu plan
```

This will show you all the resources that will be created.

### 3. Apply the Configuration

```bash
opentofu apply
```

When prompted, type `yes` to confirm the deployment.

### 4. Retrieve Outputs

After deployment, view the resource IDs and details:

```bash
opentofu output
```

## Configuration Customization

To customize the deployment, you can override any variables at deployment time:

```bash
opentofu apply \
  -var="location=westus" \
  -var="resource_group_name=MY-CUSTOM-RG"
```

Alternatively, create a `terraform.tfvars` file:

```hcl
location              = "westus"
resource_group_name   = "MY-CUSTOM-RG"
vnet01_address_space  = ["10.0.0.0/16"]
```

See `variables.tf` for all available variables and defaults.

## Testing with Azure Mapper

Once deployed, use Azure Mapper to:

1. Connect to your Azure subscription
2. Select the subscription where you deployed this sample
3. Run a resource scan
4. The logical diagram should show:
   - SAMPLE-RG resource group
   - Two virtual networks with their subnets
   - The bidirectional peering connection between the VNets

## Cleanup

To remove all deployed resources:

```bash
opentofu destroy
```

When prompted, type `yes` to confirm.

## Files

- `main.tf` - Main infrastructure configuration
- `variables.tf` - Input variable definitions with defaults
- `outputs.tf` - Output value definitions
- `README.md` - This file

## Notes

- The sample uses hardcoded names and address spaces; modify `variables.tf` to parameterize for your needs
- All resources are tagged as part of the SAMPLE-RG resource group for easy identification and cleanup
- The peering allows virtual network access and forwarded traffic by default
