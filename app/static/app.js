/**
 * Frontend app for Azure Mapper visualization
 */

let cy = null;
let currentGraph = null;
let drillDownActive = false;

// Resource-group filter state. `allResourceGroups` is every RG in the current
// scan; `selectedResourceGroups` is the subset drawn on the canvas. The Set is
// the source of truth rather than the checkboxes, because the list is
// re-rendered whenever the search box narrows it.
let allResourceGroups = [];
let selectedResourceGroups = new Set();

/**
 * Ask the backend which Azure identity it resolved.
 *
 * Authentication happens server-side via DefaultAzureCredential, so there is
 * nothing to sign into here — this just reports what the host is running as,
 * and surfaces the credential error if it resolved nothing.
 */
async function checkIdentity() {
    showStatus('authStatus', 'Checking Azure credential…', 'loading');
    try {
        const res = await fetch('/api/identity');
        const data = await res.json();

        if (!res.ok || !data.authenticated) {
            throw new Error(data.error || 'No Azure credential available');
        }

        showStatus('authStatus', `Signed in as ${data.identity || 'host identity'}`, 'success');
        const tenant = document.getElementById('tenantInfo');
        if (data.tenant_id) {
            tenant.textContent = `Tenant ${data.tenant_id}`;
            tenant.style.display = 'block';
        }
    } catch (e) {
        showStatus('authStatus', `No Azure credential: ${e.message}`, 'error');
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
            // Peering edges (base)
            {
                selector: 'edge[type="peered_to"]',
                style: {
                    'line-color': '#107c10',
                    'target-arrow-color': '#107c10',
                    'target-arrow-shape': 'triangle',
                    'line-style': 'dashed',
                    'width': 2,
                    'label': 'peering',
                    'font-size': 8,
                    'color': '#107c10',
                    'text-rotation': 'autorotate',
                }
            },
            // Healthy bidirectional peering — arrowheads on both ends, one line
            {
                selector: 'edge[type="peered_to"][?bidirectional]',
                style: {
                    'source-arrow-shape': 'triangle',
                    'source-arrow-color': '#107c10',
                }
            },
            // One-way peering — only one direction exists. Flag it in red.
            {
                selector: 'edge[type="peered_to"][!bidirectional]',
                style: {
                    'line-color': '#d13438',
                    'target-arrow-color': '#d13438',
                    'color': '#d13438',
                    'label': '1-way peering',
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
        const res = await fetch('/api/subscriptions');
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
        const res = await fetch('/api/scan', {
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

        // Build the RG filter from this scan (everything selected), then let
        // the filter lay the graph out and fit the view.
        populateResourceGroupFilter();
        applyResourceGroupFilter();

        // Update title with subscription name
        const subSelect = document.getElementById('subscriptionSelect');
        const subName = subSelect.options[subSelect.selectedIndex].text;
        document.getElementById('diagramTitle').textContent =
            `Topology for ${subName}`;

        document.getElementById('infoSub').textContent = subscriptionId;
        document.getElementById('graphInfo').style.display = 'block';

    } catch (e) {
        showStatus('scanStatus', `Render error: ${e.message}`, 'error');
    }
}

/**
 * Lay out a set of resource groups: RGs in a grid, VNets in a row within each
 * RG, subnets in a row within each VNet.
 *
 * Takes the collection to place rather than reading every RG off the graph, so
 * the resource-group filter can re-pack only what is currently shown instead
 * of leaving holes where the hidden RGs used to sit.
 */
function layoutTopology(rgNodes) {
    const cols = Math.max(Math.ceil(Math.sqrt(rgNodes.length)), 1);
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
}

/**
 * Rebuild the resource-group filter from the graph currently loaded.
 *
 * Every RG starts selected, so a fresh scan looks the way it always has.
 */
function populateResourceGroupFilter() {
    allResourceGroups = cy.nodes('[type="resource_group"]')
        .map(function(rg) { return rg.data('label'); })
        .sort(function(a, b) { return a.localeCompare(b); });
    selectedResourceGroups = new Set(allResourceGroups);

    document.getElementById('rgSearch').value = '';
    document.getElementById('rgFilterSection').style.display =
        allResourceGroups.length > 0 ? 'block' : 'none';
    renderResourceGroupList();
}

/**
 * Render the checkbox list, narrowed to whatever the search box matches.
 */
function renderResourceGroupList() {
    const list = document.getElementById('rgList');
    const query = document.getElementById('rgSearch').value.trim().toLowerCase();
    const shown = listedResourceGroups();

    list.innerHTML = '';

    if (shown.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'rg-empty';
        empty.textContent = query
            ? `No resource groups match "${query}"`
            : 'No resource groups in this scan';
        list.appendChild(empty);
        return;
    }

    shown.forEach(function(name) {
        const label = document.createElement('label');
        label.className = 'rg-item';
        label.title = name;

        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = selectedResourceGroups.has(name);
        box.addEventListener('change', function() {
            if (box.checked) {
                selectedResourceGroups.add(name);
            } else {
                selectedResourceGroups.delete(name);
            }
            applyResourceGroupFilter();
        });

        label.appendChild(box);
        label.appendChild(document.createTextNode(name));
        list.appendChild(label);
    });
}

/**
 * The resource groups the list is currently showing (all of them, or just
 * those matching the search box).
 */
function listedResourceGroups() {
    const query = document.getElementById('rgSearch').value.trim().toLowerCase();
    if (!query) return allResourceGroups;
    return allResourceGroups.filter(function(name) {
        return name.toLowerCase().includes(query);
    });
}

/**
 * Select-all / clear. Both act on the resource groups the list is showing, so
 * a search plus "Select All" is a quick way to isolate a family of RGs.
 */
function setListedResourceGroups(selected) {
    listedResourceGroups().forEach(function(name) {
        if (selected) {
            selectedResourceGroups.add(name);
        } else {
            selectedResourceGroups.delete(name);
        }
    });
    renderResourceGroupList();
    applyResourceGroupFilter();
}

/**
 * Show only the selected resource groups, then re-pack and re-fit the view.
 *
 * Filtering is display-only: the full scan stays in memory, so toggling an RG
 * back on costs nothing and never re-queries Azure. Hiding a node also hides
 * its edges, which means a peering to a hidden VNet disappears — the status
 * line reports how many, since a peering leaving the filtered set is usually
 * the thing you actually want to know about.
 */
function applyResourceGroupFilter() {
    if (!cy) return;

    // Re-packing the canvas underneath an open drill-down would strand it.
    if (drillDownActive) backToOverview();

    const visibleRgs = cy.nodes('[type="resource_group"]').filter(function(rg) {
        return selectedResourceGroups.has(rg.data('label'));
    });
    const hiddenRgs = cy.nodes('[type="resource_group"]').difference(visibleRgs);

    visibleRgs.union(visibleRgs.descendants()).style('display', 'element');
    hiddenRgs.union(hiddenRgs.descendants()).style('display', 'none');

    if (visibleRgs.length > 0) {
        layoutTopology(visibleRgs);
        cy.fit(cy.elements(':visible'), 50);
    }

    updateGraphInfo();

    const total = allResourceGroups.length;
    const shownCount = visibleRgs.length;
    const cutPeerings = cy.edges('[type="peered_to"]').length -
        cy.edges('[type="peered_to"]:visible').length;

    if (shownCount === 0) {
        showStatus('rgStatus', 'No resource groups selected', 'error');
    } else {
        let msg = `Showing ${shownCount} of ${total} resource groups`;
        if (cutPeerings > 0) {
            msg += ` — ${cutPeerings} peering${cutPeerings === 1 ? '' : 's'} to hidden RGs`;
        }
        showStatus('rgStatus', msg, shownCount === total ? 'success' : 'info');
    }
}

/**
 * Refresh the info panel with what is actually on screen.
 */
function updateGraphInfo() {
    document.getElementById('infoRgs').textContent =
        `${cy.nodes('[type="resource_group"]:visible').length} of ${allResourceGroups.length}`;
    document.getElementById('infoNodes').textContent = cy.nodes(':visible').length;
    document.getElementById('infoEdges').textContent = cy.edges(':visible').length;
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
        const res = await fetch('/api/subnet/resources', {
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

    // Zoom back to full view — filtered-out resource groups stay hidden.
    cy.animate({
        fit: { eles: cy.elements(':visible'), padding: 50 },
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
        allResourceGroups = [];
        selectedResourceGroups = new Set();
        document.getElementById('rgFilterSection').style.display = 'none';
        document.getElementById('rgStatus').style.display = 'none';
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
    checkIdentity();
});
