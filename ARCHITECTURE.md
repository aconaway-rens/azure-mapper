# Azure Mapper — Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              BROWSER                                    │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                        index.html                                │  │
│  │  ┌──────────────────────────────────────────────────────────┐   │  │
│  │  │                        app.js                            │   │  │
│  │  │  No credentials held here — plain same-origin fetch()    │   │  │
│  │  │  checkIdentity() ──▶ GET /api/identity (display only)    │   │  │
│  │  │                                                          │   │  │
│  │  │  ┌────────────────────────────────────────────────────┐  │   │  │
│  │  │  │        Cytoscape.js v3.28  (vendored, local)       │  │   │  │
│  │  │  │                                                    │  │   │  │
│  │  │  │  Compound nodes:                                   │  │   │  │
│  │  │  │   RG → VNet → Subnet → VM → detail                 │  │   │  │
│  │  │  │  Peering edges (VNet↔VNet)                         │  │   │  │
│  │  │  │  Attachment edges (spanning VM → NIC)              │  │   │  │
│  │  │  │  Balancing edges (LB → backend members)            │  │   │  │
│  │  │  │  Drill-down: Subnet → VM cards                     │  │   │  │
│  │  │  │  Resource-group filter (display-only, client-side) │  │   │  │
│  │  │  │  Per-subnet render limit; busy subnets collapse    │  │   │  │
│  │  │  │  Custom manual layout engine                       │  │   │  │
│  │  │  └────────────────────────────────────────────────────┘  │   │  │
│  │  └──────────────────────────────────────────────────────────┘   │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │  HTTP/REST  (same-origin, no auth header)
                                 │  GET /api/identity
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
│  │  Flask (same-origin only — no CORS)                              │  │
│  │                                                                  │  │
│  │  get_ingestor()  ──▶ AzureResourceIngestor()  (process-wide)     │  │
│  │                                                                  │  │
│  │  /api/identity       ──▶  describe_identity()                    │  │
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
│  │  get_credential()   │             │  GraphNode (id, type,      │    │
│  │   DefaultAzureCred. │             │    label, parent, data)    │    │
│  │  AzureResourceIng.  │             │  GraphEdge (src, tgt, type)│    │
│  │                     │             │  TopologyGraph             │    │
│  │  azure-mgmt-sub  ──▶│ subscript.  │   .build_from_azure_scan() │    │
│  │  azure-mgmt-net  ──▶│ VNets       │   .to_cytoscape_format()   │    │
│  │  azure-mgmt-net  ──▶│ subnets     │   .to_dict()               │    │
│  │  azure-mgmt-net  ──▶│ peerings    └────────────────────────────┘    │
│  │  azure-mgmt-net  ──▶│ NICs                                          │
│  │  azure-mgmt-comp ──▶│ VMs                                           │
│  └──────────┬──────────┘                                               │
└─────────────┼───────────────────────────────────────────────────────────┘
              │  HTTPS (ARM API)  token from DefaultAzureCredential
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

Azure Resource Manager is the only network destination. Discovered topology
stays in the Flask process's memory and is never sent anywhere else.

## Authentication

`DefaultAzureCredential` resolves one credential per process, trying in order:
service-principal environment variables, workload/managed identity (IMDS), then
an `az login` session. Nothing tenant-specific is compiled into the app, so the
same image runs against any subscription the host identity can read.

The consequence is that the app has no per-user authorization: every request it
serves acts as the host identity. It binds to loopback by default for that
reason — see the exposure notes in the README.

## Libraries Summary

### Frontend (vendored in `app/static/vendor/`)
| Library | Version | Purpose |
|---|---|---|
| Cytoscape.js | 3.28.1 | Graph visualization, compound nodes, tap events |

### Backend (pip)
| Package | Version | Purpose |
|---|---|---|
| Flask | ≥3.0 | HTTP routing, template rendering |
| Gunicorn | ≥21.2 | WSGI production server (2 workers) |
| azure-identity | ≥1.19 | DefaultAzureCredential — ambient host auth |
| azure-mgmt-subscription | ≥3.0 | Subscription enumeration |
| azure-mgmt-resource | ≥23.0 | Resource management (available) |
| azure-mgmt-network | ≥24.0 | VNets, subnets, peerings, NICs |
| azure-mgmt-compute | ≥30.0 | VM detail (size, OS type) |
| python-dotenv | ≥1.0 | Optional app/.env loading |

### Infrastructure-as-Code
| Tool | Purpose |
|---|---|
| Terraform azurerm ≥3.0 | Sample RGs, VNets, subnets, peerings, VMs |
