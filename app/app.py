"""Flask app for Azure resource visualization."""

import logging
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from azure_ingest import AzureResourceIngestor
from graph_builder import TopologyGraph

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Global state (in-memory)
current_graph = None
current_subscription_id = None


def get_token_from_request():
    """Extract Bearer token from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def get_ingestor():
    """Create an AzureResourceIngestor from the request's Bearer token."""
    token = get_token_from_request()
    if not token:
        return None
    return AzureResourceIngestor(token)


@app.route("/", methods=["GET"])
def index():
    """Serve the main UI."""
    return render_template("index.html")


@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "graph_available": current_graph is not None,
    })


@app.route("/api/subscriptions", methods=["GET"])
def get_subscriptions():
    """List available Azure subscriptions."""
    ingestor = get_ingestor()
    if ingestor is None:
        return jsonify({"error": "Authorization header with Bearer token required"}), 401

    try:
        subscriptions = ingestor.get_subscriptions()
        return jsonify({"subscriptions": subscriptions})
    except Exception as e:
        logger.error(f"Error fetching subscriptions: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan", methods=["POST"])
def scan_subscription():
    """Trigger a resource scan for a subscription."""
    global current_graph, current_subscription_id

    ingestor = get_ingestor()
    if ingestor is None:
        return jsonify({"error": "Authorization header with Bearer token required"}), 401

    data = request.get_json() or {}
    subscription_id = data.get("subscription_id")

    if not subscription_id:
        return jsonify({"error": "subscription_id required"}), 400

    try:
        logger.info(f"Starting scan for subscription: {subscription_id}")

        # Fetch data from Azure
        scan_data = ingestor.scan_subscription(subscription_id)

        # Build graph
        current_graph = TopologyGraph()
        current_graph.build_from_azure_scan(scan_data)
        current_subscription_id = subscription_id

        logger.info(f"Scan complete: {len(current_graph.nodes)} nodes, {len(current_graph.edges)} edges")

        return jsonify({
            "status": "success",
            "subscription_id": subscription_id,
            "nodes_count": len(current_graph.nodes),
            "edges_count": len(current_graph.edges),
        })
    except Exception as e:
        logger.error(f"Error scanning subscription: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/graph", methods=["GET"])
def get_graph():
    """Get the current graph in Cytoscape format."""
    if current_graph is None:
        return jsonify({"error": "No scan data available. Run /api/scan first."}), 400

    try:
        cytoscape_data = current_graph.to_cytoscape_format()
        return jsonify({
            "subscription_id": current_subscription_id,
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
            "graph": graph_data,
        })
    except Exception as e:
        logger.error(f"Error fetching graph JSON: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/subnet/resources", methods=["POST"])
def get_subnet_resources():
    """Fetch NICs and VMs for a specific subnet."""
    ingestor = get_ingestor()
    if ingestor is None:
        return jsonify({"error": "Authorization header with Bearer token required"}), 401

    data = request.get_json() or {}
    subscription_id = data.get("subscription_id")
    subnet_azure_id = data.get("subnet_azure_id")

    if not subscription_id or not subnet_azure_id:
        return jsonify({"error": "subscription_id and subnet_azure_id required"}), 400

    try:
        logger.info(f"Fetching resources for subnet: {subnet_azure_id}")
        resources = ingestor.get_subnet_resources(subscription_id, subnet_azure_id)
        return jsonify(resources)
    except Exception as e:
        logger.error(f"Error fetching subnet resources: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/graph/clear", methods=["POST"])
def clear_graph():
    """Clear the current graph."""
    global current_graph, current_subscription_id
    current_graph = None
    current_subscription_id = None
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
    import os
    debug = os.getenv("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=8080, debug=debug)
