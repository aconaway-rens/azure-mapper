> **Note:** This project was generated with the assistance of [Claude](https://claude.ai) by Anthropic.

# Azure Mapper

A web application that visualizes Azure network topology — VNets, subnets, peerings, and resource groups — as an interactive diagram.

Everything runs locally. The app talks to the Azure Management API and nothing
else; no topology data leaves the host it runs on.

## Features

- **Zero-configuration auth** — uses the host's existing Azure identity (managed
  identity, `az login`, or a service principal). No app registration, client ID,
  or redirect URI to set up per deployment.
- **Automatic resource discovery** of VNets, subnets, and peerings for any subscription
- **Interactive topology diagram** using Cytoscape.js with compound nodes:
  - Resource groups as outer containers
  - VNets nested inside with address space labels
  - Subnets nested inside VNets with CIDR labels
  - Peering connections shown as dashed green edges (one-way peerings flagged in red)
- **Subnet drill-down** — click a subnet to load its NICs and VMs
- **Resource group filter** — tick one or more resource groups to narrow the
  diagram; the canvas re-packs around what's left, and the sidebar reports any
  peerings that now run off to a hidden resource group. Filtering is local to
  the browser, so toggling groups never re-queries Azure.
- **No outbound internet required** — frontend libraries are vendored, so the app
  works on locked-down hosts that can only reach Azure

## Prerequisites

- Docker and Docker Compose (or Python 3.12+ to run it directly)
- An identity with **Reader** on the subscriptions you want to map

## Get it

Azure Mapper ships as source you build yourself — there's no published package
or container image. Clone the latest release:

```bash
git clone --depth 1 --branch v1.0.1 https://github.com/aconaway-rens/azure-mapper.git
cd azure-mapper
```

Or, without git, download the release tarball:

```bash
curl -L https://github.com/aconaway-rens/azure-mapper/archive/refs/tags/v1.0.1.tar.gz | tar xz
cd azure-mapper-1.0.1
```

Both give you the same tree with Cytoscape.js vendored in, so the build pulls
nothing from the internet beyond GitHub itself and PyPI. See the
[releases page](https://github.com/aconaway-rens/azure-mapper/releases) for the
current version — substitute it for `v1.0.1` above, or drop `--branch v1.0.1`
to track `main`.

To move a clone to a newer release later:

```bash
git fetch --tags
git checkout v1.1.0          # whichever release you're moving to
docker compose up -d --build
```

## Quick Start

### On a VM with a managed identity

Nothing to configure — the app picks the identity up from the instance metadata service:

```bash
docker compose up -d --build
```

Open http://localhost:8080. The sidebar shows which identity resolved; then load
subscriptions and scan.

### On a machine using `az login`

Run it directly in a virtualenv — no Docker needed:

```bash
az login
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r app/requirements.txt
python app/app.py
```

Open http://127.0.0.1:8080. Override the bind with `HOST` / `PORT` if 8080 is taken.

To run that same session under Docker instead, build with the Azure CLI included
and mount the host's login (uncomment the `volumes` block in `docker-compose.yml`):

```bash
docker compose build --build-arg WITH_AZURE_CLI=true
docker compose up -d
```

### With a service principal

Copy `app/.env.example` to `app/.env` and set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
and `AZURE_CLIENT_SECRET`. Compose picks the file up automatically.

## Access and exposure

The app has **no sign-in of its own** — every request it serves acts as the host's
Azure identity. Compose therefore binds it to `127.0.0.1` only. To use it on a
remote VM, tunnel rather than exposing the port:

```bash
ssh -L 8080:localhost:8080 <vm>
```

## Troubleshooting auth

The sidebar reports the resolved identity on page load. If it reports an error,
`GET /api/identity` returns the full `DefaultAzureCredential` message listing
every source it tried and why each failed. Common causes:

- Running under Docker after `az login` on the host, without `WITH_AZURE_CLI=true`
  — the container has no `az` binary and can't read the host session.
- A VM with several user-assigned identities — set `AZURE_CLIENT_ID` to pick one.
- The identity has no Reader role, so it authenticates but lists zero subscriptions.

## Project Structure

```
.
├── Dockerfile
├── docker-compose.yml
├── app/
│   ├── app.py              # Flask backend
│   ├── azure_ingest.py     # Azure SDK resource discovery + credential handling
│   ├── graph_builder.py    # Topology graph model
│   ├── requirements.txt
│   ├── static/
│   │   ├── app.js          # Frontend (Cytoscape visualization)
│   │   └── vendor/         # Vendored JS libraries (see vendor/README.md)
│   └── templates/
│       └── index.html
└── samples/
    └── *.tf                # Terraform sample infrastructure for testing
```

## Architecture

The Flask backend resolves an Azure credential once per process with
`DefaultAzureCredential` and uses it with the Azure Management SDK to discover
resources, then builds a graph model that the frontend renders with Cytoscape.js
compound nodes. The frontend holds no credentials and issues plain same-origin
requests.
