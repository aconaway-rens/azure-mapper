"""Claude-powered review of the discovered Azure network topology.

Takes the topology graph the app already builds (VNets, subnets, peerings,
resource groups) and asks Claude to review it for addressing, peering, and
design issues. Returns a validated, structured result — no prose parsing.

The data sent to Claude is whatever the scan produced. Point this at a lab
subscription you own; the topology of a customer environment is customer data
and must not be sent to a third-party API without the appropriate agreement.
"""

import json
import logging
import os
from typing import Any, Dict, List, Literal

import anthropic
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Opus 4.8 — strong reasoning for architecture review. Adaptive thinking lets
# the model decide how much to reason per request; no token budget to tune.
MODEL = "claude-opus-4-8"


class Finding(BaseModel):
    """A single observation about the topology."""

    severity: Literal["high", "medium", "low", "info"]
    category: Literal["addressing", "peering", "design", "naming", "other"]
    title: str
    affected_resources: List[str]
    finding: str
    recommendation: str


class Analysis(BaseModel):
    """The full review returned to the frontend."""

    summary: str
    findings: List[Finding]


SYSTEM_PROMPT = """\
You are a senior Azure network architect reviewing a network topology.

You will be given a JSON graph discovered from an Azure subscription. It contains:
- resource_group nodes
- vnet nodes (with address_prefixes and location)
- subnet nodes (with address_prefix / CIDR)
- edges of type "peered_to" between VNets (with peering_state)

Review the topology and report concrete issues a network engineer would care about:
- addressing: overlapping or adjacent/overlapping CIDR ranges across VNets or
  subnets, wasteful or inconsistent prefix sizing, address space that can't peer
  cleanly because of overlap.
- peering: peerings in a non-"Connected"/"Initiated" state, one-way peerings,
  hub-and-spoke gaps (spokes that should reach a hub but don't), assumptions
  about transitive routing (Azure VNet peering is NOT transitive by default).
- design: VNets with no subnets, single points of connectivity, isolated VNets
  that look like they were meant to be connected.
- naming: inconsistent or unclear naming/resource-group organization.

CRITICAL GROUNDING RULES — accuracy matters more than thoroughness:
- Reason ONLY from the data in the graph. Do NOT invent or assume the presence
  or absence of NSGs, firewalls, route tables, gateways, NAT, or any resource
  type that is not present in the input. If the data can't support a finding,
  don't make it.
- If you are unsure whether something is a real problem, mark it "info" and say
  what additional data (e.g. NSG rules, route tables) would be needed to confirm.
- Severity: "high" = likely outage, security exposure, or broken connectivity;
  "medium" = real problem worth fixing; "low" = minor/cleanup; "info" =
  observation or something needing more data to judge.
- If the topology is clean, return an empty findings list and say so in summary.

Write the summary as 1-3 plain sentences a busy engineer can read at a glance.
"""


def analyze_topology(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Send the topology graph to Claude and return a structured review.

    Args:
        graph: the dict produced by TopologyGraph.to_dict() — {"nodes": {...},
            "edges": {...}}.

    Returns:
        A dict with "summary" (str) and "findings" (list of finding dicts).

    Raises:
        ValueError: if ANTHROPIC_API_KEY is not configured.
        RuntimeError: if Claude declines the request or returns no structured output.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Add it to app/.env "
            "(get a key from https://console.anthropic.com)."
        )

    # Reads ANTHROPIC_API_KEY from the environment.
    client = anthropic.Anthropic()

    node_count = len(graph.get("nodes", {}))
    edge_count = len(graph.get("edges", {}))
    logger.info(
        "Analyzing topology with Claude: %d nodes, %d edges", node_count, edge_count
    )

    user_content = (
        "Review this Azure network topology graph and report findings.\n\n"
        "```json\n" + json.dumps(graph, indent=2) + "\n```"
    )

    # messages.parse() validates the response against the Pydantic model for us,
    # so we get a typed object back instead of hand-parsing JSON.
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        output_format=Analysis,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to analyze this request.")

    if response.parsed_output is None:
        raise RuntimeError("Claude returned no structured output.")

    logger.info("Analysis complete: %d findings", len(response.parsed_output.findings))
    return response.parsed_output.model_dump()
