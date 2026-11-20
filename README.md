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
  - Peering connections shown as dashed green edges: one-way peerings in red,
    Disconnected or out-of-sync ones in amber, cross-subscription ones bold
- **Several subscriptions in one diagram** — tick as many subscriptions as you
  like and scan them together. Each becomes a box holding its resource groups,
  and peerings between them join the real VNets on either side. The
  subscription with the most peerings to the others (usually the hub) is
  drawn on top with the rest beneath, so its peerings fan out rather than
  cutting through boxes. A subscription that fails to scan is skipped and
  named in the status line; the rest still draw. The ticked set is remembered
  in the browser for next time.
- **Peerings that leave the scan still show** — a peering to a VNet in a
  subscription you didn't scan (or can't read) ends at a dotted "not scanned"
  placeholder named from the VNet's ID, carrying the address space the peering
  reports, inside a box for its subscription. Its subscription is named when
  this identity can see it. Only one side of such a peering can be read, so
  Connected (which Azure reports only when both sides exist) is drawn as
  healthy, and anything else as one-way.
- **VMs and NICs in the diagram** — workloads are drawn inside their subnets,
  with size, OS, and private IPs on each card. Unattached NICs are drawn on
  their own, since an orphaned NIC is usually worth noticing.
- **NSG attachment** — a NIC or VM card guarded by a network security group
  wears a dark badge naming it, tagged onto the bottom of the card. Subnet-level
  groups are named in the subnet caption, and a subnet with none says so when
  you drill in, which is the coverage question worth asking. Rules themselves
  aren't read — this is attachment only.
- **Public IPs on the diagram** — a NIC with a public IP shows it under its
  private one, a VM card shows the public IPs of the NICs folded into it, and a
  public load balancer shows its frontend address. Anything holding a public IP
  is outlined in red, so what faces the internet is visible without reading
  every caption. A dynamic public IP on a stopped VM has no address until the
  VM starts, but it's still attached, so it reads `public (unassigned)`
  rather than disappearing.
- **Load balancers** — an internal LB is drawn inside its frontend subnet,
  above the workloads it fronts. A public LB belongs to no subnet and to no
  VNet, whatever its backend pool reaches into, so it sits above the VNet box
  entirely. Both are linked to the members they balance; a backend NIC folded
  into a VM card is linked via that card, so the line always lands on something
  you can see.
- **Multi-homed VMs stay one object** — a firewall or NVA with a NIC in four
  subnets is drawn once, at the VNet level, linked to its NIC in each subnet,
  rather than as four unrelated VMs. Azure requires every NIC on a VM to sit in
  the same VNet, so that level is always the right home for it.
- **Per-subnet render limit** — a subnet holding more NICs than the limit
  (default 10) collapses to a marked summary instead of flooding the diagram,
  so a subscription with fifty VMs behind one subnet still reads. Subnets
  holding workloads are outlined in purple whether or not their cards are
  drawn, and collapsed ones say what they're hiding.
- **Subnet drill-down** — click a subnet to expand it and dim everything else.
  Its contents come from the scan already in memory, so there's no round trip.
  Leave with Escape, a click on empty canvas, or the sidebar button.
- **Resource group filter** — tick one or more resource groups to narrow the
  diagram; the canvas re-packs around what's left, and the sidebar reports any
  peerings that now run off to a hidden resource group. Filtering is local to
  the browser, so toggling groups never re-queries Azure. With several
  subscriptions, each group is listed with its subscription, so two groups
  called `rg-network` stay apart, and a subscription box hides along with its
  last group.
- **Export** — download the diagram as a PNG (matching what's on screen,
  filters and all) or the whole scan as JSON, including the contents of
  collapsed subnets. The running version is shown in the sidebar.
- **No outbound internet required** — frontend libraries are vendored, so the app
  works on locked-down hosts that can only reach Azure

## Command-line queries

`app/azmap.py` answers questions about VMs, NICs, NSGs, subnets, peerings, VPN gateways and load balancers from the terminal,
using the app's own credential — whatever identity the web app resolves on a
host, the CLI resolves too. It needs nothing beyond `app/requirements.txt`.

```bash
python app/azmap.py vms                     # asks for subscription, then resource groups
python app/azmap.py vms  --sub Prod --rg 'web-*' --no-nsg
python app/azmap.py nics --sub Prod --rg '*' --unattached
python app/azmap.py nsgs --sub Prod --rg hub --vm fw01
python app/azmap.py subnets --sub Prod --rg '*' --routes
python app/azmap.py vms  --sub Prod --rg '*' --port 3389 --from internet --access allow
python app/azmap.py vms  --sub '*' --rg '*'    # every VM in every subscription
python app/azmap.py lbs  --sub '*' --rg '*'    # every load balancer, internal and external
python app/azmap.py peerings --sub '*' --rg '*' --problems   # broken or one-way peerings
python app/azmap.py export --sub '*'           # everything, to one snapshot file
python app/azmap.py gateways --file azmap-snapshot-20261119-1740.yaml --detail
```

Scope works in the app's two stages: the subscription is scanned once, then
resource groups are picked from what the scan found. Leave out `--sub` or
`--rg` on a terminal and it lists the choices and asks, then prints the flags
that would skip the questions next time. Piped or scripted, a missing `--rg`
means every group, and a missing `--sub` is an error unless only one is visible.

`--sub` repeats and takes wildcards; `--sub '*'` scans every subscription the
identity can see, in parallel, and adds a SUBSCRIPTION column to tables and
CSVs (JSON and YAML always carry `subscription`). A wildcard skips disabled
subscriptions, and one that fails to scan is reported and left out, so the
rest of the run carries on. At the subscription prompt, `*` or several
numbers pick more than one.

VM and NIC tables show each public IP's address and its resource name
(PUBLIC IP NAME). An attached public IP with no address yet reads
`(unassigned)`, with its name still shown.

Filters: different flags AND together, a repeated flag ORs, and names take
wildcards, case-insensitively. Each VM and NIC carries both NSG layers — the one
on the NIC and the one on each subnet it sits in — and `--nsg`, `--no-nsg` and
the rule filters look at both.

| Command | Filters beyond `--name` |
|---|---|
| `vms` | `--os`, `--subnet`, `--nsg`, `--no-nsg`, `--public-ip`, `--rules`, `--effective` |
| `nics` | `--vm`, `--unattached`, `--subnet`, `--nsg`, `--no-nsg`, `--public-ip`, `--rules` |
| `nsgs` | `--vm`, `--subnet`, `--unused` |
| `subnets` | `--vnet`, `--nsg`, `--no-nsg`, `--route-table`, `--no-udr`, `--routes`, `--rules` |
| `gateways` | `--type vpn\|expressroute`, `--detail` |
| `local-gateways` | `--unused`, `--detail` |
| `connections` | `--type`, `--status`, `--gateway`, `--remote`, `--detail` |
| `peerings` | `--vnet`, `--remote`, `--state connected\|disconnected\|initiated`, `--cross-sub`, `--problems` |
| `lbs` (or `load-balancers`) | `--type internal\|external\|gateway`, `--vm`, `--frontend`, `--unused`, `--port`, `--proto`, `--detail` |

NSG rule filters, on any command: `--port`, `--from` (`internet`, an IP or
CIDR, or a service tag), `--access allow|deny`, `--proto`, `--direction`
(inbound by default when filtering), `--defaults` to include Azure's built-in
rules. A VM or NIC matches when any rule in either layer does, and only the
matching rules are printed. Rules are matched **as written**: a match says a
rule allows the traffic, not that it gets through — a higher-priority deny, or
the other layer's NSG, can still stop it.

### Effective rules: what Azure actually enforces

```bash
python app/azmap.py vms --sub Prod --rg '*' --effective
python app/azmap.py vms --sub Prod --rg '*' --effective --port 22 --from internet
```

`--effective` asks Azure for each NIC's effective security rules — the portal's
*Effective security rules* view. Per VM and NIC it shows which NSGs are in
force and every rule from both layers in one list, in the order Azure evaluates
them (inbound: subnet, then NIC; outbound: NIC, then subnet), with service tags
expanded so `--from 10.0.5.5` matches a `VirtualNetwork` rule. Azure's default
rules are hidden unless `--defaults`.

It lists the rules in force; it doesn't compute a verdict. An allow on the
subnet followed by a deny on the NIC is shown as both rows — the order tells
you which wins.

Azure's terms, not ours:

- **Running VMs only.** For a stopped VM it prints the rules as written
  instead, labelled as such.
- **One call per NIC**, a few seconds each; they run in parallel.
- **Needs more than Reader** —
  `Microsoft.Network/networkInterfaces/effectiveNetworkSecurityGroups/action`,
  which Network Contributor includes. Without it, each VM says so and the
  rest of the run carries on.

### Every NSG in a subscription

```bash
python app/azmap.py nsgs --sub Prod --rg '*'                  # readable report
python app/azmap.py nsgs --sub Prod --rg '*' -o yaml --out nsgs.yaml
```

Each NSG lists the subnets it's bound to, the interfaces it's bound to (with
the VM behind each), every VM it covers either way, and its rules as separate
inbound and outbound tables. Add `--defaults` for Azure's built-in rules, which
complete the picture: without a custom deny, `DenyAllInBound` is what stops
everything else.

### Subnets, with their NSG and route table

```bash
python app/azmap.py subnets --sub Prod --rg '*'                 # one row per subnet
python app/azmap.py subnets --sub Prod --rg '*' --routes        # plus each UDR
python app/azmap.py subnets --sub Prod --rg '*' --no-udr        # nothing steering traffic
python app/azmap.py subnets --sub Prod --rg '*' --routes -o csv --out subnets.csv
```

Each subnet shows its VNet, prefix, the NSG bound to it and the route table
bound to it, with a count of its routes. `--routes` adds each table's routes
(prefix, next hop type, next hop IP) and whether BGP route propagation is on;
`--rules` adds the NSG's rules, and the NSG rule filters work here too. A
subnet's resource group is its VNet's. `--name` matches `NAME` or `VNET/NAME`.

These are the routes **as written** — the UDRs on the table, not the
NIC's effective routes, which also carry Azure's system routes and anything
learned over BGP. With `-o csv --routes` there's a row per route, and a subnet
without a route table still gets one row, so nothing goes missing from the sheet.

### VPN: gateways, local gateways and connections

```bash
python app/azmap.py gateways       --sub '*' --rg '*'           # VPN and ExpressRoute gateways
python app/azmap.py local-gateways --sub '*' --rg '*'           # the on-prem side
python app/azmap.py connections    --sub '*' --rg '*'           # tunnels, with live status
python app/azmap.py connections    --sub Prod --rg '*' --status notconnected
python app/azmap.py gateways       --sub Prod --rg '*' --detail # every setting
python app/azmap.py connections    --sub '*' --rg '*' -o yaml --out vpn-connections.yaml
```

The tables summarise: a gateway's type, SKU, generation, active-active, BGP
ASN and peering address, public IPs, VNet, point-to-site pool and the
connections on it; a local gateway's address or FQDN, address space and BGP
peer; a connection's type, status, both ends, IKE version, BGP and custom
IPsec policy (`default` when none is set). The objects are linked, so a
connection names its gateway and remote end and each gateway lists its
connections, across subscriptions too.

`--detail`, `-o json` and `-o yaml` carry **every setting** Azure returns,
under its own names — the same `properties` that `az network vnet-gateway show`
prints — so nothing is left out for want of a column. Pre-shared keys and
RADIUS secrets are always removed: they'd otherwise land in files.

Each gateway also carries what its settings only point at: the full
settings of its public IPs (SKU, tier, allocation, IP version, zones, DNS
label, idle timeout) and its GatewaySubnet's prefix with the VNet's address
space, so one record is enough to recreate it.

Reading these takes Reader. The resource types are found through the
subscription's resource listing, then each is read individually, which is
what returns a connection's live status; one that can't be read is listed as
`(unreadable)` with the reason.

### Peerings, across subscriptions

```bash
python app/azmap.py peerings --sub '*' --rg '*'                # every peering, both sides
python app/azmap.py peerings --sub '*' --rg '*' --problems     # not Connected, out of sync, or one-way
python app/azmap.py peerings --sub '*' --rg '*' --cross-sub    # only those leaving their subscription
python app/azmap.py peerings --sub Prod --rg '*' --remote 'hub*'
```

One row per side of each peering: the VNet it's on, the remote VNet with its
resource group, subscription and address space, state and sync level, the
forwarded-traffic, gateway-transit and use-remote-gateways settings, and
**OTHER SIDE**: `present` when the reverse peering was found (or the state is
Connected, which Azure reports only when both sides exist), `missing` when
the remote subscription was scanned and has no peering back, and
`not scanned` when it wasn't, rather than a guess. Scan both subscriptions
(`--sub '*'`) to check both sides of a cross-subscription peering.

### Load balancers, internal and external

```bash
python app/azmap.py lbs --sub '*' --rg '*'                     # one line each
python app/azmap.py lbs --sub Prod --rg '*' --type external
python app/azmap.py lbs --sub '*' --rg '*' --vm 'web*'         # which LBs front these VMs?
python app/azmap.py lbs --sub '*' --rg '*' --port 443          # what listens on 443?
python app/azmap.py lbs --sub Prod --rg '*' --detail           # frontends, pools, rules, probes, NAT
python app/azmap.py lbs --sub '*' --rg '*' -o csv --detail --out lb-rules.csv
```

The table gives each load balancer's type (`external` when a frontend has a
public IP, `internal` otherwise, `gateway` for a Gateway SKU), SKU, frontend
addresses with their public IP or subnet, rules as `TCP 443>8443` (`HA ports`
for an internal all-ports rule), the VMs in its backend pools, and probes.

`--detail` adds a table per part: frontends (address, public IP or subnet,
allocation, zones); backend pools, one row per member with its VM, NIC,
ip-config and private IP; load-balancing rules with every option (floating
IP, idle timeout, TCP reset, distribution, outbound SNAT); probes; inbound NAT
rules and NAT pools with their targets; and outbound rules. With `-o csv`,
`--detail` writes one row per load-balancing rule with its frontend, pool
members and probe spelled out.

`--port` and `--proto` here mean what a load-balancing or NAT rule listens
on, a NAT port range included; an HA ports rule matches every port.
`-o json` and `-o yaml` carry all of that plus every setting Azure returns
under its own names, and each public frontend's full public IP settings, so
one record is enough to recreate it. Scale set instances show as
`scaleset_instance`; a pool of bare IP addresses lists the addresses.

### Snapshots: export once, read back offline

```bash
python app/azmap.py export --sub '*'                                   # azmap-snapshot-YYYYMMDD-HHMM.yaml
python app/azmap.py export --sub Prod --out prod.yaml
python app/azmap.py gateways    --file prod.yaml --detail              # no Azure, no login
python app/azmap.py subnets     --file prod.yaml --rg '*' --routes
python app/azmap.py connections --file prod.yaml -o csv --out tunnels.csv
```

`export` saves everything azmap reads — VMs, NICs, public IPs, VNets and
subnets, NSGs with their rules, route tables, gateways, local gateways,
connections, load balancers and peerings — for each subscription into one YAML file, with when it was
taken and by which version. `--file` on any command reads that file instead
of Azure: the same tables, filters, `--detail` and output formats, with no
credential needed, so it works when Azure doesn't. It covers every
subscription in the file unless `--sub` narrows it.

Keep these as the record to rebuild from. Things to know:

- **No secrets.** Pre-shared keys and RADIUS secrets are left out; keep them
  in a password vault. A tunnel can't be rebuilt without its key.
- **No effective rules.** `--effective` asks Azure live, so it doesn't work
  with `--file`; `--rules` shows the rules as written, which is what you'd
  rebuild anyway.
- **Re-export after upgrading.** A snapshot holds what its version of azmap
  recorded; one taken before load balancers (v1.13.0) or peerings (v1.14.0)
  were added says so rather than showing none.
- **Diffable.** Gateway and load balancer settings are written in sorted order, so two
  snapshots of an unchanged environment diff clean.
- **Only export files read back.** A file from `-o yaml` is a report shaped
  for reading, not a snapshot, and `--file` says so.
- **Plain YAML.** Any YAML reader opens it. azmap reads back the YAML it
  writes without needing a YAML library; a hand-edited file that breaks the
  layout is reported with a line number.

### Output formats

Output is a table by default. `-o json` and `-o yaml` carry the same data —
rules nested under each object, and for `nsgs` split into `inbound` and
`outbound` lists. `-o csv` gives one row per rule. Long address lists are shortened in tables
(`+13 more`) unless you pass `--full`. Prompts and progress go to stderr, so
stdout pipes cleanly.

**For Excel, use `--out` rather than `>`:**

```powershell
python app\azmap.py vms --effective -o csv --out vms.csv
```

Windows PowerShell 5.1 redirects with `>` as UTF-16, which Excel won't split
on commas — every row lands in column A. `--out` writes UTF-8 with a BOM and
Windows line endings, which Excel opens as columns whatever shell you use.

## Tools

[`tools/Get-AzureNetworkState.ps1`](tools/Get-AzureNetworkState.ps1) prints the
same objects as text rather than drawing them — VMs and interfaces, load
balancers, NSGs, route tables, optional backend health — for pasting into a
ticket or an email. It reads through the Azure CLI and runs on its own, with no
dependency on the app. See [tools/README.md](tools/README.md).

## Prerequisites

- Docker and Docker Compose (or Python 3.12+ to run it directly)
- An identity with **Reader** on the subscriptions you want to map
- On **Windows for ARM**, an x64 build of Python — see below

### Architecture notes

x86-64 and ARM are both fine on Linux and macOS, and Docker sidesteps the
question entirely: the image is Linux, and every dependency ships Linux wheels
for x86-64 and aarch64 alike.

**Windows on ARM is the exception.** `cryptography` — pulled in by
`azure-identity` — publishes no `win_arm64` wheels at all, only `win_amd64`. An
ARM64 Python therefore falls back to compiling it from Rust source and fails
unless you have both Rust and the MSVC C++ build tools installed:

```
error: linker `link.exe` not found
ERROR: Failed building wheel for cryptography
```

Use the **x64** Python build instead. It runs under emulation and gets the
prebuilt wheel, which is faster to set up and roughly as fast to run for a tool
that spends its time waiting on Azure:

```powershell
winget install Python.Python.3.13 --architecture x64
```

On python.org that's the "Windows installer (64-bit)" download — the
`-amd64.exe` file, not `-arm64.exe`. Build the venv with that interpreter, then
confirm which one you actually got before installing anything:

```powershell
python -c "import sysconfig; print(sysconfig.get_platform())"
```

`win-amd64` is what you want. If it prints `win-arm64`, the venv came from the
ARM interpreter and `pip install` will fail on cryptography — use `py --list-paths`
to find the x64 install and recreate the venv from it.

## Get it

Azure Mapper ships as source you build yourself — there's no published package
or container image. Clone the latest release:

```bash
git clone --depth 1 --branch v1.13.0 https://github.com/aconaway-rens/azure-mapper.git
cd azure-mapper
```

Or, without git, download the release tarball:

```bash
curl -L https://github.com/aconaway-rens/azure-mapper/archive/refs/tags/v1.13.0.tar.gz | tar xz
cd azure-mapper-1.13.0
```

Both give you the same tree with Cytoscape.js vendored in, so the build pulls
nothing from the internet beyond GitHub itself and PyPI. See the
[releases page](https://github.com/aconaway-rens/azure-mapper/releases) for the
current version — substitute it for `v1.13.0` above, or drop `--branch v1.13.0`
to track `main`.

To move a clone to a newer release later:

```bash
git fetch --tags
git checkout v1.13.0          # whichever release you're moving to
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

On Windows for ARM, make sure that venv is built from an x64 Python — see
[Architecture notes](#architecture-notes).

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
│   ├── azmap.py            # CLI: VMs, NICs, NSGs, subnets, peerings, VPN, LBs; snapshots
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
