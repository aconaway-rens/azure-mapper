"""Command-line queries over the same Azure objects the app draws.

    python app/azmap.py vms   [--sub SUB] [--rg RG ...] [filters]
    python app/azmap.py nics  [--sub SUB] [--rg RG ...] [filters]
    python app/azmap.py nsgs  [--sub SUB] [--rg RG ...] [filters]
    python app/azmap.py subnets [--sub SUB] [--rg RG ...] [filters]
    python app/azmap.py gateways | local-gateways | connections [--detail]

Authentication is the app's own: DefaultAzureCredential, through azure_ingest,
so whatever identity the web app resolves on this host, the CLI resolves too.

Filtering runs in the app's two stages. Each subscription is scanned once, then
resource groups are picked from what that scan found, locally. Leave out --sub
or --rg on a terminal and the CLI asks for them, listing the choices.
--sub '*' scans every subscription the identity can see.

NSG rule filters (--port, --from, ...) match rules as written. They answer
"which rules allow this?", not "is this reachable?" — a higher-priority deny,
or the other layer's NSG, can still block what a matching rule allows.
"""

import argparse
import contextlib
import copy
import csv
import fnmatch
import ipaddress
import json
import logging
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError

from azure_ingest import AzureResourceIngestor
from version import __version__

# What a rule's source looks like when it admits the whole internet.
INTERNET_SOURCES = {"*", "any", "internet", "0.0.0.0/0", "::/0"}

RULE_HEADERS = [
    "PRI", "DIR", "ACCESS", "PROTO", "SOURCE", "SPORT", "DEST", "DPORT", "NAME"
]


def note(message: str = "") -> None:
    """Talk to the operator on stderr, so stdout stays clean for pipes."""
    print(message, file=sys.stderr)


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


# Set once either stage has had to ask, so the run can end by printing the
# flags that would have skipped the questions.
_prompted = False


def ask(question: str) -> str:
    global _prompted
    _prompted = True
    return input(question).strip()


def matches_any(value: Optional[str], patterns: Iterable[str]) -> bool:
    """Case-insensitive glob match against any of the patterns."""
    if not value:
        return False
    value = value.lower()
    return any(fnmatch.fnmatchcase(value, p.lower()) for p in patterns)


# ----------------------------------------------------------------- scan ---

def scan(ingestor: AzureResourceIngestor, subscription: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """Fetch and join VMs, NICs, subnets, NSGs and route tables for one subscription.

    Every NIC and VM comes back with the NSGs that apply to it, by layer: the
    NSG on the NIC itself, and the NSG on each subnet it sits in. Traffic has
    to pass both, so both are listed.
    """
    if kind in VPN_KINDS:
        return scan_vpn(ingestor, subscription)
    subscription_id = subscription["id"]
    public_ips = ingestor.get_public_ips(subscription_id)
    compute = ingestor.get_nics_and_vms(subscription_id, public_ips)
    vnets = ingestor.get_vnets_and_subnets(subscription_id)
    nsgs = ingestor.get_network_security_groups(subscription_id)
    route_tables = ingestor.get_route_tables(subscription_id)

    # NIC records carry lowercased subnet IDs; the VNet listing has the
    # original case, so names print the way they were created.
    subnet_names = {
        subnet["id"].lower(): f"{vnet['name']}/{subnet['name']}"
        for vnet in vnets.values()
        for subnet in vnet["subnets"]
    }

    nsg_on_nic: Dict[str, Dict[str, Any]] = {}
    nsg_on_subnet: Dict[str, Dict[str, Any]] = {}
    for nsg in nsgs:
        nsg["subnets"] = [subnet_names.get(s, s) for s in nsg["subnet_ids"]]
        nsg["nics"] = []
        nsg["vms"] = []
        for nic_id in nsg["nic_ids"]:
            nsg_on_nic[nic_id] = nsg
        for subnet_id in nsg["subnet_ids"]:
            nsg_on_subnet[subnet_id] = nsg

    # get_nics_and_vms emits one record per ip-config; fold them back into
    # one entry per NIC.
    nics: Dict[str, Dict[str, Any]] = {}
    for record in compute["nics"]:
        key = record["id"].lower()
        nic = nics.setdefault(key, {
            "id": record["id"],
            "name": record["name"],
            "resource_group": record["id"].split("/")[4],
            "vm_id": record["vm_id"],
            "vm_name": record["vm_name"],
            "private_ips": [],
            "public_ips": [],
            "public_ip_names": [],
            "subnet_ids": [],
        })
        if record["private_ip"]:
            nic["private_ips"].append(record["private_ip"])
        if record["public_ip"]:
            nic["public_ips"].append(record["public_ip"])
        if record.get("public_ip_name"):
            nic["public_ip_names"].append(record["public_ip_name"])
        if record["subnet_id"] not in nic["subnet_ids"]:
            nic["subnet_ids"].append(record["subnet_id"])

    for key, nic in nics.items():
        nic["subnets"] = [subnet_names.get(s, s) for s in nic["subnet_ids"]]
        layers = []
        for subnet_id in nic["subnet_ids"]:
            if subnet_id in nsg_on_subnet:
                layers.append({
                    "layer": "subnet",
                    "attached_to": subnet_names.get(subnet_id, subnet_id),
                    "nsg": nsg_on_subnet[subnet_id],
                })
        if key in nsg_on_nic:
            layers.append({
                "layer": "nic", "attached_to": nic["name"], "nsg": nsg_on_nic[key],
            })
            nsg_on_nic[key]["nics"].append({"name": nic["name"], "vm": nic["vm_name"]})
        nic["layers"] = layers

    vms = []
    for vm_key, vm in compute["vms"].items():
        vm = dict(vm)
        vm["nics"] = [
            n for n in nics.values() if (n["vm_id"] or "").lower() == vm_key
        ]
        seen = set()
        vm["layers"] = []
        for nic in vm["nics"]:
            for layer in nic["layers"]:
                ident = (layer["layer"], layer["attached_to"], layer["nsg"]["id"])
                if ident not in seen:
                    seen.add(ident)
                    vm["layers"].append(layer)
                    if vm["name"] not in layer["nsg"]["vms"]:
                        layer["nsg"]["vms"].append(vm["name"])
        vms.append(vm)

    # A subnet's resource group is its VNet's: subnets aren't top-level
    # resources, so that's where the portal shows them too.
    subnets = []
    for vnet in vnets.values():
        for subnet in vnet["subnets"]:
            key = subnet["id"].lower()
            path = subnet_names[key]
            layers = []
            if key in nsg_on_subnet:
                layers.append({"layer": "subnet", "attached_to": path, "nsg": nsg_on_subnet[key]})
            rt_id = subnet["route_table_id"]
            subnets.append({
                "id": subnet["id"],
                "name": subnet["name"],
                "vnet": vnet["name"],
                "path": path,
                "resource_group": vnet["resource_group"],
                "location": vnet["location"],
                "address_prefixes": subnet["address_prefixes"],
                "layers": layers,
                # A table we couldn't list (another subscription, no access)
                # still shows by name, from the last segment of its ID.
                "route_table": route_tables.get(rt_id) if rt_id else None,
                "route_table_id": rt_id,
            })

    # Every object says which subscription it came from, for runs that
    # scan more than one.
    for obj in [*vms, *nics.values(), *nsgs, *subnets]:
        obj["subscription"] = subscription["name"]
        obj["subscription_id"] = subscription_id

    by_name = lambda o: (o["resource_group"].lower(), o["name"].lower())
    return {
        "vms": sorted(vms, key=by_name),
        "nics": sorted(nics.values(), key=by_name),
        "nsgs": sorted(nsgs, key=by_name),
        "subnets": sorted(subnets, key=lambda o: (
            o["resource_group"].lower(), o["vnet"].lower(), o["name"].lower(),
        )),
        "subnet_names": subnet_names,
    }


# ------------------------------------------------------ stage 1: the sub ---

def enabled(sub: Dict[str, Any]) -> bool:
    return str(sub["state"]).lower().endswith("enabled")


def choose_subscriptions(
    ingestor: AzureResourceIngestor, wanted: Optional[List[str]]
) -> List[Dict[str, Any]]:
    """The subscriptions to scan.

    ``--sub`` takes a name or ID, wildcards allowed, and repeats; ``'*'`` is
    every subscription this identity can see. A wildcard skips disabled
    subscriptions, which refuse reads; a subscription named outright is
    scanned whatever its state, so its error is the one you asked for.
    """
    subscriptions = sorted(
        ingestor.get_subscriptions(), key=lambda s: (s["name"] or "").lower()
    )
    if not subscriptions:
        raise SystemExit(
            "This identity can see no subscriptions. It authenticated, so it "
            "most likely lacks the Reader role."
        )
    listing = "\n".join(f"  {s['name']}  ({s['id']})" for s in subscriptions)

    def pick(tokens: List[str]) -> List[Dict[str, Any]]:
        chosen, bad, skipped = [], [], []
        for token in tokens:
            exact = [s for s in subscriptions
                     if token.lower() in (s["id"].lower(), (s["name"] or "").lower())]
            globbed = [
                s for s in subscriptions
                if matches_any(s["name"], [token]) or matches_any(s["id"], [token])
            ]
            found = exact or [s for s in globbed if enabled(s)]
            if not exact:
                for s in globbed:
                    if not enabled(s) and s["name"] not in skipped:
                        skipped.append(s["name"])
            if not found:
                bad.append(token)
            chosen += [s for s in found if s not in chosen]
        if bad:
            raise ValueError(", ".join(bad))
        if skipped:
            note(f"Skipping disabled subscription(s): {', '.join(skipped)} (name one with --sub to try it)")
        return chosen

    if wanted:
        try:
            return pick(wanted)
        except ValueError as e:
            raise SystemExit(f"No subscription matches '{e}'. Available:\n{listing}")

    if len(subscriptions) == 1:
        note(f"Subscription: {subscriptions[0]['name']} (the only one visible)")
        return subscriptions

    if not interactive():
        raise SystemExit(f"Pass --sub (or --sub '*' for all); this identity sees several:\n{listing}")

    note("Subscriptions:")
    for i, sub in enumerate(subscriptions, 1):
        state = "" if enabled(sub) else f"  [{sub['state']}]"
        note(f"  {i:>3}) {sub['name']}  ({sub['id']}){state}")
    while True:
        answer = ask("Pick subscriptions (numbers or names, comma-separated; * for all): ")
        tokens = [
            subscriptions[int(t) - 1]["id"] if t.isdigit() and 1 <= int(t) <= len(subscriptions) else t
            for t in answer.replace(",", " ").split()
        ]
        if not tokens:
            continue
        try:
            return pick(tokens)
        except ValueError as e:
            note(f"  Not in the list: {e}")


def scan_all(
    ingestor: AzureResourceIngestor, subscriptions: List[Dict[str, Any]], kind: str,
) -> Dict[str, Any]:
    """Scan each subscription, in parallel, into one inventory.

    A subscription that fails (no access, a provider not registered) is
    reported and left out, so one bad subscription doesn't sink a run over
    all of them. With only one, its failure is the run's.
    """
    verb = "Reading" if isinstance(ingestor, SnapshotIngestor) else "Scanning"
    if len(subscriptions) == 1:
        note(f"{verb} {subscriptions[0]['name']}...")
        inventories = [scan(ingestor, subscriptions[0], kind)]
    else:
        note(f"{verb} {len(subscriptions)} subscriptions...")

        def one(sub):
            try:
                return scan(ingestor, sub, kind)
            except HttpResponseError as e:
                note(f"  Skipped {sub['name']}: {(e.message or str(e)).splitlines()[0]}")
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            inventories = [i for i in pool.map(one, subscriptions) if i]
        if not inventories:
            raise SystemExit("Every subscription failed to scan.")

    merged: Dict[str, Any] = {"subnet_names": {}}
    for key in inventories[0]:
        if key == "subnet_names":
            continue
        merged[key] = sorted(
            (o for inv in inventories for o in inv[key]),
            key=lambda o: (o["subscription"].lower(), o["resource_group"].lower(),
                           (o.get("vnet") or "").lower(), o["name"].lower()),
        )
    for inv in inventories:
        merged["subnet_names"].update(inv["subnet_names"])
    if kind in VPN_KINDS:
        # Linked after the merge, so a connection to a gateway in another
        # subscription still finds it.
        link_vpn(merged)
    return merged


# ------------------------------------------------------------------- vpn ---

VPN_KINDS = ("gateways", "local-gateways", "connections")


def ref_id(value: Any) -> Optional[str]:
    """The resource ID out of an ARM reference ({"id": ...}), lowercased."""
    if isinstance(value, dict) and value.get("id"):
        return value["id"].lower()
    return None


def scan_vpn(ingestor: AzureResourceIngestor, subscription: Dict[str, Any]) -> Dict[str, Any]:
    """Gateways, local gateways and connections, with public IPs resolved."""
    subscription_id = subscription["id"]
    public_ips = ingestor.get_public_ips(subscription_id)
    vnets = ingestor.get_vnets_and_subnets(subscription_id)
    raw = ingestor.get_vpn_inventory(subscription_id)

    # A gateway's settings name its subnet and public IPs only by ID; to
    # rebuild it you need what's behind them, so carry that on the record.
    subnets_by_id = {
        sub["id"].lower(): (vnet, sub) for vnet in vnets.values() for sub in vnet["subnets"]
    }
    for gw in raw["gateways"]:
        gw["public_ips"], gw["gateway_subnets"], names = [], [], []
        for config in gw["properties"].get("ipConfigurations") or []:
            props = config.get("properties") or {}
            pip = ref_id(props.get("publicIPAddress"))
            if pip:
                entry = public_ips.get(pip) or {}
                gw["public_ips"].append({
                    "name": entry.get("name") or pip.split("/")[-1],
                    "address": entry.get("ip_address") or ("(unassigned)" if entry else "(unknown)"),
                    "ip_configuration": config.get("name") or "",
                    **{k: v for k, v in entry.items() if k not in ("ip_address", "name")},
                })
            subnet_id = ref_id(props.get("subnet"))
            if subnet_id and subnet_id in subnets_by_id:
                vnet, sub = subnets_by_id[subnet_id]
                if vnet["name"] not in names:
                    names.append(vnet["name"])
                    gw["gateway_subnets"].append({
                        "vnet": vnet["name"],
                        "vnet_resource_group": vnet["resource_group"],
                        "vnet_address_space": vnet["address_prefixes"],
                        "subnet": sub["name"],
                        "subnet_prefixes": sub["address_prefixes"],
                    })
            elif subnet_id and len(subnet_id.split("/")) > 8:
                name = subnet_id.split("/")[8]
                if name not in names:
                    names.append(name)
        gw["vnet"] = ", ".join(names)

    inventory = {
        "gateways": raw["gateways"],
        "local-gateways": raw["local_gateways"],
        "connections": raw["connections"],
        "subnet_names": {},
    }
    for objects in (v for k, v in inventory.items() if k != "subnet_names"):
        for obj in objects:
            obj["subscription"] = subscription["name"]
            obj["subscription_id"] = subscription_id
            obj.setdefault("connections", [])
    return inventory


def link_vpn(inventory: Dict[str, Any]) -> None:
    """Tie each connection to its gateway and remote end, and back again."""
    by_id = {
        o["id"].lower(): o
        for key in ("gateways", "local-gateways") for o in inventory.get(key, [])
    }
    for conn in inventory.get("connections", []):
        props = conn["properties"]
        conn.pop("connections", None)
        gw_id = ref_id(props.get("virtualNetworkGateway1"))
        gw = by_id.get(gw_id) if gw_id else None
        conn["gateway"] = gw["name"] if gw else (gw_id.split("/")[-1] if gw_id else "")
        if gw:
            gw["connections"].append(conn["name"])

        remote = {"kind": "", "name": "", "address": ""}
        for field, label in (("localNetworkGateway2", "local gateway"),
                             ("virtualNetworkGateway2", "gateway"),
                             ("peer", "ExpressRoute circuit")):
            rid = ref_id(props.get(field))
            if not rid:
                continue
            target = by_id.get(rid)
            remote["kind"] = label
            remote["name"] = target["name"] if target else rid.split("/")[-1]
            if target:
                if conn["name"] not in target["connections"]:
                    target["connections"].append(conn["name"])
                tprops = target["properties"]
                remote["address"] = (
                    tprops.get("gatewayIpAddress") or tprops.get("fqdn")
                    or " ".join(p["address"] for p in target.get("public_ips", []))
                )
            break
        conn["remote"] = remote


def yes_no(value: Any) -> str:
    return "yes" if value else "no"


def ipsec_cell(props: Dict[str, Any]) -> str:
    policies = props.get("ipsecPolicies") or []
    if not policies:
        return "default"
    p = policies[0]
    text = (f"IKE {p.get('ikeEncryption')}/{p.get('ikeIntegrity')}/{p.get('dhGroup')}, "
            f"IPsec {p.get('ipsecEncryption')}/{p.get('ipsecIntegrity')}/{p.get('pfsGroup')}")
    return text + (f" +{len(policies) - 1} more" if len(policies) > 1 else "")


def bgp_cell(settings: Optional[Dict[str, Any]], enabled: bool = True) -> str:
    if not enabled or not settings or not settings.get("asn"):
        return "off"
    peer = settings.get("bgpPeeringAddress")
    return f"ASN {settings['asn']}" + (f" peer {peer}" if peer else "")


VPN_HEADERS = {
    "gateways": ["NAME", "RESOURCE GROUP", "TYPE", "VPN TYPE", "SKU", "GEN", "ACTIVE-ACTIVE",
                 "BGP", "PUBLIC IP", "VNET", "P2S POOL", "CONNECTIONS"],
    "local-gateways": ["NAME", "RESOURCE GROUP", "ADDRESS", "ADDRESS SPACE", "BGP", "CONNECTIONS"],
    "connections": ["NAME", "RESOURCE GROUP", "TYPE", "STATUS", "GATEWAY", "REMOTE",
                    "REMOTE ADDRESS", "PROTOCOL", "BGP", "IPSEC POLICY"],
}


def vpn_row(obj: Dict[str, Any], kind: str, cap: Optional[int] = None) -> List[str]:
    if obj.get("error"):
        # Blank rather than "no"/"default", which would read as settings.
        cells = [obj["name"], obj["resource_group"]] + [""] * (len(VPN_HEADERS[kind]) - 2)
        cells[3 if kind == "connections" else 2] = "(unreadable)"
        return cells
    props = obj["properties"]
    if kind == "gateways":
        p2s = ((props.get("vpnClientConfiguration") or {}).get("vpnClientAddressPool") or {})
        return [
            obj["name"], obj["resource_group"],
            props.get("gatewayType") or "",
            props.get("vpnType") or "",
            (props.get("sku") or {}).get("name") or "",
            (props.get("vpnGatewayGeneration") or "").replace("Generation", "Gen").replace("None", ""),
            yes_no(props.get("activeActive")),
            bgp_cell(props.get("bgpSettings"), props.get("enableBgp")),
            " ".join(f"{p['address']} ({p['name']})" for p in obj["public_ips"]),
            obj["vnet"],
            join_list(p2s.get("addressPrefixes") or [], cap) or "none",
            join_list(obj["connections"], cap),
        ]
    if kind == "local-gateways":
        space = (props.get("localNetworkAddressSpace") or {}).get("addressPrefixes") or []
        return [
            obj["name"], obj["resource_group"],
            props.get("gatewayIpAddress") or props.get("fqdn") or "",
            join_list(space, cap),
            bgp_cell(props.get("bgpSettings")),
            join_list(obj["connections"], cap),
        ]
    remote = obj.get("remote") or {}
    return [
        obj["name"], obj["resource_group"],
        props.get("connectionType") or "",
        props.get("connectionStatus") or "",
        obj.get("gateway") or "",
        f"{remote['name']} ({remote['kind']})" if remote.get("name") else "",
        remote.get("address") or "",
        props.get("connectionProtocol") or "",
        yes_no(props.get("enableBgp")),
        ipsec_cell(props),
    ]


def vpn_json(obj: Dict[str, Any]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "name": obj["name"],
        "id": obj["id"],
        "resource_group": obj["resource_group"],
        "location": obj["location"],
    }
    if "public_ips" in obj:
        entry["vnet"] = obj["vnet"]
        entry["gateway_subnets"] = obj.get("gateway_subnets", [])
        entry["public_ips"] = obj["public_ips"]
    if "gateway" in obj:
        entry["gateway"] = obj["gateway"]
        entry["remote"] = obj["remote"]
    else:
        entry["connections"] = obj["connections"]
    if obj.get("error"):
        entry["error"] = obj["error"]
    entry["tags"] = obj["tags"]
    # Every setting Azure returned, under Azure's own names.
    entry["properties"] = obj["properties"]
    return entry


def vpn_keep(obj: Dict[str, Any], args, kind: str) -> bool:
    props = obj["properties"]
    if kind == "gateways" and args.type and (props.get("gatewayType") or "").lower() != args.type:
        return False
    if kind == "local-gateways" and args.unused and obj["connections"]:
        return False
    if kind == "connections":
        if args.type and (props.get("connectionType") or "").lower() != args.type:
            return False
        if args.status and (props.get("connectionStatus") or "").lower() != args.status:
            return False
        if args.gateway and not matches_any(obj.get("gateway"), args.gateway):
            return False
        remote = obj.get("remote") or {}
        if args.remote and not (
            matches_any(remote.get("name"), args.remote) or matches_any(remote.get("address"), args.remote)
        ):
            return False
    return True


def render_vpn_table(objects: List[Dict[str, Any]], args, kind: str) -> None:
    cap = None if args.full else CELL_WIDTH
    print_table(sub_head(args) + VPN_HEADERS[kind],
                [sub_cols(o, args) + vpn_row(o, kind, cap) for o in objects])
    for obj in objects:
        if obj.get("error"):
            note(f"{obj['name']}: couldn't be read: {obj['error']}")
    if not args.detail:
        return
    for obj in objects:
        print()
        print(f"{obj['name']}  ({where(obj, args)}, {obj['location']})")
        detail = {k: v for k, v in vpn_json(obj).items()
                  if k not in ("name", "resource_group", "location")}
        for line in yaml_lines(detail, 1):
            print(line)


def sub_head(args) -> List[str]:
    """A leading SUBSCRIPTION column, when the run spans more than one."""
    return ["SUBSCRIPTION"] if args.multi_sub else []


def sub_cols(obj: Dict[str, Any], args) -> List[str]:
    return [obj["subscription"]] if args.multi_sub else []


def where(obj: Dict[str, Any], args) -> str:
    """Where an object lives, for section headings: its group, and sub if several."""
    return f"{obj['subscription']} / {obj['resource_group']}" if args.multi_sub else obj["resource_group"]


# --------------------------------------------- stage 2: resource groups ---

def choose_resource_groups(
    objects: List[Dict[str, Any]], noun: str, wanted: Optional[List[str]]
) -> Optional[List[str]]:
    """Pick resource groups from those the scan actually found.

    Returns glob patterns to keep, or None for every group. Only groups that
    hold something the command lists are offered, as in the app's filter.
    """
    # ARM IDs don't agree on case — the same group can come back as
    # MY-RG on a VM and My-RG on its NSG — so count by the lowered name and
    # show whichever spelling turned up first.
    counts: Dict[str, int] = {}
    spelling: Dict[str, str] = {}
    for obj in objects:
        key = obj["resource_group"].lower()
        spelling.setdefault(key, obj["resource_group"])
        counts[key] = counts.get(key, 0) + 1
    groups = sorted(spelling.values(), key=str.lower)

    if wanted:
        if "*" in wanted:
            return None
        unmatched = [w for w in wanted if not any(matches_any(g, [w]) for g in groups)]
        if unmatched:
            note(f"Note: no {noun} in resource group(s) {', '.join(unmatched)}")
        return wanted

    if not groups:
        return None
    if len(groups) == 1 or not interactive():
        return None

    note(f"Resource groups holding {noun}:")
    for i, group in enumerate(groups, 1):
        note(f"  {i:>3}) {group}  ({counts[group.lower()]})")
    while True:
        answer = ask(
            "Pick resource groups (numbers or names, comma-separated; Enter for all): "
        )
        if not answer or answer == "*":
            return None
        picked, bad = [], []
        for token in answer.replace(",", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(groups):
                picked.append(groups[int(token) - 1])
            elif any(matches_any(g, [token]) for g in groups):
                picked.append(token)
            else:
                bad.append(token)
        if not bad:
            return picked
        note(f"  Not in the list: {', '.join(bad)}")


# ------------------------------------------------------------ rule match ---

def port_matches(ranges: List[str], port: int) -> bool:
    for entry in ranges:
        if entry == "*":
            return True
        low, _, high = entry.partition("-")
        try:
            if int(low) <= port <= int(high or low):
                return True
        except ValueError:
            continue
    return False


def source_matches(sources: List[str], wanted: str) -> bool:
    """Does a rule's source admit ``wanted``?

    ``internet`` (or ``any``, ``*``) means "open to everyone": a source of
    ``*``, ``Internet`` or ``0.0.0.0/0``. An address or CIDR matches any
    source that overlaps it, plus ``*``, plus ``Internet`` when the address is
    public. Anything else is compared as a service tag or ASG name.
    """
    lowered = [s.lower() for s in sources]
    if wanted.lower() in INTERNET_SOURCES:
        return any(s in INTERNET_SOURCES for s in lowered)

    try:
        network = ipaddress.ip_network(wanted, strict=False)
    except ValueError:
        return any(s in ("*", "any", wanted.lower()) for s in lowered)

    for source in lowered:
        if source in ("*", "any"):
            return True
        if source == "internet" and network.is_global:
            return True
        try:
            if ipaddress.ip_network(source, strict=False).overlaps(network):
                return True
        except (ValueError, TypeError):
            continue
    return False


def rule_filter_active(args) -> bool:
    return any([args.port, args.source, args.access, args.proto])


def rule_matches(rule: Dict[str, Any], args) -> bool:
    if rule["default"] and not args.defaults:
        return False
    if args.direction != "any" and rule["direction"].lower() != args.direction + "bound":
        return False
    if args.access and rule["access"].lower() != args.access:
        return False
    if args.proto and rule["protocol"].lower() not in ("*", args.proto):
        return False
    if args.port is not None and not port_matches(rule["destination_ports"], args.port):
        return False
    # Effective rules also carry service tags expanded to prefixes, so an
    # address can match VirtualNetwork or AzureLoadBalancer by what they hold.
    sources = rule["source"] + rule.get("expanded_source", [])
    if args.source and not source_matches(sources, args.source):
        return False
    return True


def shown_rules(nsg: Dict[str, Any], args) -> List[Dict[str, Any]]:
    rules = [r for r in nsg["rules"] if rule_matches(r, args)]
    return sorted(rules, key=lambda r: (r["direction"], r["priority"]))


# --------------------------------------------------------------- filters ---

def keep(obj: Dict[str, Any], args, kind: str, rules_as_written: bool = True) -> bool:
    if args.rg_patterns is not None and not matches_any(obj["resource_group"], args.rg_patterns):
        return False
    if args.name and not (
        matches_any(obj["name"], args.name)
        or (kind == "subnets" and matches_any(obj["path"], args.name))
    ):
        return False
    if kind in VPN_KINDS:
        return vpn_keep(obj, args, kind)

    if kind == "subnets":
        nsg_names = [layer["nsg"]["name"] for layer in obj["layers"]]
        if args.vnet and not matches_any(obj["vnet"], args.vnet):
            return False
        if args.nsg and not any(matches_any(n, args.nsg) for n in nsg_names):
            return False
        if args.no_nsg and nsg_names:
            return False
        if args.route_table and not matches_any(route_table_name(obj), args.route_table):
            return False
        if args.no_udr and obj["route_table_id"]:
            return False
        if rule_filter_active(args) and not any(
            shown_rules(layer["nsg"], args) for layer in obj["layers"]
        ):
            return False

    if kind in ("vms", "nics"):
        nsg_names = [layer["nsg"]["name"] for layer in obj["layers"]]
        subnets = obj["subnets"] if kind == "nics" else [
            s for nic in obj["nics"] for s in nic["subnets"]
        ]
        public = obj["public_ips"] if kind == "nics" else [
            ip for nic in obj["nics"] for ip in nic["public_ips"]
        ]
        if args.nsg and not any(matches_any(n, args.nsg) for n in nsg_names):
            return False
        if args.no_nsg and nsg_names:
            return False
        if args.subnet and not any(
            matches_any(s, args.subnet) or matches_any(s.split("/")[-1], args.subnet)
            for s in subnets
        ):
            return False
        if args.public_ip and not public:
            return False
        if rules_as_written and rule_filter_active(args) and not any(
            shown_rules(layer["nsg"], args) for layer in obj["layers"]
        ):
            return False

    if kind == "vms":
        if args.os and (obj["os_type"] or "").lower() != args.os:
            return False

    if kind == "nics":
        if args.vm and not matches_any(obj["vm_name"], args.vm):
            return False
        if args.unattached and obj["vm_id"]:
            return False

    if kind == "nsgs":
        if args.vm and not any(matches_any(v, args.vm) for v in obj["vms"]):
            return False
        if args.subnet and not any(
            matches_any(s, args.subnet) or matches_any(s.split("/")[-1], args.subnet)
            for s in obj["subnets"]
        ):
            return False
        if args.unused and (obj["subnet_ids"] or obj["nic_ids"]):
            return False
        if rule_filter_active(args) and not shown_rules(obj, args):
            return False

    return True


# ------------------------------------------------------ effective rules ---

# Azure evaluates inbound traffic network manager -> subnet -> NIC, and
# outbound NIC -> subnet; listing rules in that order reads like the path.
LAYER_ORDER = {
    "Inbound": {"network manager": 0, "subnet": 1, "nic": 2},
    "Outbound": {"network manager": 0, "nic": 1, "subnet": 2},
}


def effective_error(error: HttpResponseError) -> str:
    code = getattr(getattr(error, "error", None), "code", None) or ""
    if code == "NicMustBeAttachedToRunningVmToGetEffectiveSecurityGroups":
        return "VM is not running"
    if code in ("AuthorizationFailed", "LinkedAuthorizationFailed"):
        return (
            "this identity lacks Microsoft.Network/networkInterfaces/"
            "effectiveNetworkSecurityGroups/action (Reader doesn't include it)"
        )
    return (getattr(error, "message", None) or str(error)).splitlines()[0]


def fetch_effective(
    ingestor: AzureResourceIngestor,
    subscription_id: str,
    vms: List[Dict[str, Any]],
    subnet_names: Dict[str, str],
) -> None:
    """Attach Azure's effective NSG rules to every NIC of every VM given.

    Stopped VMs are skipped without a call — Azure refuses them anyway. The
    rest run in parallel, since each is a long-running operation of a few
    seconds. Each NIC gets ``effective`` (a list) or ``effective_error``.
    """
    states = ingestor.get_vm_power_states(subscription_id)
    jobs = []
    for vm in vms:
        vm["power_state"] = states.get(vm["id"].lower(), "unknown")
        for nic in vm["nics"]:
            if vm["power_state"] == "running":
                jobs.append(nic)
            else:
                nic["effective_error"] = f"VM is {vm['power_state']}"

    if jobs:
        note(f"Asking Azure for effective rules on {len(jobs)} NIC(s)...")

    def one(nic: Dict[str, Any]) -> None:
        try:
            groups = ingestor.get_effective_security_groups(subscription_id, nic["id"])
        except HttpResponseError as e:
            nic["effective_error"] = effective_error(e)
            return
        for group in groups:
            attached = group.pop("attached_to_id")
            group["attached_to"] = (
                subnet_names.get(attached, attached) if group["layer"] == "subnet"
                else nic["name"] if group["layer"] == "nic" else ""
            )
        nic["effective"] = groups

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(one, jobs))


def effective_rows(groups: List[Dict[str, Any]], args) -> List[Dict[str, Any]]:
    """Every shown rule across a NIC's NSGs, tagged with its NSG, in evaluation order."""
    rows = [
        {"layer": group["layer"], "nsg": group["name"], "rule": rule}
        for group in groups
        for rule in shown_rules(group, args)
    ]
    return sorted(rows, key=lambda r: (
        r["rule"]["direction"],
        LAYER_ORDER.get(r["rule"]["direction"], {}).get(r["layer"], 9),
        r["rule"]["priority"],
    ))


def effective_match(vm: Dict[str, Any], args) -> bool:
    return any(effective_rows(nic.get("effective", []), args) for nic in vm["nics"])


# ---------------------------------------------------------------- output ---

def print_table(headers: List[str], rows: List[List[str]], indent: str = "") -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    for row in [headers, ["-" * w for w in widths]] + rows:
        print(indent + "  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())


# Widest a list cell gets in a table before the rest is summarised as
# "+N more"; --full, JSON and CSV always carry the whole list.
CELL_WIDTH = 40


def join_list(values: List[str], cap: Optional[int] = None) -> str:
    text = ",".join(values)
    if cap is None or len(text) <= cap:
        return text
    shown: List[str] = []
    for value in values:
        if len(",".join(shown + [value])) > cap and shown:
            break
        shown.append(value)
    return ",".join(shown) + f" +{len(values) - len(shown)} more"


def rule_row(rule: Dict[str, Any], cap: Optional[int] = None) -> List[str]:
    return [
        str(rule["priority"]),
        "in" if rule["direction"] == "Inbound" else "out",
        rule["access"].lower(),
        rule["protocol"].lower(),
        join_list(rule["source"], cap),
        join_list(rule["source_ports"], cap),
        join_list(rule["destination"], cap),
        join_list(rule["destination_ports"], cap),
        rule["name"],
    ]


def nsg_cell(layers: List[Dict[str, Any]]) -> str:
    if not layers:
        return "none"
    return ", ".join(f"{l['nsg']['name']} ({l['layer']})" for l in layers)


def route_table_name(subnet: Dict[str, Any]) -> Optional[str]:
    if subnet["route_table"]:
        return subnet["route_table"]["name"]
    if subnet["route_table_id"]:
        return subnet["route_table_id"].split("/")[-1]
    return None


def route_table_cell(subnet: Dict[str, Any]) -> str:
    table = subnet["route_table"]
    if table:
        count = len(table["routes"])
        return f"{table['name']} ({count} route{'' if count == 1 else 's'})"
    if subnet["route_table_id"]:
        return f"{route_table_name(subnet)} (not readable)"
    return "none"


def object_row(obj: Dict[str, Any], kind: str) -> List[str]:
    if kind == "subnets":
        return [
            obj["vnet"],
            obj["name"],
            obj["resource_group"],
            " ".join(obj["address_prefixes"]),
            ", ".join(l["nsg"]["name"] for l in obj["layers"]) or "none",
            route_table_cell(obj),
        ]
    if kind == "vms":
        nics = obj["nics"]
        return [
            obj["name"],
            obj["resource_group"],
            obj["vm_size"] or "",
            obj["os_type"] or "",
            " ".join(ip for n in nics for ip in n["private_ips"]),
            " ".join(ip for n in nics for ip in n["public_ips"]),
            " ".join(p for n in nics for p in n["public_ip_names"]),
            " ".join(s for n in nics for s in n["subnets"]),
            nsg_cell(obj["layers"]),
        ]
    return [
        obj["name"],
        obj["resource_group"],
        obj["vm_name"] or "(unattached)",
        " ".join(obj["private_ips"]),
        " ".join(obj["public_ips"]),
        " ".join(obj["public_ip_names"]),
        " ".join(obj["subnets"]),
        nsg_cell(obj["layers"]),
    ]


OBJECT_HEADERS = {
    "vms": ["NAME", "RESOURCE GROUP", "SIZE", "OS", "PRIVATE IP", "PUBLIC IP", "PUBLIC IP NAME",
            "SUBNET", "NSG"],
    "nics": ["NAME", "RESOURCE GROUP", "VM", "PRIVATE IP", "PUBLIC IP", "PUBLIC IP NAME",
             "SUBNET", "NSG"],
    "subnets": ["VNET", "SUBNET", "RESOURCE GROUP", "PREFIX", "NSG", "ROUTE TABLE"],
}

ROUTE_HEADERS = ["ROUTE", "ADDRESS PREFIX", "NEXT HOP", "NEXT HOP IP"]


def route_row(route: Dict[str, Any]) -> List[str]:
    return [route["name"], route["address_prefix"], route["next_hop_type"], route["next_hop_ip"]]


def print_routes(subnet: Dict[str, Any], indent: str) -> None:
    table = subnet["route_table"]
    if not subnet["route_table_id"]:
        print(f"{indent}no route table; Azure's system routes apply")
        return
    if not table:
        print(f"{indent}route table {route_table_name(subnet)}  (couldn't be read)")
        return
    bgp = "on" if table["bgp_route_propagation"] else "off"
    print(f"{indent}route table {table['name']}  ({table['resource_group']}; BGP route propagation {bgp})")
    if table["routes"]:
        print_table(ROUTE_HEADERS, [route_row(r) for r in table["routes"]], indent + "  ")
    else:
        print(f"{indent}  (no routes)")


def print_rules(nsg: Dict[str, Any], args, indent: str) -> None:
    rules = shown_rules(nsg, args)
    if rules:
        cap = None if args.full else CELL_WIDTH
        print_table(RULE_HEADERS, [rule_row(r, cap) for r in rules], indent)
    elif rule_filter_active(args):
        print(f"{indent}(no matching rules)")
    else:
        print(f"{indent}(no custom rules; --defaults shows Azure's built-in ones)")


def print_list(label: str, values: List[str], indent: str = "  ") -> None:
    """``label: a, b, c``, wrapped under itself when it runs long."""
    if not values:
        return
    head = f"{indent}{label + ':':<13}"
    print(textwrap.fill(
        ", ".join(values), width=100,
        initial_indent=head, subsequent_indent=" " * len(head),
        break_long_words=False, break_on_hyphens=False,
    ))


def print_rules_by_direction(nsg: Dict[str, Any], args, indent: str) -> None:
    """The NSG's rules as an inbound table and an outbound table."""
    rules = shown_rules(nsg, args)
    cap = None if args.full else CELL_WIDTH
    headers = [h for h in RULE_HEADERS if h != "DIR"]
    for direction in ("Inbound", "Outbound"):
        # Both headings always print, so an empty direction reads as empty
        # rather than as forgotten -- unless --direction asked for one side.
        if args.direction not in ("any", direction[:-5].lower()):
            continue
        these = [r for r in rules if r["direction"] == direction]
        if these:
            print(f"{indent}{direction.lower()}:")
            print_table(headers, [
                [c for h, c in zip(RULE_HEADERS, rule_row(r, cap)) if h != "DIR"]
                for r in these
            ], indent + "  ")
        elif rule_filter_active(args):
            print(f"{indent}{direction.lower()}: (no matching rules)")
        elif args.defaults:
            print(f"{indent}{direction.lower()}: (no rules)")
        else:
            print(f"{indent}{direction.lower()}: (no custom rules; --defaults shows Azure's built-in ones)")


def render_effective_table(vms: List[Dict[str, Any]], args) -> None:
    cap = None if args.full else CELL_WIDTH
    for i, vm in enumerate(vms):
        if i:
            print()
        print(f"{vm['name']}  ({where(vm, args)})  [{vm['power_state']}]")
        for nic in vm["nics"]:
            public = f"  public {' '.join(nic['public_ips'])}" if nic["public_ips"] else ""
            if nic["public_ip_names"]:
                public += f" ({' '.join(nic['public_ip_names'])})"
            print(f"  NIC {nic['name']}  on {' '.join(nic['subnets'])}"
                  f"  {' '.join(nic['private_ips'])}{public}")
            if "effective_error" in nic:
                # Fall back to the NSGs' own rules, so a stopped VM still
                # answers the question -- labelled, since Azure didn't vouch
                # for them.
                print(f"    effective rules unavailable: {nic['effective_error']}")
                if not nic["layers"]:
                    print("    no NSG on the NIC or its subnet")
                    continue
                print(f"    rules as written instead: {nsg_cell(nic['layers'])}")
                groups = [
                    {"layer": l["layer"], "name": l["nsg"]["name"], "rules": l["nsg"]["rules"]}
                    for l in nic["layers"]
                ]
            elif not nic["effective"]:
                print("    no NSG applies: nothing filters this NIC's traffic")
                continue
            else:
                groups = nic["effective"]
                layers = ", ".join(f"{g['name']} ({g['layer']})" for g in groups)
                print(f"    in force: {layers}")
            rows = effective_rows(groups, args)
            if rows:
                print_table(
                    ["LAYER", "NSG"] + RULE_HEADERS,
                    [[r["layer"], r["nsg"]] + rule_row(r["rule"], cap) for r in rows],
                    "    ",
                )
            elif rule_filter_active(args):
                print("    (no matching rules)")
            else:
                print("    (only Azure's default rules; --defaults shows them)")


def render_table(objects: List[Dict[str, Any]], args, kind: str) -> None:
    if kind in VPN_KINDS:
        render_vpn_table(objects, args, kind)
        return
    if args.effective:
        render_effective_table(objects, args)
        return
    if kind == "nsgs":
        for i, nsg in enumerate(objects):
            if i:
                print()
            print(f"{nsg['name']}  ({where(nsg, args)}, {nsg['location']})")
            if not nsg["subnets"] and not nsg["nics"]:
                print("  attached to nothing")
            print_list("subnets", nsg["subnets"])
            print_list("interfaces", [
                f"{n['name']} ({n['vm'] or 'unattached'})" for n in nsg["nics"]
            ])
            print_list("VMs covered", nsg["vms"])
            print_rules_by_direction(nsg, args, "  ")
        return

    print_table(sub_head(args) + OBJECT_HEADERS[kind],
                [sub_cols(o, args) + object_row(o, kind) for o in objects])
    if kind == "subnets":
        if not (args.show_rules or args.show_routes):
            return
        for subnet in objects:
            print()
            print(f"{subnet['path']}  ({where(subnet, args)}, {' '.join(subnet['address_prefixes'])})")
            if args.show_routes:
                print_routes(subnet, "  ")
            if args.show_rules:
                if not subnet["layers"]:
                    print("  no NSG")
                for layer in subnet["layers"]:
                    print(f"  NSG {layer['nsg']['name']}")
                    print_rules(layer["nsg"], args, "    ")
        return
    if not args.show_rules:
        return
    for obj in objects:
        print()
        print(f"{obj['name']}  ({where(obj, args)})")
        if not obj["layers"]:
            print("  no NSG on the NIC or the subnet")
        for layer in obj["layers"]:
            print(f"  {layer['layer']} NSG {layer['nsg']['name']}  on {layer['attached_to']}")
            print_rules(layer["nsg"], args, "    ")


def nsg_json(nsg: Dict[str, Any], args) -> Dict[str, Any]:
    rules = shown_rules(nsg, args)

    def side(direction: str) -> List[Dict[str, Any]]:
        # The section names the direction; an empty description is noise.
        return [
            {k: v for k, v in r.items()
             if k != "direction" and not (k == "description" and not v)}
            for r in rules if r["direction"] == direction
        ]

    return {
        "name": nsg["name"],
        "id": nsg["id"],
        "resource_group": nsg["resource_group"],
        "location": nsg["location"],
        "subnets": nsg["subnets"],
        "interfaces": nsg["nics"],
        "vms": nsg["vms"],
        "inbound": side("Inbound"),
        "outbound": side("Outbound"),
    }


def layers_json(layers: List[Dict[str, Any]], args) -> List[Dict[str, Any]]:
    out = []
    for layer in layers:
        entry = {
            "layer": layer["layer"],
            "attached_to": layer["attached_to"],
            "name": layer["nsg"]["name"],
            "id": layer["nsg"]["id"],
        }
        if args.show_rules:
            entry["rules"] = shown_rules(layer["nsg"], args)
        out.append(entry)
    return out


def nic_json(nic: Dict[str, Any], args) -> Dict[str, Any]:
    return {
        "name": nic["name"],
        "id": nic["id"],
        "resource_group": nic["resource_group"],
        "vm": nic["vm_name"],
        "private_ips": nic["private_ips"],
        "public_ips": nic["public_ips"],
        "public_ip_names": nic["public_ip_names"],
        "subnets": nic["subnets"],
        "nsgs": layers_json(nic["layers"], args),
    }


def effective_json(nic: Dict[str, Any], args) -> Dict[str, Any]:
    entry = {k: v for k, v in nic_json(nic, args).items() if k not in ("vm", "nsgs")}
    if "effective_error" in nic:
        entry["effective_error"] = nic["effective_error"]
    else:
        entry["effective"] = [
            {
                "layer": g["layer"],
                "attached_to": g["attached_to"],
                "name": g["name"],
                "id": g["id"],
                "rules": shown_rules(g, args),
            }
            for g in nic["effective"]
        ]
    return entry


def subnet_json(subnet: Dict[str, Any], args) -> Dict[str, Any]:
    nsg = None
    for layer in subnet["layers"]:
        nsg = {"name": layer["nsg"]["name"], "id": layer["nsg"]["id"]}
        if args.show_rules:
            nsg["rules"] = shown_rules(layer["nsg"], args)
    table = subnet["route_table"]
    if table:
        route_table = {k: table[k] for k in
                       ("name", "id", "resource_group", "bgp_route_propagation", "routes")}
    elif subnet["route_table_id"]:
        route_table = {"name": route_table_name(subnet), "id": subnet["route_table_id"],
                       "error": "couldn't be read"}
    else:
        route_table = None
    return {
        "name": subnet["name"],
        "vnet": subnet["vnet"],
        "id": subnet["id"],
        "resource_group": subnet["resource_group"],
        "location": subnet["location"],
        "address_prefixes": subnet["address_prefixes"],
        "nsg": nsg,
        "route_table": route_table,
    }


def structured(objects: List[Dict[str, Any]], args, kind: str) -> List[Dict[str, Any]]:
    """The output as plain data, for JSON and YAML alike."""
    if args.effective:
        data = [{
            "name": vm["name"],
            "id": vm["id"],
            "resource_group": vm["resource_group"],
            "vm_size": vm["vm_size"],
            "os_type": vm["os_type"],
            "power_state": vm["power_state"],
            "nics": [effective_json(n, args) for n in vm["nics"]],
        } for vm in objects]
    elif kind == "nsgs":
        data = [nsg_json(n, args) for n in objects]
    elif kind == "subnets":
        data = [subnet_json(n, args) for n in objects]
    elif kind in VPN_KINDS:
        data = [vpn_json(n) for n in objects]
    elif kind == "nics":
        data = [nic_json(n, args) for n in objects]
    else:
        data = [{
            "name": vm["name"],
            "id": vm["id"],
            "resource_group": vm["resource_group"],
            "vm_size": vm["vm_size"],
            "os_type": vm["os_type"],
            "nics": [
                {k: v for k, v in nic_json(n, args).items() if k not in ("vm", "nsgs")}
                for n in vm["nics"]
            ],
            "nsgs": layers_json(vm["layers"], args),
        } for vm in objects]
    # Name the subscription beside the resource group, always: a file is
    # often merged with others later, and it costs one line per object.
    return [_with_subscription(d, o) for d, o in zip(data, objects)]


def _with_subscription(entry: Dict[str, Any], obj: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in entry.items():
        out[key] = value
        if key == "resource_group":
            out["subscription"] = obj["subscription"]
    return out


def render_json(objects: List[Dict[str, Any]], args, kind: str) -> None:
    json.dump(structured(objects, args, kind), sys.stdout, indent=2)
    print()


# ------------------------------------------------------------------ yaml ---
#
# Written by hand rather than with PyYAML: the data is only strings, numbers,
# booleans, lists and dicts, and a new dependency would mean reinstalling on
# every host that already runs the app.

# Words YAML 1.1 readers turn into booleans or null if left unquoted.
YAML_RESERVED = {"", "~", "null", "true", "false", "yes", "no", "on", "off", "y", "n"}
YAML_PLAIN = re.compile(r"^[A-Za-z0-9_(][A-Za-z0-9_ ./()-]*$")
YAML_NUMBER = re.compile(r"^[-+]?(\d[\d_]*(\.\d*)?|\.\d+)([eE][-+]?\d+)?$|^0[xXoO]")
YAML_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")
YAML_FLOW_WIDTH = 80


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # The SDK hands back some fields (VM sizes, for one) as str-based enums,
    # whose str() is "VirtualMachineSizeTypes.STANDARD_B2_MS"; JSON writes the
    # underlying value, so YAML must too.
    text = str.__str__(value) if isinstance(value, str) else str(value)
    if (
        YAML_PLAIN.match(text) and not text.endswith(" ")
        and text.lower() not in YAML_RESERVED
        and not YAML_NUMBER.match(text) and not YAML_DATE.match(text)
    ):
        return text
    # A JSON string is a valid YAML double-quoted scalar, escapes and all.
    return json.dumps(text, ensure_ascii=False)


def yaml_inline(value: Any) -> Optional[str]:
    """``value`` on one line, or None if it needs a block."""
    if isinstance(value, dict):
        return "{}" if not value else None
    if isinstance(value, list):
        if any(isinstance(v, (dict, list)) for v in value):
            return None
        flow = "[" + ", ".join(yaml_scalar(v) for v in value) + "]"
        return flow if len(flow) <= YAML_FLOW_WIDTH else None
    return yaml_scalar(value)


def yaml_lines(value: Any, indent: int = 0) -> List[str]:
    pad = "  " * indent
    lines: List[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            inline = yaml_inline(item)
            if inline is not None:
                lines.append(f"{pad}{yaml_scalar(key)}: {inline}")
            else:
                lines.append(f"{pad}{yaml_scalar(key)}:")
                lines.extend(yaml_lines(item, indent + 1))
    else:
        for item in value:
            inline = yaml_inline(item)
            if inline is not None:
                lines.append(f"{pad}- {inline}")
            else:
                # The item's block, with its first line moved up beside the dash.
                block = yaml_lines(item, indent + 1)
                lines.append(f"{pad}- {block[0].lstrip()}")
                lines.extend(block[1:])
    return lines


def render_yaml(objects: List[Dict[str, Any]], args, kind: str) -> None:
    data = structured(objects, args, kind)
    print("\n".join(yaml_lines(data)) if data else "[]")


# The reader below parses what yaml_lines writes, and only that: block maps
# and lists two spaces deep, flow lists of scalars, and scalars that are
# either plain or a JSON string. That's enough to read snapshots back without
# a YAML library; a hand-edited file that strays outside it gets a line
# number. Plain text is typed the way yaml_scalar decided to leave it plain:
# anything that would read as a number, boolean or null was quoted.

class YamlError(ValueError):
    pass


def _yaml_value(text: str, line: int) -> Any:
    if text.startswith('"'):
        try:
            value, end = json.JSONDecoder().raw_decode(text)
        except ValueError:
            raise YamlError(f"line {line}: bad quoted string")
        if text[end:].strip():
            raise YamlError(f"line {line}: unexpected text after a string")
        return value
    if text == "{}":
        return {}
    if text.startswith("["):
        return _yaml_flow(text, line)
    if text == "null" or text == "~":
        return None
    if text in ("true", "false"):
        return text == "true"
    if YAML_NUMBER.match(text):
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return text
    return text


def _yaml_flow(text: str, line: int) -> List[Any]:
    if not text.endswith("]"):
        raise YamlError(f"line {line}: unclosed list")
    body, items, i = text[1:-1], [], 0
    while i < len(body):
        while i < len(body) and body[i] == " ":
            i += 1
        if i >= len(body):
            break
        if body[i] == '"':
            try:
                value, end = json.JSONDecoder().raw_decode(body, i)
            except ValueError:
                raise YamlError(f"line {line}: bad quoted string in list")
            items.append(value)
            i = end
        else:
            end = body.find(",", i)
            end = len(body) if end < 0 else end
            items.append(_yaml_value(body[i:end].strip(), line))
            i = end
        while i < len(body) and body[i] == " ":
            i += 1
        if i < len(body):
            if body[i] != ",":
                raise YamlError(f"line {line}: expected ',' in list")
            i += 1
    return items


def _yaml_key(text: str, line: int):
    """Split ``key: rest`` into (key, rest), or None if it isn't a map entry."""
    if text.startswith('"'):
        try:
            key, end = json.JSONDecoder().raw_decode(text)
        except ValueError:
            return None
        rest = text[end:]
    else:
        colon = text.find(":")
        if colon < 0:
            return None
        key, rest = text[:colon], text[colon:]
        # yaml_scalar leaves a key unquoted only when it's plain; anything
        # else with a colon in it (a flow list holding "k: v") is a value.
        if not YAML_PLAIN.match(key):
            return None
    if rest == ":":
        return key, ""
    if rest.startswith(": "):
        return key, rest[2:]
    return None


def yaml_load(text: str) -> Any:
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        stripped = raw.lstrip(" ")
        lines.append([len(raw) - len(stripped), stripped.rstrip(), number])
    if not lines:
        return None
    if len(lines) == 1 and lines[0][0] == 0 and _yaml_key(lines[0][1], 1) is None \
            and not lines[0][1].startswith("- "):
        return _yaml_value(lines[0][1], lines[0][2])

    def block(i: int, indent: int):
        if lines[i][1].startswith("- "):
            out: List[Any] = []
            while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                rest = lines[i][1][2:]
                if rest.startswith("- ") or _yaml_key(rest, lines[i][2]) is not None:
                    # The item's own block, its first line pulled up beside the dash.
                    lines[i] = [indent + 2, rest, lines[i][2]]
                    value, i = block(i, indent + 2)
                else:
                    value, i = _yaml_value(rest, lines[i][2]), i + 1
                out.append(value)
            return out, i
        mapping: Dict[str, Any] = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
            entry = _yaml_key(lines[i][1], lines[i][2])
            if entry is None:
                raise YamlError(f"line {lines[i][2]}: expected 'key: value'")
            key, rest = entry
            if rest:
                mapping[key], i = _yaml_value(rest, lines[i][2]), i + 1
            else:
                if i + 1 >= len(lines) or lines[i + 1][0] <= indent:
                    raise YamlError(f"line {lines[i][2]}: '{key}:' has nothing under it")
                mapping[key], i = block(i + 1, lines[i + 1][0])
        return mapping, i

    value, i = block(0, lines[0][0])
    if i < len(lines):
        raise YamlError(f"line {lines[i][2]}: indentation doesn't match the lines above")
    return value


def render_csv(objects: List[Dict[str, Any]], args, kind: str) -> None:
    """One row per object, or with rules in play, one row per rule."""
    writer = csv.writer(sys.stdout)

    def head(cells: List[str]) -> None:
        writer.writerow(sub_head(args) + cells)

    def row(obj: Dict[str, Any], cells: List[str]) -> None:
        writer.writerow(sub_cols(obj, args) + cells)

    if kind in VPN_KINDS:
        head(VPN_HEADERS[kind])
        for obj in objects:
            row(obj, vpn_row(obj, kind))
        return
    if args.effective:
        head(["VM", "RESOURCE GROUP", "NIC", "PUBLIC IP", "PUBLIC IP NAME", "LAYER", "NSG"] + RULE_HEADERS)
        for vm in objects:
            for nic in vm["nics"]:
                if "effective_error" in nic:
                    note(f"{vm['name']}/{nic['name']}: {nic['effective_error']}")
                for r in effective_rows(nic.get("effective", []), args):
                    row(vm, [vm["name"], vm["resource_group"], nic["name"],
                             " ".join(nic["public_ips"]), " ".join(nic["public_ip_names"]),
                             r["layer"], r["nsg"]] + rule_row(r["rule"]))
        return
    if kind == "nsgs":
        head(["NSG", "RESOURCE GROUP"] + RULE_HEADERS)
        for nsg in objects:
            for rule in shown_rules(nsg, args):
                row(nsg, [nsg["name"], nsg["resource_group"]] + rule_row(rule))
        return

    if kind == "subnets" and args.show_routes:
        # Every subnet gets at least one row, so one without a UDR is
        # still in the sheet rather than silently missing from it.
        head(OBJECT_HEADERS[kind] + ROUTE_HEADERS)
        for subnet in objects:
            cells = object_row(subnet, kind)
            cells[-1] = route_table_name(subnet) or ""
            routes = (subnet["route_table"] or {}).get("routes") or [None]
            for route in routes:
                row(subnet, cells + (route_row(route) if route else [""] * len(ROUTE_HEADERS)))
        return

    if not args.show_rules:
        head(OBJECT_HEADERS[kind])
        for obj in objects:
            row(obj, object_row(obj, kind))
        return

    head([kind[:-1].upper(), "RESOURCE GROUP", "LAYER", "NSG"] + RULE_HEADERS)
    for obj in objects:
        for layer in obj["layers"]:
            for rule in shown_rules(layer["nsg"], args):
                row(obj, [obj.get("path", obj["name"]), obj["resource_group"],
                          layer["layer"], layer["nsg"]["name"]] + rule_row(rule))


# -------------------------------------------------------------- snapshot ---
#
# `azmap export` records what each Azure call returned, per subscription;
# --file plays it back through SnapshotIngestor, so every command, filter and
# output format reads a snapshot exactly as it reads Azure.

SNAPSHOT_FORMAT = 1


def snapshot_subscription(ingestor: AzureResourceIngestor, sub: Dict[str, Any]) -> Dict[str, Any]:
    sub_id = sub["id"]
    public_ips = ingestor.get_public_ips(sub_id)
    return {
        "id": sub_id,
        "name": sub["name"],
        "state": str(sub["state"]).split(".")[-1].capitalize(),
        "public_ips": public_ips,
        "compute": ingestor.get_nics_and_vms(sub_id, public_ips),
        "vnets": ingestor.get_vnets_and_subnets(sub_id),
        "nsgs": ingestor.get_network_security_groups(sub_id),
        "route_tables": ingestor.get_route_tables(sub_id),
        "vpn": ingestor.get_vpn_inventory(sub_id),
    }


class SnapshotIngestor:
    """Answers the CLI's Azure calls from an ``azmap export`` file."""

    def __init__(self, path: str):
        try:
            with open(path, encoding="utf-8-sig") as handle:
                text = handle.read()
        except OSError as e:
            raise SystemExit(f"Can't read {path}: {e.strerror or e}")
        try:
            data = json.loads(text) if text.lstrip().startswith("{") else yaml_load(text)
        except (YamlError, ValueError) as e:
            raise SystemExit(f"{path} isn't a readable snapshot: {e}")
        if not isinstance(data, dict) or "azmap_snapshot" not in data:
            raise SystemExit(
                f"{path} isn't an azmap snapshot. Make one with: azmap export --sub '*'\n"
                f"(a file from -o yaml is a report; it can't be read back.)"
            )
        self.taken = data.get("taken", "?")
        self.version = data.get("azmap_version", "?")
        self.subs = {s["id"]: s for s in data.get("subscriptions") or []}

    def _part(self, sub_id: str, key: str, empty: Any) -> Any:
        # Deep-copied: scan() annotates what it's given, and a snapshot from
        # an older azmap may lack a part a newer one records.
        return copy.deepcopy(self.subs[sub_id].get(key, empty))

    def get_subscriptions(self):
        return [{"id": s["id"], "name": s["name"], "state": s.get("state", "Enabled")}
                for s in self.subs.values()]

    def get_public_ips(self, sub_id):
        return self._part(sub_id, "public_ips", {})

    def get_nics_and_vms(self, sub_id, public_ips=None):
        return self._part(sub_id, "compute", {"nics": [], "vms": {}})

    def get_vnets_and_subnets(self, sub_id):
        return self._part(sub_id, "vnets", {})

    def get_network_security_groups(self, sub_id):
        return self._part(sub_id, "nsgs", [])

    def get_route_tables(self, sub_id):
        return self._part(sub_id, "route_tables", {})

    def get_vpn_inventory(self, sub_id):
        return self._part(sub_id, "vpn", {"gateways": [], "local_gateways": [], "connections": []})


def run_export(args) -> int:
    path = args.out or datetime.now().strftime("azmap-snapshot-%Y%m%d-%H%M.yaml")
    try:
        open(path, "a").close()
    except OSError as e:
        raise SystemExit(out_error(path, e))

    live = AzureResourceIngestor()
    subs = choose_subscriptions(live, args.sub)
    note(f"Exporting {len(subs)} subscription(s)...")

    def one(sub):
        try:
            return snapshot_subscription(live, sub)
        except HttpResponseError as e:
            note(f"  Skipped {sub['name']}: {(e.message or str(e)).splitlines()[0]}")
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        recorded = [r for r in pool.map(one, subs) if r]
    if not recorded:
        raise SystemExit("Every subscription failed to export; nothing written.")

    snapshot = {
        "azmap_snapshot": SNAPSHOT_FORMAT,
        "taken": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "azmap_version": __version__,
        "subscriptions": recorded,
    }
    header = [
        f"# azmap snapshot of {len(recorded)} subscription(s), taken {snapshot['taken']}.",
        "# Read it back with any command plus --file, e.g.:",
        f"#   azmap gateways --file {os.path.basename(path)} --detail",
        f"#   azmap subnets  --file {os.path.basename(path)} --routes",
        "# Pre-shared keys and RADIUS secrets are not included.",
    ]
    try:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(header + yaml_lines(snapshot)) + "\n")
    except OSError as e:
        raise SystemExit(out_error(path, e))

    counts = {
        "VMs": sum(len(r["compute"]["vms"]) for r in recorded),
        "NSGs": sum(len(r["nsgs"]) for r in recorded),
        "subnets": sum(len(v["subnets"]) for r in recorded for v in r["vnets"].values()),
        "gateways": sum(len(r["vpn"]["gateways"]) for r in recorded),
        "local gateways": sum(len(r["vpn"]["local_gateways"]) for r in recorded),
        "connections": sum(len(r["vpn"]["connections"]) for r in recorded),
    }
    note(f"Wrote {path}: " + ", ".join(f"{n} {k}" for k, n in counts.items()))
    return 0


# ------------------------------------------------------------------ main ---

NOUNS = {"vms": "VMs", "nics": "NICs", "nsgs": "NSGs", "subnets": "subnets",
         "gateways": "gateways", "local-gateways": "local gateways",
         "connections": "connections"}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    scope = common.add_argument_group("scope (asked for when left out)")
    scope.add_argument(
        "--sub", metavar="SUB", action="append",
        help="subscription name or ID; repeatable, wildcards allowed, '*' for all",
    )
    scope.add_argument(
        "--rg", metavar="RG", action="append",
        help="resource group; repeatable, wildcards allowed, '*' for all",
    )

    match = common.add_argument_group("filters (different flags AND, a repeated flag ORs)")
    match.add_argument("--name", action="append", metavar="GLOB", help="object name")

    rules = common.add_argument_group(
        "NSG rules (matched as written; inbound unless --direction says otherwise)"
    )
    rules.add_argument("--port", type=int, help="destination port the rule covers")
    rules.add_argument(
        "--from", dest="source", metavar="SOURCE",
        help="source the rule admits: internet, an IP or CIDR, or a service tag",
    )
    rules.add_argument("--access", choices=["allow", "deny"])
    rules.add_argument("--proto", choices=["tcp", "udp", "icmp"])
    rules.add_argument(
        "--direction", choices=["in", "out", "any"],
        help="default: in when filtering rules, otherwise any",
    )
    rules.add_argument(
        "--defaults", action="store_true",
        help="include Azure's built-in default rules",
    )

    common.add_argument("-o", "--output", choices=["table", "json", "yaml", "csv"], default="table")
    common.add_argument(
        "--out", metavar="FILE",
        help="write the output to FILE instead of the screen; use this rather "
             "than '>' for a CSV meant for Excel",
    )
    common.add_argument("--full", action="store_true",
                        help="don't shorten long address and port lists in tables")
    common.add_argument(
        "--file", metavar="SNAPSHOT",
        help="read an 'azmap export' snapshot instead of Azure; no login needed, "
             "and every subscription in it unless --sub narrows it",
    )

    parser = argparse.ArgumentParser(
        prog="azmap",
        description="Query VMs, NICs, NSGs, subnets and VPN gateways with the web app's Azure identity.",
        epilog="Example: azmap vms --port 3389 --from internet --access allow",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    vms = commands.add_parser("vms", parents=[common], help="virtual machines")
    vms.add_argument("--os", type=str.lower, choices=["linux", "windows"])
    vms.add_argument(
        "--effective", action="store_true",
        help="ask Azure for the rules actually in force on each NIC (subnet + NIC "
             "NSGs, as the portal's Effective security rules shows); running VMs "
             "only, a few seconds per NIC, and needs more than Reader",
    )

    nics = commands.add_parser("nics", parents=[common], help="network interfaces")
    nics.add_argument("--vm", action="append", metavar="GLOB", help="attached VM name")
    nics.add_argument("--unattached", action="store_true", help="no VM attached")

    for sub in (vms, nics):
        sub.add_argument("--nsg", action="append", metavar="GLOB", help="NSG on the NIC or subnet")
        sub.add_argument("--no-nsg", action="store_true", help="no NSG at either level")
        sub.add_argument("--public-ip", action="store_true", help="holds a public IP")
        sub.add_argument("--rules", dest="show_rules", action="store_true",
                         help="list the rules of each applicable NSG")

    nsgs = commands.add_parser("nsgs", parents=[common], help="network security groups, with rules")
    nsgs.add_argument("--vm", action="append", metavar="GLOB", help="guards this VM")
    nsgs.add_argument("--unused", action="store_true", help="attached to nothing")

    subnets = commands.add_parser(
        "subnets", parents=[common], help="subnets, with their NSG and route table (UDRs)",
    )
    subnets.add_argument("--vnet", action="append", metavar="GLOB", help="VNet name")
    subnets.add_argument("--nsg", action="append", metavar="GLOB", help="NSG on the subnet")
    subnets.add_argument("--no-nsg", action="store_true", help="no NSG on the subnet")
    subnets.add_argument("--route-table", action="append", metavar="GLOB",
                         help="route table on the subnet")
    subnets.add_argument("--no-udr", action="store_true", help="no route table on the subnet")
    subnets.add_argument("--routes", dest="show_routes", action="store_true",
                         help="list the routes in each subnet's route table")
    subnets.add_argument("--rules", dest="show_rules", action="store_true",
                         help="list the rules of each subnet's NSG")

    gateways = commands.add_parser(
        "gateways", parents=[common], help="VPN and ExpressRoute gateways, with every setting",
    )
    gateways.add_argument("--type", type=str.lower, choices=["vpn", "expressroute"])
    local_gws = commands.add_parser(
        "local-gateways", parents=[common], help="local network gateways (the on-prem side)",
    )
    local_gws.add_argument("--unused", action="store_true", help="no connection uses it")
    connections = commands.add_parser(
        "connections", parents=[common], help="gateway connections (site-to-site, VNet-to-VNet, ExpressRoute)",
    )
    connections.add_argument("--type", type=str.lower,
                             choices=["ipsec", "vnet2vnet", "expressroute", "vpnclient"])
    connections.add_argument("--status", type=str.lower,
                             choices=["connected", "notconnected", "connecting", "unknown"])
    connections.add_argument("--gateway", action="append", metavar="GLOB", help="Azure-side gateway")
    connections.add_argument("--remote", action="append", metavar="GLOB",
                             help="remote end: local gateway, gateway or circuit name, or its address")
    for sub in (gateways, local_gws, connections):
        sub.add_argument("--detail", action="store_true",
                         help="every setting of each, below the table (as -o yaml has)")

    export = commands.add_parser(
        "export", help="save everything to one YAML snapshot, readable later with --file",
    )
    export.add_argument(
        "--sub", metavar="SUB", action="append",
        help="subscription name or ID; repeatable, wildcards allowed, '*' for all",
    )
    export.add_argument("--out", metavar="FILE",
                        help="default: azmap-snapshot-YYYYMMDD-HHMM.yaml here")

    for sub in (vms, nics, nsgs):
        sub.add_argument("--subnet", action="append", metavar="GLOB",
                         help="subnet, as NAME or VNET/NAME")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    kind = args.command
    if kind == "export":
        logging.basicConfig(level=logging.WARNING)
        return run_export(args)
    if args.file and getattr(args, "effective", False):
        parser.error("--effective asks Azure for live rules; a snapshot holds the "
                     "rules as written, which --rules shows")
    if args.direction is None:
        args.direction = "in" if rule_filter_active(args) else "any"
    # A rule filter on VMs or NICs is a question about rules; show the answer.
    args.show_rules = kind == "nsgs" or getattr(args, "show_rules", False) or rule_filter_active(args)
    args.show_routes = getattr(args, "show_routes", False)
    if kind == "subnets" and args.output == "csv" and args.show_rules and args.show_routes:
        parser.error("-o csv takes --routes or NSG rules, not both: one is a row per "
                     "route, the other a row per rule")

    logging.basicConfig(level=logging.WARNING)

    if args.out:
        # Check the file can be written before a scan that may take minutes.
        # Append mode creates it if need be and leaves an existing one intact.
        try:
            open(args.out, "a").close()
        except OSError as e:
            raise SystemExit(out_error(args.out, e))

    if args.file:
        ingestor = SnapshotIngestor(args.file)
        note(f"Snapshot {args.file}: taken {ingestor.taken} by azmap {ingestor.version}")
        subs = (choose_subscriptions(ingestor, args.sub) if args.sub
                else ingestor.get_subscriptions())
    else:
        ingestor = AzureResourceIngestor()
        subs = choose_subscriptions(ingestor, args.sub)
    args.multi_sub = len(subs) > 1
    args.detail = getattr(args, "detail", False)
    inventory = scan_all(ingestor, subs, kind)

    objects = inventory[kind]
    args.rg_patterns = choose_resource_groups(objects, NOUNS[kind], args.rg)

    if _prompted:
        rgs = " ".join(f"--rg '{g}'" for g in args.rg_patterns or ["*"])
        names = " ".join(f"--sub '{s['name']}'" for s in subs)
        note(f"Next time, skip the questions with: {names} {rgs}")
    note()

    args.effective = getattr(args, "effective", False)
    selected = [o for o in objects if keep(o, args, kind, not args.effective)]
    if args.effective:
        by_sub: Dict[str, List[Dict[str, Any]]] = {}
        for vm in selected:
            by_sub.setdefault(vm["subscription_id"], []).append(vm)
        for sub_id, vms_here in by_sub.items():
            fetch_effective(ingestor, sub_id, vms_here, inventory["subnet_names"])
        if rule_filter_active(args):
            unknown = [v["name"] for v in selected if any("effective_error" in n for n in v["nics"])]
            selected = [v for v in selected if effective_match(v, args)]
            if unknown:
                note(f"Couldn't check {len(unknown)} VM(s) without effective rules: {', '.join(unknown)}")
    if args.out:
        # Written here rather than through '>': Windows PowerShell 5.1
        # redirects as UTF-16, which Excel won't split on commas, so every row
        # lands in column A. UTF-8 with a BOM is what Excel recognises, and
        # newline="" leaves the csv module's \r\n alone instead of doubling it.
        encoding = "utf-8-sig" if args.output == "csv" else "utf-8"
        try:
            with open(args.out, "w", encoding=encoding, newline="") as handle, \
                    contextlib.redirect_stdout(handle):
                render(selected, args, kind)
        except OSError as e:
            raise SystemExit(out_error(args.out, e))
        note(f"Wrote {len(selected)} {NOUNS[kind]} to {args.out}")
    else:
        if args.output == "csv":
            # Same doubling on stdout: Windows text mode turns each \r\n
            # into \r\r\n, which Excel shows as a blank row between rows.
            sys.stdout.reconfigure(newline="")
        render(selected, args, kind)
    return 0


def out_error(path: str, error: OSError) -> str:
    if isinstance(error, PermissionError):
        return (f"Can't write {path}: it's open in another program (Excel locks "
                f"files it has open) or the folder is read-only. Close it and rerun.")
    return f"Can't write {path}: {error.strerror or error}"


def render(selected: List[Dict[str, Any]], args, kind: str) -> None:
    if args.output == "json":
        render_json(selected, args, kind)
    elif args.output == "yaml":
        render_yaml(selected, args, kind)
    elif args.output == "csv":
        render_csv(selected, args, kind)
    elif selected:
        render_table(selected, args, kind)
    else:
        note(f"No {NOUNS[kind]} match.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ClientAuthenticationError as e:
        sys.exit(f"Could not authenticate to Azure:\n{e.message}")
    except AzureError as e:
        sys.exit(f"Azure request failed: {e}")
    except (KeyboardInterrupt, EOFError):
        note()
        sys.exit(130)
