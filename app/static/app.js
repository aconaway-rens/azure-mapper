/**
 * Frontend app for Azure Mapper visualization
 */

let cy = null;
let currentGraph = null;
let msalInstance = null;
let drillDownActive = false;

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
            // When it has children (drill-down), it becomes a compound node
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
            // Subnet when it has children (drill-down active)
            {
                selector: 'node[type="subnet"]:parent',
                style: {
                    'text-valign': 'top',
                    'text-halign': 'center',
                    'text-margin-y': -8,
                    'font-size': 11,
                    'font-weight': 'bold',
                    'padding': '20px',
                    'background-opacity': 0.5,
                    'width': 'auto',
                    'height': 'auto',
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
            // NIC nodes
            {
                selector: 'node[type="nic"]',
                style: {
                    'shape': 'roundrectangle',
                    'background-color': '#ff8c00',
                    'border-color': '#cc7000',
                    'border-width': 1,
                    'width': 140,
                    'height': 40,
                    'font-size': 9,
                    'color': '#fff',
                    'text-wrap': 'wrap',
                    'text-max-width': '130px',
                }
            },
            // VM container node (name at top)
            {
                selector: 'node[type="vm"]',
                style: {
                    'shape': 'roundrectangle',
                    'background-color': '#8661c5',
                    'border-color': '#6b4fa0',
                    'border-width': 1,
                    'text-valign': 'top',
                    'text-halign': 'center',
                    'text-margin-y': -6,
                    'font-size': 11,
                    'font-weight': 'bold',
                    'color': '#fff',
                    'padding': '10px',
                    'background-opacity': 0.9,
                }
            },
            // VM detail child (size, OS, IP)
            {
                selector: 'node[type="vm_detail"]',
                style: {
                    'shape': 'roundrectangle',
                    'background-color': '#7451b0',
                    'border-width': 0,
                    'width': 160,
                    'height': 40,
                    'font-size': 8,
                    'color': '#ddd',
                    'text-wrap': 'wrap',
                    'text-max-width': '150px',
                    'text-valign': 'center',
                    'text-halign': 'center',
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

    // Click a subnet to drill down into its resources
    cy.on('tap', 'node[type="subnet"]', function(evt) {
        const node = evt.target;
        drillDownToSubnet(node);
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
 * Drill down into a subnet to show NICs and VMs
 */
async function drillDownToSubnet(subnetNode) {
    if (drillDownActive) return;

    const subnetData = subnetNode.data();
    const azureId = subnetData.azure_id;
    const subscriptionId = document.getElementById('infoSub').textContent;

    if (!azureId || !subscriptionId) return;

    drillDownActive = true;

    // Show detail panel
    const detailSection = document.getElementById('detailSection');
    detailSection.style.display = 'block';
    showStatus('detailStatus', 'Loading resources...', 'loading');

    // Update detail panel header
    const detailPanel = document.getElementById('detailPanel');
    detailPanel.innerHTML = `
        <div class="detail-item">
            <span class="detail-label">Subnet:</span> ${subnetData.label}
        </div>
    `;

    try {
        const res = await authFetch('/api/subnet/resources', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                subscription_id: subscriptionId,
                subnet_azure_id: azureId,
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to fetch resources');

        // Remove any previously added drill-down nodes
        cy.nodes('[type="nic"]').remove();
        cy.nodes('[type="vm"]').remove();
        cy.edges('[type="attached_to"]').remove();

        const subnetPos = subnetNode.position();
        const vmNodes = [];

        // Build a map of VM name -> list of NICs
        const vmNicMap = {};
        data.nics.forEach(function(nic) {
            if (nic.vm_name) {
                if (!vmNicMap[nic.vm_name]) vmNicMap[nic.vm_name] = [];
                vmNicMap[nic.vm_name].push(nic);
            }
        });

        // Create VM compound nodes with a detail child inside
        data.vms.forEach(function(vm) {
            const vmId = 'vm_' + vm.name;
            const nics = vmNicMap[vm.name] || [];
            const ips = nics.map(function(n) {
                return n.private_ip || '?';
            }).join(', ');

            // VM container node (shows name at top)
            vmNodes.push({
                data: {
                    id: vmId,
                    label: vm.name,
                    type: 'vm',
                    parent: subnetNode.id(),
                    vm_size: vm.vm_size,
                    os_type: vm.os_type,
                }
            });

            // Detail child node (shows size + IP inside)
            let detailLines = [];
            if (vm.vm_size) detailLines.push(vm.vm_size);
            if (vm.os_type) detailLines.push(String(vm.os_type));
            if (ips) detailLines.push(ips);

            vmNodes.push({
                data: {
                    id: vmId + '_detail',
                    label: detailLines.join('\n'),
                    type: 'vm_detail',
                    parent: vmId,
                }
            });
        });

        // Orphan NICs (no VM attached)
        data.nics.forEach(function(nic) {
            if (!nic.vm_name) {
                vmNodes.push({
                    data: {
                        id: 'nic_' + nic.name,
                        label: nic.name + '\n' + (nic.private_ip || ''),
                        type: 'nic',
                        parent: subnetNode.id(),
                    }
                });
            }
        });

        // Add to graph
        cy.add(vmNodes);

        // Hide all nodes except the ancestors and siblings of this subnet
        const parentVnet = subnetNode.parent();
        const ancestors = subnetNode.ancestors();
        cy.nodes().forEach(function(node) {
            const type = node.data('type');
            // Keep: the drilled subnet, its ancestors, and new VM/detail nodes
            if (node.same(subnetNode) || ancestors.contains(node) ||
                type === 'vm' || type === 'vm_detail' || type === 'nic') {
                return;
            }
            // Keep siblings of the subnet (other subnets in same VNet)
            if (node.parent() && node.parent().same(parentVnet) && type === 'subnet') {
                node.style('opacity', 0.2);
                return;
            }
            // Hide everything else
            node.style('opacity', 0.15);
        });
        // Dim peering edges
        cy.edges().style('opacity', 0.1);

        // Highlight the active subnet
        subnetNode.style('border-color', '#e8374a');
        subnetNode.style('border-width', 3);

        // Position VM nodes in a row
        const vmOnlyNodes = cy.nodes('[type="vm"]');
        const spacing = 220;
        const count = vmOnlyNodes.length;
        const totalW = (count - 1) * spacing;
        vmOnlyNodes.forEach(function(node, i) {
            node.position({
                x: subnetPos.x - totalW / 2 + i * spacing,
                y: subnetPos.y,
            });
        });

        // Position orphan NICs after VMs
        const orphanNics = cy.nodes('[type="nic"]');
        orphanNics.forEach(function(node, i) {
            node.position({
                x: subnetPos.x - totalW / 2 + (count + i) * spacing,
                y: subnetPos.y,
            });
        });

        // Zoom to the subnet and its contents
        const allDrillContent = subnetNode.union(subnetNode.descendants());
        cy.animate({
            fit: { eles: allDrillContent, padding: 80 },
            duration: 500,
        });

        // Update detail panel
        let detailHtml = `
            <div class="detail-item">
                <span class="detail-label">Subnet:</span> ${subnetData.label}
            </div>
        `;

        if (data.nics.length > 0) {
            detailHtml += `<div class="detail-item"><span class="detail-label">NICs (${data.nics.length}):</span></div>`;
            data.nics.forEach(function(nic) {
                detailHtml += `<div class="detail-item">&nbsp;&nbsp;${nic.name} - ${nic.private_ip || 'no IP'}`;
                if (nic.vm_name) detailHtml += ` &rarr; ${nic.vm_name}`;
                detailHtml += '</div>';
            });
        } else {
            detailHtml += '<div class="detail-item">No NICs found</div>';
        }

        if (data.vms.length > 0) {
            detailHtml += `<div class="detail-item"><span class="detail-label">VMs (${data.vms.length}):</span></div>`;
            data.vms.forEach(function(vm) {
                detailHtml += `<div class="detail-item">&nbsp;&nbsp;${vm.name}`;
                if (vm.vm_size) detailHtml += ` (${vm.vm_size})`;
                if (vm.os_type) detailHtml += ` - ${vm.os_type}`;
                detailHtml += '</div>';
            });
        }

        detailPanel.innerHTML = detailHtml;
        showStatus('detailStatus',
            `${data.nics.length} NICs, ${data.vms.length} VMs`, 'success');

    } catch (e) {
        showStatus('detailStatus', `Error: ${e.message}`, 'error');
        drillDownActive = false;
    }
}

/**
 * Return to the full topology overview
 */
function backToOverview() {
    // Remove drill-down nodes
    cy.nodes('[type="vm_detail"]').remove();
    cy.nodes('[type="vm"]').remove();
    cy.nodes('[type="nic"]').remove();

    // Restore opacity on all remaining nodes and edges
    cy.nodes().style('opacity', 1);
    cy.edges().style('opacity', 1);

    // Reset any highlighted subnet borders
    cy.nodes('[type="subnet"]').style({
        'border-color': '#0078d4',
        'border-width': 1,
    });

    // Hide detail panel
    document.getElementById('detailSection').style.display = 'none';

    // Zoom back to full view
    cy.animate({
        fit: { eles: cy.elements(), padding: 50 },
        duration: 500,
    });

    drillDownActive = false;
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
