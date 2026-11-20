"""Flask app for Azure resource visualization.

The app authenticates to Azure with the host's own identity (managed identity,
`az login` session, or service-principal env vars) via DefaultAzureCredential.
There is no per-user sign-in: anyone who can reach this app's port acts as the
host identity, so bind it to localhost or a trusted network.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request

from azure_ingest import AzureResourceIngestor, describe_identity
from graph_builder import TopologyGraph
from version import __version__

# Load app/.env for local runs (Docker injects these via env_file).
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Global state (in-memory)
current_graph = None
current_subscription_id = None
current_subscription_ids = []
_ingestor = None


def get_ingestor():
    """Return the process-wide ingestor, built on the ambient credential."""
    global _ingestor
    if _ingestor is None:
        _ingestor = AzureResourceIngestor()
    return _ingestor


@app.route("/", methods=["GET"])
def index():
    """Serve the main UI."""
    return render_template("index.html", version=__version__)


@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "version": __version__,
        "graph_available": current_graph is not None,
    })


@app.route("/api/identity", methods=["GET"])
def identity():
    """Report which Azure identity the app is running as.

    Called by the UI on load so the operator can confirm the host credential
    resolved before scanning anything.
    """
    try:
        return jsonify(describe_identity())
    except Exception as e:
        # azure-identity's message lists every source it tried, which is the
        # most useful thing to show when a fresh deployment can't authenticate.
        logger.error(f"Could not acquire an Azure token: {e}")
        return jsonify({"authenticated": False, "error": str(e)}), 503


@app.route("/api/subscriptions", methods=["GET"])
def get_subscriptions():
    """List available Azure subscriptions."""
    try:
        subscriptions = get_ingestor().get_subscriptions()
        return jsonify({"subscriptions": subscriptions})
    except Exception as e:
        logger.error(f"Error fetching subscriptions: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan", methods=["POST"])
def scan_subscription():
    """Scan one or more subscriptions into a single graph.

    Takes ``subscription_ids`` (a list) or, as before, ``subscription_id``.
    Subscriptions are scanned in parallel; one that fails is reported in
    ``skipped`` and left out, so a single subscription without access
    doesn't sink a scan of ten. Peerings between the scanned subscriptions
    join their real VNets.
    """
    global current_graph, current_subscription_id, current_subscription_ids

    data = request.get_json() or {}
    ids = data.get("subscription_ids") or (
        [data["subscription_id"]] if data.get("subscription_id") else []
    )
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return jsonify({"error": "subscription_ids required"}), 400

    ingestor = get_ingestor()
    try:
        # Names label each subscription's box, and name the subscription
        # behind a peering to one that wasn't scanned.
        names = {s["id"].lower(): s["name"] for s in ingestor.get_subscriptions()}
    except Exception as e:
        logger.warning(f"Couldn't list subscription names: {e}")
        names = {}

    def one(sub_id):
        try:
            return sub_id, ingestor.scan_subscription(sub_id, names.get(sub_id.lower())), None
        except Exception as e:
            logger.error(f"Error scanning subscription {sub_id}: {e}")
            return sub_id, None, str(e).splitlines()[0] if str(e) else type(e).__name__

    logger.info(f"Starting scan of {len(ids)} subscription(s)")
    with ThreadPoolExecutor(max_workers=min(len(ids), 4)) as pool:
        results = list(pool.map(one, ids))

    scans = [scan for _, scan, _ in results if scan]
    skipped = [
        {"subscription_id": sub_id, "name": names.get(sub_id.lower(), sub_id), "error": err}
        for sub_id, scan, err in results if not scan
    ]
    if not scans:
        return jsonify({"error": "; ".join(f"{s['name']}: {s['error']}" for s in skipped)}), 500

    try:
        current_graph = TopologyGraph()
        current_graph.build_from_azure_scans(scans, names)
    except Exception as e:
        logger.error(f"Error building graph: {e}")
        return jsonify({"error": str(e)}), 500
    current_subscription_ids = [s["subscription_id"] for s in scans]
    current_subscription_id = current_subscription_ids[0]

    logger.info(f"Scan complete: {len(current_graph.nodes)} nodes, {len(current_graph.edges)} edges")
    return jsonify({
        "status": "success",
        "subscription_id": current_subscription_id,
        "subscription_ids": current_subscription_ids,
        "skipped": skipped,
        "nodes_count": len(current_graph.nodes),
        "edges_count": len(current_graph.edges),
    })


@app.route("/api/graph", methods=["GET"])
def get_graph():
    """Get the current graph in Cytoscape format."""
    if current_graph is None:
        return jsonify({"error": "No scan data available. Run /api/scan first."}), 400

    try:
        cytoscape_data = current_graph.to_cytoscape_format()
        return jsonify({
            "subscription_id": current_subscription_id,
            "subscription_ids": current_subscription_ids,
            "graph": cytoscape_data,
        })
    except Exception as e:
        logger.error(f"Error fetching graph: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/graph/json", methods=["GET"])
def get_graph_json():
    """Get the current graph as raw JSON."""
    if current_graph is None:
        return jsonify({"error": "No scan data available. Run /api/scan first."}), 400

    try:
        graph_data = current_graph.to_dict()
        return jsonify({
            "subscription_id": current_subscription_id,
            "subscription_ids": current_subscription_ids,
            "graph": graph_data,
        })
    except Exception as e:
        logger.error(f"Error fetching graph JSON: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/subnet/resources", methods=["POST"])
def get_subnet_resources():
    """Fetch NICs and VMs for a specific subnet."""
    data = request.get_json() or {}
    subscription_id = data.get("subscription_id")
    subnet_azure_id = data.get("subnet_azure_id")

    if not subscription_id or not subnet_azure_id:
        return jsonify({"error": "subscription_id and subnet_azure_id required"}), 400

    try:
        logger.info(f"Fetching resources for subnet: {subnet_azure_id}")
        resources = get_ingestor().get_subnet_resources(
            subscription_id, subnet_azure_id
        )
        return jsonify(resources)
    except Exception as e:
        logger.error(f"Error fetching subnet resources: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/graph/clear", methods=["POST"])
def clear_graph():
    """Clear the current graph."""
    global current_graph, current_subscription_id, current_subscription_ids
    current_graph = None
    current_subscription_id = None
    current_subscription_ids = []
    return jsonify({"status": "graph cleared"})


@app.errorhandler(404)
def not_found(e):
    """Handle 404 errors."""
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    """Handle 500 errors."""
    logger.error(f"Server error: {e}")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    debug = os.getenv("FLASK_ENV") == "development"
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8080")),
        debug=debug,
    )
