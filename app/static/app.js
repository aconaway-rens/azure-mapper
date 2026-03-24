/**
 * Frontend app for Azure Mapper visualization
 */

let cy = null;
let currentGraph = null;
let msalInstance = null;

// MSAL configuration
const msalConfig = {
    auth: {
        clientId: '18ea91ca-8997-483d-a9d3-72da14f6a69b',
        authority: 'https://login.microsoftonline.com/common',
        redirectUri: window.location.origin,
    },
    cache: {
        cacheLocation: 'sessionStorage',
    },
};

const loginRequest = {
    scopes: ['https://management.azure.com/user_impersonation'],
};

/**
 * Get an access token for Azure Management API
 */
async function getAccessToken() {
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length === 0) {
        throw new Error('Not signed in');
    }

    try {
        const response = await msalInstance.acquireTokenSilent({
            ...loginRequest,
            account: accounts[0],
        });
        return response.accessToken;
    } catch (e) {
        // Silent token acquisition failed — try interactive
        const response = await msalInstance.acquireTokenPopup(loginRequest);
        return response.accessToken;
    }
}

/**
 * Make an authenticated fetch request
 */
async function authFetch(url, options = {}) {
    const token = await getAccessToken();
    const headers = {
        ...options.headers,
        'Authorization': `Bearer ${token}`,
    };
    return fetch(url, { ...options, headers });
}

/**
 * Sign in with Microsoft
 */
async function signIn() {
    try {
        showStatus('authStatus', 'Signing in...', 'loading');
        const response = await msalInstance.loginPopup(loginRequest);
        updateAuthUI(response.account);
        showStatus('authStatus', 'Signed in', 'success');
    } catch (e) {
        showStatus('authStatus', `Sign in failed: ${e.message}`, 'error');
    }
}

/**
 * Sign out
 */
async function signOut() {
    await msalInstance.logoutPopup();
    updateAuthUI(null);
    showStatus('authStatus', 'Not signed in', 'info');
}

/**
 * Update UI to reflect auth state
 */
function updateAuthUI(account) {
    const authBtn = document.getElementById('authBtn');
    const userName = document.getElementById('userName');

    if (account) {
        authBtn.textContent = 'Sign Out';
        authBtn.onclick = signOut;
        userName.textContent = account.username;
        userName.style.display = 'block';
    } else {
        authBtn.textContent = 'Sign In with Microsoft';
        authBtn.onclick = signIn;
        userName.style.display = 'none';
    }
}

/**
 * Initialize Cytoscape container
 */
function initializeCytoscape() {
    if (cy) return;

    cy = cytoscape({
        container: document.getElementById('cy'),
        style: [
            // Default node style
            {
                selector: 'node',
                style: {
                    'label': 'data(label)',
                    'font-size': 10,
                    'color': '#333',
                    'text-valign': 'center',
                    'text-halign': 'center',
                }
            },
            // Resource group: outermost rounded rectangle
            {
                selector: 'node[type="resource_group"]',
                style: {
                    'shape': 'roundrectangle',
                    'background-color': '#f0f0f0',
                    'border-color': '#a0a0a0',
                    'border-width': 2,
                    'border-style': 'dashed',
                    'text-valign': 'top',
                    'text-halign': 'center',
                    'text-margin-y': -8,
                    'font-size': 12,
                    'font-weight': 'bold',
                    'color': '#555',
                    'padding': '30px',
                    'background-opacity': 0.3,
                }
            },
            // VNet: rounded rectangle inside resource group
            {
                selector: 'node[type="vnet"]',
                style: {
                    'shape': 'roundrectangle',
                    'background-color': '#e6f2ff',
                    'border-color': '#0078d4',
                    'border-width': 2,
                    'border-style': 'dashed',
                    'text-valign': 'top',
                    'text-halign': 'center',
                    'text-margin-y': -8,
                    'font-size': 11,
                    'font-weight': 'bold',
                    'color': '#0078d4',
                    'padding': '20px',
                    'background-opacity': 0.4,
                    'text-wrap': 'wrap',
                    'text-max-width': '200px',
                }
            },
            // Subnet: solid rounded rectangle inside VNet
            {
                selector: 'node[type="subnet"]',
                style: {
                    'shape': 'roundrectangle',
                    'background-color': '#50e6ff',
                    'border-color': '#0078d4',
                    'border-width': 1,
                    'width': 140,
                    'height': 45,
                    'font-size': 9,
                    'color': '#000',
                    'text-wrap': 'wrap',
                    'text-max-width': '130px',
                }
            },
            // Edges
            {
                selector: 'edge',
                style: {
                    'line-color': '#999',
                    'target-arrow-color': '#999',
                    'target-arrow-shape': 'triangle',
                    'width': 2,
                    'curve-style': 'bezier',
                }
            },
            // Peering edges
            {
                selector: 'edge[type="peered_to"]',
                style: {
                    'line-color': '#107c10',
                    'target-arrow-color': '#107c10',
                    'line-style': 'dashed',
                    'width': 2,
                    'label': 'peering',
                    'font-size': 8,
                    'color': '#107c10',
                    'text-rotation': 'autorotate',
                }
            },
            // Selected state
            {
                selector: ':selected',
                style: {
                    'border-color': '#ff6b6b',
                    'border-width': 3,
                    'line-color': '#ff6b6b',
                    'target-arrow-color': '#ff6b6b',
                }
            }
        ],
        layout: { name: 'preset' }
    });

    // Add click handler for node inspection
    cy.on('tap', 'node', function(evt) {
        const node = evt.target;
        console.log('Node selected:', node.data());
    });
}

/**
 * Display status message
 */
function showStatus(elementId, message, type = 'info') {
    const el = document.getElementById(elementId);
    el.textContent = message;
    el.className = `status ${type}`;
    el.style.display = 'block';
}

/**
 * Load available subscriptions
 */
async function loadSubscriptions() {
    showStatus('subStatus', 'Loading subscriptions...', 'loading');
    try {
        const res = await authFetch('/api/subscriptions');
        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.error || 'Failed to load subscriptions');
        }

        const select = document.getElementById('subscriptionSelect');
        select.innerHTML = '<option value="">-- Select subscription --</option>';

        data.subscriptions.forEach(sub => {
            const option = document.createElement('option');
            option.value = sub.id;
            option.textContent = `${sub.name} (${sub.state})`;
            select.appendChild(option);
        });

        showStatus('subStatus', `Loaded ${data.subscriptions.length} subscriptions`, 'success');
    } catch (e) {
        showStatus('subStatus', `Error: ${e.message}`, 'error');
    }
}

/**
 * Scan the selected subscription
 */
async function scanSubscription() {
    const subscriptionId = document.getElementById('subscriptionSelect').value;
    if (!subscriptionId) {
        showStatus('scanStatus', 'Please select a subscription', 'error');
        return;
    }

    const scanBtn = document.getElementById('scanBtn');
    scanBtn.disabled = true;
    showStatus('scanStatus', 'Scanning resources...', 'loading');

    try {
        const res = await authFetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ subscription_id: subscriptionId })
        });

        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.error || 'Scan failed');
        }

        showStatus('scanStatus', `Scan complete: ${data.nodes_count} nodes, ${data.edges_count} edges`, 'success');

        // Load the graph
        await renderGraph(subscriptionId);

    } catch (e) {
        showStatus('scanStatus', `Scan error: ${e.message}`, 'error');
    } finally {
        scanBtn.disabled = false;
    }
}

/**
 * Render the graph in Cytoscape
 */
async function renderGraph(subscriptionId) {
    try {
        initializeCytoscape();

        const res = await fetch('/api/graph');
        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.error || 'Failed to fetch graph');
        }

        currentGraph = data.graph;

        // Load elements into Cytoscape
        cy.elements().remove();
        cy.add(currentGraph.elements);

        // Custom layout: position resource groups in a grid,
        // VNets in a row within each RG, subnets in a row within each VNet
        const rgNodes = cy.nodes('[type="resource_group"]');
        const cols = Math.ceil(Math.sqrt(rgNodes.length));
        const subnetNodeWidth = 160;
        const vnetPadding = 40;
        const rgPadding = 60;
        const rgGapX = 80;
        const rgGapY = 80;

        // First pass: calculate the width each RG needs
        const rgSizes = [];
        rgNodes.forEach(function(rg) {
            const vnets = rg.children('[type="vnet"]');
            let totalVnetWidth = 0;
            vnets.forEach(function(vnet) {
                const subCount = Math.max(vnet.children('[type="subnet"]').length, 1);
                totalVnetWidth += subCount * subnetNodeWidth + vnetPadding * 2;
            });
            // Add gaps between VNets
            totalVnetWidth += Math.max(vnets.length - 1, 0) * 40;
            rgSizes.push({
                width: totalVnetWidth + rgPadding * 2,
                height: 250,
            });
        });

        // Second pass: position everything
        // Track column widths for grid alignment
        const colWidths = [];
        for (let c = 0; c < cols; c++) {
            let maxW = 0;
            for (let r = 0; r * cols + c < rgNodes.length; r++) {
                maxW = Math.max(maxW, rgSizes[r * cols + c].width);
            }
            colWidths.push(maxW);
        }

        let cursorY = 0;
        for (let r = 0; r * cols < rgNodes.length; r++) {
            let cursorX = 0;
            let rowHeight = 0;
            for (let c = 0; c < cols && r * cols + c < rgNodes.length; c++) {
                const idx = r * cols + c;
                const rg = rgNodes[idx];
                const rgCenterX = cursorX + colWidths[c] / 2;
                const rgCenterY = cursorY + rgSizes[idx].height / 2;

                // Position VNets left-to-right within RG
                const vnets = rg.children('[type="vnet"]');
                let vnetCursorX = rgCenterX - rgSizes[idx].width / 2 + rgPadding;

                vnets.forEach(function(vnet) {
                    const subnets = vnet.children('[type="subnet"]');
                    const subCount = Math.max(subnets.length, 1);
                    const vnetWidth = subCount * subnetNodeWidth + vnetPadding * 2;
                    const vnetCenterX = vnetCursorX + vnetWidth / 2;

                    vnet.position({ x: vnetCenterX, y: rgCenterY });

                    // Position subnets in a row within VNet
                    const totalSubW = (subnets.length - 1) * subnetNodeWidth;
                    subnets.forEach(function(subnet, si) {
                        subnet.position({
                            x: vnetCenterX - totalSubW / 2 + si * subnetNodeWidth,
                            y: rgCenterY,
                        });
                    });

                    vnetCursorX += vnetWidth + 40;
                });

                cursorX += colWidths[c] + rgGapX;
                rowHeight = Math.max(rowHeight, rgSizes[idx].height);
            }
            cursorY += rowHeight + rgGapY;
        }

        // Fit the view to all elements
        cy.fit(cy.elements(), 50);

        // Update title with subscription name
        const subSelect = document.getElementById('subscriptionSelect');
        const subName = subSelect.options[subSelect.selectedIndex].text;
        document.getElementById('diagramTitle').textContent =
            `Topology for ${subName}`;

        // Update info panel
        const nodes = cy.nodes();
        const edges = cy.edges();
        document.getElementById('infoSub').textContent = subscriptionId;
        document.getElementById('infoNodes').textContent = nodes.length;
        document.getElementById('infoEdges').textContent = edges.length;
        document.getElementById('graphInfo').style.display = 'block';

    } catch (e) {
        showStatus('scanStatus', `Render error: ${e.message}`, 'error');
    }
}

/**
 * Clear the graph
 */
async function clearGraph() {
    try {
        await fetch('/api/graph/clear', { method: 'POST' });
        if (cy) {
            cy.elements().remove();
        }
        document.getElementById('graphInfo').style.display = 'none';
        showStatus('scanStatus', 'Graph cleared', 'success');
    } catch (e) {
        showStatus('scanStatus', `Error: ${e.message}`, 'error');
    }
}

/**
 * Initialize on page load
 */
document.addEventListener('DOMContentLoaded', () => {
    // Initialize MSAL
    msalInstance = new msal.PublicClientApplication(msalConfig);

    // Check if already signed in (e.g. after redirect)
    const accounts = msalInstance.getAllAccounts();
    if (accounts.length > 0) {
        updateAuthUI(accounts[0]);
        showStatus('authStatus', 'Signed in', 'success');
    }
});
