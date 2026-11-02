"""Command-line queries over the same Azure objects the app draws.

    python app/azmap.py vms   [--sub SUB] [--rg RG ...] [filters]
    python app/azmap.py nics  [--sub SUB] [--rg RG ...] [filters]
    python app/azmap.py nsgs  [--sub SUB] [--rg RG ...] [filters]

Authentication is the app's own: DefaultAzureCredential, through azure_ingest,
so whatever identity the web app resolves on this host, the CLI resolves too.

Filtering runs in the app's two stages. The subscription is scanned once, then
resource groups are picked from what that scan found, locally. Leave out --sub
or --rg on a terminal and the CLI asks for them, listing the choices.

NSG rule filters (--port, --from, ...) match rules as written. They answer
"which rules allow this?", not "is this reachable?" — a higher-priority deny,
or the other layer's NSG, can still block what a matching rule allows.
"""

import argparse
import csv
import fnmatch
import ipaddress
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError

from azure_ingest import AzureResourceIngestor

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

def scan(ingestor: AzureResourceIngestor, subscription_id: str) -> Dict[str, Any]:
    """Fetch and join VMs, NICs, subnets and NSGs for one subscription.

    Every NIC and VM comes back with the NSGs that apply to it, by layer: the
    NSG on the NIC itself, and the NSG on each subnet it sits in. Traffic has
    to pass both, so both are listed.
    """
    public_ips = ingestor.get_public_ips(subscription_id)
    compute = ingestor.get_nics_and_vms(subscription_id, public_ips)
    vnets = ingestor.get_vnets_and_subnets(subscription_id)
    nsgs = ingestor.get_network_security_groups(subscription_id)

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
            "subnet_ids": [],
        })
        if record["private_ip"]:
            nic["private_ips"].append(record["private_ip"])
        if record["public_ip"]:
            nic["public_ips"].append(record["public_ip"])
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
            nsg_on_nic[key]["nics"].append(nic["name"])
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

    by_name = lambda o: (o["resource_group"].lower(), o["name"].lower())
    return {
        "vms": sorted(vms, key=by_name),
        "nics": sorted(nics.values(), key=by_name),
        "nsgs": sorted(nsgs, key=by_name),
        "subnet_names": subnet_names,
    }


# ------------------------------------------------------ stage 1: the sub ---

def choose_subscription(
    ingestor: AzureResourceIngestor, wanted: Optional[str]
) -> Dict[str, Any]:
    subscriptions = sorted(
        ingestor.get_subscriptions(), key=lambda s: (s["name"] or "").lower()
    )
    if not subscriptions:
        raise SystemExit(
            "This identity can see no subscriptions. It authenticated, so it "
            "most likely lacks the Reader role."
        )

    if wanted:
        for sub in subscriptions:
            if wanted.lower() in (sub["id"].lower(), (sub["name"] or "").lower()):
                return sub
        listing = "\n".join(f"  {s['name']}  ({s['id']})" for s in subscriptions)
        raise SystemExit(f"No subscription matches '{wanted}'. Available:\n{listing}")

    if len(subscriptions) == 1:
        note(f"Subscription: {subscriptions[0]['name']} (the only one visible)")
        return subscriptions[0]

    if not interactive():
        listing = "\n".join(f"  {s['name']}  ({s['id']})" for s in subscriptions)
        raise SystemExit(f"Pass --sub; this identity sees several:\n{listing}")

    note("Subscriptions:")
    for i, sub in enumerate(subscriptions, 1):
        state = "" if str(sub["state"]).endswith(("Enabled", "ENABLED")) else f"  [{sub['state']}]"
        note(f"  {i:>3}) {sub['name']}  ({sub['id']}){state}")
    while True:
        answer = ask("Pick a subscription (number or name): ")
        if answer.isdigit() and 1 <= int(answer) <= len(subscriptions):
            return subscriptions[int(answer) - 1]
        for sub in subscriptions:
            if answer.lower() in (sub["id"].lower(), (sub["name"] or "").lower()):
                return sub
        note(f"  '{answer}' isn't one of those.")


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
    if args.name and not matches_any(obj["name"], args.name):
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


def object_row(obj: Dict[str, Any], kind: str) -> List[str]:
    if kind == "vms":
        nics = obj["nics"]
        return [
            obj["name"],
            obj["resource_group"],
            obj["vm_size"] or "",
            obj["os_type"] or "",
            " ".join(ip for n in nics for ip in n["private_ips"]),
            " ".join(ip for n in nics for ip in n["public_ips"]),
            " ".join(s for n in nics for s in n["subnets"]),
            nsg_cell(obj["layers"]),
        ]
    return [
        obj["name"],
        obj["resource_group"],
        obj["vm_name"] or "(unattached)",
        " ".join(obj["private_ips"]),
        " ".join(obj["public_ips"]),
        " ".join(obj["subnets"]),
        nsg_cell(obj["layers"]),
    ]


OBJECT_HEADERS = {
    "vms": ["NAME", "RESOURCE GROUP", "SIZE", "OS", "PRIVATE IP", "PUBLIC IP", "SUBNET", "NSG"],
    "nics": ["NAME", "RESOURCE GROUP", "VM", "PRIVATE IP", "PUBLIC IP", "SUBNET", "NSG"],
}


def print_rules(nsg: Dict[str, Any], args, indent: str) -> None:
    rules = shown_rules(nsg, args)
    if rules:
        cap = None if args.full else CELL_WIDTH
        print_table(RULE_HEADERS, [rule_row(r, cap) for r in rules], indent)
    elif rule_filter_active(args):
        print(f"{indent}(no matching rules)")
    else:
        print(f"{indent}(no custom rules; --defaults shows Azure's built-in ones)")


def render_effective_table(vms: List[Dict[str, Any]], args) -> None:
    cap = None if args.full else CELL_WIDTH
    for i, vm in enumerate(vms):
        if i:
            print()
        ips = " ".join(ip for n in vm["nics"] for ip in n["private_ips"])
        print(f"{vm['name']}  ({vm['resource_group']})  {ips}  [{vm['power_state']}]")
        for nic in vm["nics"]:
            print(f"  NIC {nic['name']}  on {' '.join(nic['subnets'])}")
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
    if args.effective:
        render_effective_table(objects, args)
        return
    if kind == "nsgs":
        for i, nsg in enumerate(objects):
            if i:
                print()
            print(f"{nsg['name']}  ({nsg['resource_group']}, {nsg['location']})")
            attached = [f"subnet {s}" for s in nsg["subnets"]] + [
                f"nic {n}" for n in nsg["nics"]
            ]
            print(f"  attached to: {', '.join(attached) or 'nothing'}")
            if nsg["vms"]:
                print(f"  guards VMs:  {', '.join(nsg['vms'])}")
            print_rules(nsg, args, "  ")
        return

    print_table(OBJECT_HEADERS[kind], [object_row(o, kind) for o in objects])
    if not args.show_rules:
        return
    for obj in objects:
        print()
        print(f"{obj['name']}  ({obj['resource_group']})")
        if not obj["layers"]:
            print("  no NSG on the NIC or the subnet")
        for layer in obj["layers"]:
            print(f"  {layer['layer']} NSG {layer['nsg']['name']}  on {layer['attached_to']}")
            print_rules(layer["nsg"], args, "    ")


def nsg_json(nsg: Dict[str, Any], args) -> Dict[str, Any]:
    return {
        "name": nsg["name"],
        "id": nsg["id"],
        "resource_group": nsg["resource_group"],
        "location": nsg["location"],
        "subnets": nsg["subnets"],
        "nics": nsg["nics"],
        "vms": nsg["vms"],
        "rules": shown_rules(nsg, args),
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


def render_json(objects: List[Dict[str, Any]], args, kind: str) -> None:
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
    json.dump(data, sys.stdout, indent=2)
    print()


def render_csv(objects: List[Dict[str, Any]], args, kind: str) -> None:
    """One row per object, or with rules in play, one row per rule."""
    writer = csv.writer(sys.stdout)
    if args.effective:
        writer.writerow(["VM", "RESOURCE GROUP", "NIC", "LAYER", "NSG"] + RULE_HEADERS)
        for vm in objects:
            for nic in vm["nics"]:
                if "effective_error" in nic:
                    note(f"{vm['name']}/{nic['name']}: {nic['effective_error']}")
                for r in effective_rows(nic.get("effective", []), args):
                    writer.writerow(
                        [vm["name"], vm["resource_group"], nic["name"], r["layer"], r["nsg"]]
                        + rule_row(r["rule"])
                    )
        return
    if kind == "nsgs":
        writer.writerow(["NSG", "RESOURCE GROUP"] + RULE_HEADERS)
        for nsg in objects:
            for rule in shown_rules(nsg, args):
                writer.writerow([nsg["name"], nsg["resource_group"]] + rule_row(rule))
        return

    if not args.show_rules:
        writer.writerow(OBJECT_HEADERS[kind])
        for obj in objects:
            writer.writerow(object_row(obj, kind))
        return

    writer.writerow([kind[:-1].upper(), "RESOURCE GROUP", "LAYER", "NSG"] + RULE_HEADERS)
    for obj in objects:
        for layer in obj["layers"]:
            for rule in shown_rules(layer["nsg"], args):
                writer.writerow(
                    [obj["name"], obj["resource_group"], layer["layer"], layer["nsg"]["name"]]
                    + rule_row(rule)
                )


# ------------------------------------------------------------------ main ---

NOUNS = {"vms": "VMs", "nics": "NICs", "nsgs": "NSGs"}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    scope = common.add_argument_group("scope (asked for when left out)")
    scope.add_argument("--sub", metavar="SUB", help="subscription name or ID")
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

    common.add_argument("-o", "--output", choices=["table", "json", "csv"], default="table")
    common.add_argument("--full", action="store_true",
                        help="don't shorten long address and port lists in tables")

    parser = argparse.ArgumentParser(
        prog="azmap",
        description="Query VMs, NICs and NSGs with the web app's Azure identity.",
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

    for sub in (vms, nics, nsgs):
        sub.add_argument("--subnet", action="append", metavar="GLOB",
                         help="subnet, as NAME or VNET/NAME")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    kind = args.command
    if args.direction is None:
        args.direction = "in" if rule_filter_active(args) else "any"
    # A rule filter on VMs or NICs is a question about rules; show the answer.
    args.show_rules = kind == "nsgs" or getattr(args, "show_rules", False) or rule_filter_active(args)

    logging.basicConfig(level=logging.WARNING)

    ingestor = AzureResourceIngestor()
    sub = choose_subscription(ingestor, args.sub)
    note(f"Scanning {sub['name']}...")
    inventory = scan(ingestor, sub["id"])

    objects = inventory[kind]
    args.rg_patterns = choose_resource_groups(objects, NOUNS[kind], args.rg)

    if _prompted:
        rgs = " ".join(f"--rg '{g}'" for g in args.rg_patterns or ["*"])
        note(f"Next time, skip the questions with: --sub '{sub['name']}' {rgs}")
    note()

    args.effective = getattr(args, "effective", False)
    selected = [o for o in objects if keep(o, args, kind, not args.effective)]
    if args.effective:
        fetch_effective(ingestor, sub["id"], selected, inventory["subnet_names"])
        if rule_filter_active(args):
            unknown = [v["name"] for v in selected if any("effective_error" in n for n in v["nics"])]
            selected = [v for v in selected if effective_match(v, args)]
            if unknown:
                note(f"Couldn't check {len(unknown)} VM(s) without effective rules: {', '.join(unknown)}")
    if args.output == "json":
        render_json(selected, args, kind)
    elif args.output == "csv":
        render_csv(selected, args, kind)
    elif selected:
        render_table(selected, args, kind)
    else:
        note(f"No {NOUNS[kind]} match.")
    return 0


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
