> **Note:** This project was generated with the assistance of [Claude](https://claude.ai) by Anthropic.

# Azure Mapper

A web application that visualizes Azure network topology — VNets, subnets, peerings, and resource groups — as an interactive diagram.

## Features

- **Browser-based Azure sign-in** via MSAL.js (multi-tenant, no service principal required)
- **Automatic resource discovery** of VNets, subnets, and peerings for any subscription
- **Interactive topology diagram** using Cytoscape.js with compound nodes:
  - Resource groups as outer containers
  - VNets nested inside with address space labels
  - Subnets nested inside VNets with CIDR labels
  - Peering connections shown as dashed green edges
- **Works across tenants** — sign in with any Microsoft account that has Reader access

## Prerequisites

- Docker and Docker Compose
- An Azure AD app registration (multi-tenant, SPA redirect to `http://localhost:8080`)

## Quick Start

```bash
docker compose build
docker compose up -d
```

Then open http://localhost:8080, sign in with Microsoft, select a subscription, and scan.

## Azure AD App Registration

Create a multi-tenant app registration:

```bash
az ad app create --display-name "Azure Mapper" --sign-in-audience AzureADMultipleOrgs
```

Add the SPA redirect URI (use the Object ID from the output above):

```bash
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/<OBJECT_ID>" \
  --headers "Content-Type=application/json" \
  --body '{"spa":{"redirectUris":["http://localhost:8080"]}}'
```

Update the `clientId` in `app/static/app.js` with your Application (Client) ID.

## Project Structure

```
.
├── Dockerfile
├── docker-compose.yml
├── app/
│   ├── app.py              # Flask backend
│   ├── azure_ingest.py     # Azure SDK resource discovery
│   ├── graph_builder.py    # Topology graph model
│   ├── requirements.txt
│   ├── static/
│   │   └── app.js          # Frontend (MSAL auth + Cytoscape visualization)
│   └── templates/
│       └── index.html
└── samples/
    └── *.tf                # Terraform sample infrastructure for testing
```

## Architecture

The frontend handles authentication via MSAL.js and passes Bearer tokens to the Flask backend on each API call. The backend uses those tokens with the Azure Management SDK to discover resources, then builds a graph model that the frontend renders with Cytoscape.js compound nodes.
