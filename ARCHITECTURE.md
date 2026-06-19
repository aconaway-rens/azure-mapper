# Azure Mapper — Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              BROWSER                                    │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                        index.html                                │  │
│  │  ┌─────────────────┐   ┌──────────────────────────────────────┐ │  │
│  │  │   MSAL.js v2.32 │   │              app.js                  │ │  │
│  │  │  (CDN: msauth)  │   │                                      │ │  │
│  │  │                 │   │  ┌────────────────────────────────┐  │ │  │
│  │  │  signIn()       │──▶│  │  authFetch()                   │  │ │  │
│  │  │  acquireToken() │   │  │  Bearer token on every request │  │ │  │
│  │  │  Multi-tenant   │   │  └──────────────┬─────────────────┘  │ │  │
│  │  │  Azure AD auth  │   │                 │                     │ │  │
│  │  └─────────────────┘   │  ┌──────────────▼─────────────────┐  │ │  │
│  │                         │  │        Cytoscape.js v3.28      │  │ │  │
│  │                         │  │        (CDN: cdnjs)            │  │ │  │
│  │                         │  │                                │  │ │  │
│  │                         │  │  Compound nodes:               │  │ │  │
│  │                         │  │   RG → VNet → Subnet           │  │ │  │
│  │                         │  │  Peering edges (VNet↔VNet)     │  │ │  │
│  │                         │  │  Drill-down: Subnet → VM cards │  │ │  │
│  │                         │  │  Custom manual layout engine   │  │ │  │
│  │                         │  └────────────────────────────────┘  │ │  │
│  │                         └──────────────────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │  HTTP/REST  (Bearer token in header)
                                 │  GET /api/subscriptions
                                 │  POST /api/scan
                                 │  GET /api/graph
                                 │  POST /api/subnet/resources
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         FLASK BACKEND  (Gunicorn)                       │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                          app.py                                  │  │
│  │  Flask + Flask-CORS                                              │  │
│  │                                                                  │  │
│  │  get_token_from_request() ──▶ Authorization: Bearer <token>      │  │
│  │  get_ingestor()           ──▶ AzureResourceIngestor(token)       │  │
│  │                                                                  │  │
│  │  /api/subscriptions  ──▶  ingestor.get_subscriptions()           │  │
│  │  /api/scan           ──▶  ingestor.scan_subscription()           │  │
│  │                       ──▶  TopologyGraph.build_from_azure_scan() │  │
│  │  /api/graph          ──▶  TopologyGraph.to_cytoscape_format()    │  │
│  │  /api/subnet/res.    ──▶  ingestor.get_subnet_resources()        │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│            │                                      │                     │
│            ▼                                      ▼                     │
│  ┌─────────────────────┐             ┌────────────────────────────┐    │
│  │   azure_ingest.py   │             │      graph_builder.py      │    │
│  │                     │             │                            │    │
│  │  AccessTokenCred.   │             │  GraphNode (id, type,      │    │
│  │  AzureResourceIng.  │             │    label, parent, data)    │    │
│  │                     │             │  GraphEdge (src, tgt, type)│    │
│  │  azure-mgmt-sub  ──▶│ subscript.  │  TopologyGraph             │    │
│  │  azure-mgmt-net  ──▶│ VNets       │   .build_from_azure_scan() │    │
│  │  azure-mgmt-net  ──▶│ subnets     │   .to_cytoscape_format()   │    │
│  │  azure-mgmt-net  ──▶│ peerings    │   .to_dict()               │    │
│  │  azure-mgmt-net  ──▶│ NICs        └────────────────────────────┘    │
│  │  azure-mgmt-comp ──▶│ VMs                                           │
│  └──────────┬──────────┘                                               │
└─────────────┼───────────────────────────────────────────────────────────┘
              │  HTTPS (ARM API)  azure.core.credentials.AccessToken
              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      AZURE RESOURCE MANAGER API                         │
│                                                                         │
│   management.azure.com                                                  │
│                                                                         │
│   SubscriptionClient       → /subscriptions                            │
│   NetworkManagementClient  → /virtualNetworks                          │
│                            → /virtualNetworkPeerings                   │
│                            → /networkInterfaces                        │
│   ComputeManagementClient  → /virtualMachines                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Libraries Summary

### Frontend (CDN)
| Library | Version | Purpose |
|---|---|---|
| MSAL.js | 2.32.2 | Browser-based Azure AD OAuth2 / token acquisition |
| Cytoscape.js | 3.28.1 | Graph visualization, compound nodes, tap events |

### Backend (pip)
| Package | Version | Purpose |
|---|---|---|
| Flask | ≥3.0 | HTTP routing, template rendering |
| Flask-CORS | ≥4.0 | Cross-origin headers |
| Gunicorn | ≥21.2 | WSGI production server (2 workers) |
| azure-core | (transitive) | AccessToken credential wrapper |
| azure-mgmt-subscription | ≥3.0 | Subscription enumeration |
| azure-mgmt-resource | ≥23.0 | Resource management (available) |
| azure-mgmt-network | ≥24.0 | VNets, subnets, peerings, NICs |
| azure-mgmt-compute | ≥30.0 | VM detail (size, OS type) |

### Infrastructure-as-Code
| Tool | Purpose |
|---|---|
| Terraform azurerm ≥3.0 | Sample RGs, VNets, subnets, peerings, VMs |
