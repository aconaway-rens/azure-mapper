/**
 * Frontend app for Azure Mapper visualization
 */

let cy = null;
let currentGraph = null;
let drillDownActive = false;

// How many NICs a subnet may hold before its VM/NIC cards stop being drawn
// inline. A subscription with fifty VMs behind one subnet is unreadable drawn
// flat, so past this point the subnet collapses to a marked summary you click
// into. The cards stay in the graph either way.
const DEFAULT_RENDER_LIMIT = 10;
const WORKLOAD_TYPES = ['vm', 'vm_detail', 'nic'];

let renderLimit = DEFAULT_RENDER_LIMIT;
// Subnets expanded by an explicit drill-down, overriding the limit.
let expandedSubnets = new Set();
// Workload cards per subnet, held aside so a collapsed subnet can drop its
// contents from the graph entirely and get them back without a rescan.
let workloadsBySubnet = {};
let totalVmCount = 0;

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
        style: topologyStyles(),
        layout: { name: 'preset' }
    });

    // Click a subnet to drill down into its resources
    cy.on('tap', 'node[type="subnet"]', function(evt) {
        drillDownToSubnet(evt.target);
    });
}

/**
 * The topology stylesheet, kept separate from the Cytoscape instance so the
 * same rules can be applied when the graph is driven outside a browser.
 */
function topologyStyles() {
    return [
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
            // When its workloads are drawn, it becomes a compound node
            {
                selector: 'node[type="subnet"]',
                style: {
                    'label': subnetLabel,
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
            // Subnet drawn as a container, once its workloads are in it
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
                }
            },
            // A subnet holding workloads, marked whether or not its cards are
            // drawn — this is what makes a collapsed subnet findable.
            {
                selector: 'node[type="subnet"][?nic_count]',
                style: {
                    'border-color': '#8661c5',
                    'border-width': 2,
                }
            },
            // Collapsed: the cards exist but aren't drawn. Overrides the
            // container styling above, since there's nothing inside to size to.
            {
                selector: 'node[type="subnet"][?collapsed]',
                style: {
                    'width': 165,
                    'height': 72,
                    'padding': 0,
                    'text-valign': 'center',
                    'text-margin-y': 0,
                    'font-size': 9,
                    'font-weight': 'normal',
                    'background-color': '#b9a3e0',
                    'background-opacity': 1,
                    'border-style': 'dashed',
                    'border-width': 3,
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
            // NIC with no VM behind it — flagged, since an orphan NIC is
            // usually a leftover worth noticing
            {
                selector: 'node[type="nic"][?orphan]',
                style: {
                    'border-color': '#d13438',
                    'border-width': 2,
                    'border-style': 'dashed',
                }
            },
            // Links a spanning VM to its NIC in each subnet
            {
                selector: 'edge[type="attached_to"]',
                style: {
                    'line-color': '#8661c5',
                    'target-arrow-shape': 'none',
                    'width': 2,
                    'curve-style': 'bezier',
                    'opacity': 0.85,
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
                    // The caption renders above the box, against the VNet's
                    // pale background rather than the node's purple — white
                    // here is invisible.
                    'color': '#4b3b7a',
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
    ];
}

/**
 * Subnet caption: name, CIDR, and what lives inside it.
 *
 * The workload line is what marks a subnet as holding VMs — it's the only clue
 * a collapsed subnet gives about what it's hiding, so it carries the counts
 * rather than a bare "has VMs".
 */
function subnetLabel(ele) {
    const data = ele.data();
    const vms = data.vm_count || 0;
    const nics = data.nic_count || 0;
    if (nics === 0) return data.label;

    const parts = [];
    if (vms > 0) parts.push(`${vms} VM${vms === 1 ? '' : 's'}`);
    parts.push(`${nics} NIC${nics === 1 ? '' : 's'}`);

    let text = `${data.label}\n${parts.join(' · ')}`;
    if (data.collapsed) text += '\nclick to expand';
    return text;
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

        // Index the workload cards before anything asks to draw them.
        indexWorkloads(currentGraph.elements);

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
        document.getElementById('limitSection').style.display = 'block';
        reportCollapsed();

    } catch (e) {
        showStatus('scanStatus', `Render error: ${e.message}`, 'error');
    }
}


/**
 * Lay out the topology: resource groups in a grid, VNets in a row inside each
 * RG, subnets in a row inside each VNet, and VM/NIC cards in a grid inside
 * each subnet.
 *
 * Only leaf nodes are positioned. Cytoscape sizes a compound node from its
 * children, so position() on a VNet — or on a subnet that has cards in it — is
 * silently ignored; those containers take their size from what's inside them.
 * Each container is therefore measured after its contents land, so a subnet
 * holding twelve VMs pushes its neighbours along instead of overlapping them.
 */
function layoutTopology(rgNodes) {
    const RG_GAP_X = 90, RG_GAP_Y = 90, RG_PAD = 45;
    const VNET_GAP = 45, VNET_PAD = 32;
    const SUBNET_GAP = 30, SUBNET_PAD = 24, SUBNET_TITLE_H = 38;
    const CARD_W = 185, CARD_H = 80, CARD_GAP = 16, SPANNING_VM_GAP = 60;

    const cols = Math.max(Math.ceil(Math.sqrt(rgNodes.length)), 1);
    let cursorY = 0, idx = 0;

    while (idx < rgNodes.length) {
        let cursorX = 0, rowHeight = 0;
        for (let c = 0; c < cols && idx < rgNodes.length; c++, idx++) {
            const size = layoutResourceGroup(rgNodes[idx], cursorX, cursorY);
            cursorX += size.w + RG_GAP_X;
            rowHeight = Math.max(rowHeight, size.h);
        }
        cursorY += rowHeight + RG_GAP_Y;
    }

    function layoutResourceGroup(rg, x, y) {
        let vnetX = x + RG_PAD;
        rg.children('[type="vnet"]').forEach(function(vnet) {
            vnetX += layoutVnet(vnet, vnetX, y + RG_PAD).w + VNET_GAP;
        });
        return alignAndMeasure(rg, x, y);
    }

    function layoutVnet(vnet, x, y) {
        let subnetX = x + VNET_PAD;
        let subnetBottom = y + VNET_PAD;

        vnet.children('[type="subnet"]').forEach(function(subnet) {
            const size = layoutSubnet(subnet, subnetX, y + VNET_PAD);
            subnetX += size.w + SUBNET_GAP;
            subnetBottom = Math.max(subnetBottom, y + VNET_PAD + size.h);
        });

        // A VM spanning subnets is a child of the VNet, not of any subnet. It
        // goes in a row underneath them, centred on the subnet row rather than
        // left-justified: its links reach across every subnet it serves, so
        // starting from the left corner drags them all diagonally across the
        // diagram instead of letting them fan out evenly.
        const spanning = vnet.children('[type="vm"]');
        if (spanning.length > 0) {
            const subnetRowWidth = Math.max(subnetX - SUBNET_GAP - (x + VNET_PAD), 0);
            const vmRowWidth = spanning.length * CARD_W +
                (spanning.length - 1) * CARD_GAP;

            let vmX = x + VNET_PAD + (subnetRowWidth - vmRowWidth) / 2;
            const rowY = subnetBottom + SPANNING_VM_GAP;
            spanning.forEach(function(vm) {
                placeCard(vm, vmX + CARD_W / 2, rowY + CARD_H / 2);
                vmX += CARD_W + CARD_GAP;
            });
        }
        return alignAndMeasure(vnet, x, y);
    }

    function layoutSubnet(subnet, x, y) {
        const cards = subnet.children(':visible');

        // Empty or collapsed: a plain box, positioned directly. Its real
        // height varies with how many caption lines it carries, so measure
        // rather than assume.
        if (cards.length === 0) return alignAndMeasure(subnet, x, y);

        const cols = Math.max(1, Math.ceil(Math.sqrt(cards.length)));
        cards.forEach(function(card, i) {
            placeCard(
                card,
                x + SUBNET_PAD + (i % cols) * (CARD_W + CARD_GAP) + CARD_W / 2,
                y + SUBNET_TITLE_H + Math.floor(i / cols) * (CARD_H + CARD_GAP) + CARD_H / 2
            );
        });
        return alignAndMeasure(subnet, x, y);
    }

    function placeCard(card, x, y) {
        // A VM is itself a compound (name on top, detail child inside), so its
        // box comes from the child. A bare NIC is a leaf and moves directly.
        const inner = card.children();
        if (inner.length > 0) {
            inner.forEach(function(ch) { ch.position({ x: x, y: y }); });
        } else {
            card.position({ x: x, y: y });
        }
    }

    /**
     * Move a container so its top-left corner sits exactly at (x, y), then
     * report its size. Compound padding and label overhang mean a container
     * spreads beyond the cards inside it; without this correction those few
     * pixels accumulate and neighbours start to touch.
     */
    function alignAndMeasure(ele, x, y) {
        let bb = ele.boundingBox();
        const dx = x - bb.x1, dy = y - bb.y1;

        if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
            const movable = ele.isChildless()
                ? ele
                : ele.descendants().filter(function(n) { return n.isChildless(); });
            movable.forEach(function(n) {
                const p = n.position();
                n.position({ x: p.x + dx, y: p.y + dy });
            });
            bb = ele.boundingBox();
        }
        return { w: bb.w, h: bb.h };
    }
}

/**
 * Re-pack and re-fit whatever is currently visible.
 */
function layoutVisible() {
    const visibleRgs = cy.nodes('[type="resource_group"]:visible');
    if (visibleRgs.length > 0) layoutTopology(visibleRgs);
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

    applyVisibility();
    layoutVisible();
    if (cy.nodes(':visible').length > 0) cy.fit(cy.elements(':visible'), 50);

    updateGraphInfo();

    const total = allResourceGroups.length;
    const shownCount = cy.nodes('[type="resource_group"]:visible').length;
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
 * Decide what gets drawn: the resource-group filter first, then per-subnet
 * workload collapsing.
 *
 * A subnet holding more NICs than the render limit keeps its VM and NIC cards
 * out of the graph, so a subscription with fifty VMs behind one subnet still
 * renders as a diagram you can read. The subnet is still marked as holding
 * workloads, and drilling into it puts the cards back.
 */
function applyVisibility() {
    cy.nodes('[type="subnet"]').forEach(function(subnet) {
        subnet.data('collapsed', isSubnetCollapsed(subnet));
    });

    syncWorkloadCards();

    cy.nodes('[type="resource_group"]').forEach(function(rg) {
        const visible = selectedResourceGroups.has(rg.data('label'));
        rg.style('display', visible ? 'element' : 'none');
        rg.descendants().style('display', visible ? 'element' : 'none');
    });
}

/**
 * Add or remove each subnet's workload cards to match the collapse decision.
 *
 * Collapsing removes the cards rather than hiding them, because Cytoscape
 * sizes and positions a compound node from its children: a subnet whose
 * children were merely display:none would shrink to a stub the layout can
 * neither measure nor move. Removing them makes it an ordinary box again, and
 * the definitions live in `workloadsBySubnet` so putting them back is free.
 */
function syncWorkloadCards() {
    cy.nodes('[type="subnet"]').forEach(function(subnet) {
        const shouldDraw = !isSubnetCollapsed(subnet);
        const present = subnet.children().length > 0;

        if (shouldDraw && !present) {
            const cards = workloadsBySubnet[subnet.id()] || [];
            // Clone, so re-adding the same subnet later isn't affected by
            // whatever Cytoscape did to the previous copy.
            cy.add(cards.map(function(card) {
                return { data: Object.assign({}, card.data) };
            }));
        } else if (!shouldDraw && present) {
            subnet.descendants().remove();
        }
    });
}

/**
 * Group the scan's VM and NIC cards by the subnet they belong to.
 *
 * Only cards a subnet owns are indexed. A VM that spans subnets is parented to
 * the VNet and stays in the graph permanently — it belongs to no one subnet,
 * so no one subnet gets to collapse it away.
 *
 * Order matters on the way back in: a VM has to exist before the detail card
 * naming it as parent, and both endpoints of an attachment edge have to exist
 * before the edge does. Nodes are therefore emitted ahead of edges.
 */
function indexWorkloads(elements) {
    const subnetIds = new Set();
    const vmToSubnet = {};
    const nicToSubnet = {};
    workloadsBySubnet = {};
    totalVmCount = 0;

    elements.forEach(function(el) {
        if (el.data.type === 'subnet') subnetIds.add(el.data.id);
        if (el.data.type === 'vm') totalVmCount += 1;
    });

    function own(subnetId, el) {
        (workloadsBySubnet[subnetId] = workloadsBySubnet[subnetId] || []).push(el);
    }

    elements.forEach(function(el) {
        const d = el.data;
        if ((d.type === 'vm' || d.type === 'nic') && subnetIds.has(d.parent)) {
            if (d.type === 'vm') vmToSubnet[d.id] = d.parent;
            else nicToSubnet[d.id] = d.parent;
            own(d.parent, el);
        }
    });
    elements.forEach(function(el) {
        const d = el.data;
        if (d.type === 'vm_detail' && vmToSubnet[d.parent]) own(vmToSubnet[d.parent], el);
    });
    elements.forEach(function(el) {
        const d = el.data;
        if (d.source && nicToSubnet[d.target]) own(nicToSubnet[d.target], el);
    });
}

/**
 * The subnet a VM, NIC, or VM detail card belongs to.
 */
function owningSubnet(node) {
    return node.data('type') === 'vm_detail'
        ? node.parent().parent()
        : node.parent();
}

/**
 * A subnet collapses when it holds more NICs than the render limit — unless it
 * has been expanded explicitly by drilling into it.
 */
function isSubnetCollapsed(subnet) {
    if (!subnet || subnet.length === 0) return false;
    if (expandedSubnets.has(subnet.id())) return false;
    return (subnet.data('nic_count') || 0) > renderLimit;
}

/**
 * Change how many NICs a subnet may hold before its contents collapse.
 *
 * Re-deciding every subnet drops any drill-down expansions, which is the point
 * — the new limit should apply uniformly rather than leaving earlier clicks
 * pinned open.
 */
function setRenderLimit(value) {
    const parsed = parseInt(value, 10);
    renderLimit = (isNaN(parsed) || parsed < 0) ? DEFAULT_RENDER_LIMIT : parsed;

    if (!cy || cy.elements().length === 0) return;

    if (drillDownActive) backToOverview();
    expandedSubnets.clear();
    applyVisibility();
    layoutVisible();
    cy.fit(cy.elements(':visible'), 50);
    updateGraphInfo();
    reportCollapsed();
}

/**
 * Say how many subnets are holding their contents back.
 */
function reportCollapsed() {
    const collapsed = cy.nodes('[type="subnet"][?collapsed]:visible');
    const withWork = cy.nodes('[type="subnet"][?nic_count]:visible');

    if (withWork.length === 0) {
        showStatus('limitStatus', 'No VMs or NICs in view', 'info');
        return;
    }
    if (collapsed.length === 0) {
        showStatus('limitStatus',
            `Drawing workloads in all ${withWork.length} populated subnet${withWork.length === 1 ? '' : 's'}`,
            'success');
        return;
    }
    const hiddenVms = collapsed.reduce(function(sum, s) {
        return sum + (s.data('vm_count') || 0);
    }, 0);
    showStatus('limitStatus',
        `${collapsed.length} subnet${collapsed.length === 1 ? '' : 's'} collapsed ` +
        `(${hiddenVms} VMs) — click one to drill in`, 'info');
}


/**
 * Refresh the info panel with what is actually on screen.
 */
function updateGraphInfo() {
    document.getElementById('infoRgs').textContent =
        `${cy.nodes('[type="resource_group"]:visible').length} of ${allResourceGroups.length}`;
    document.getElementById('infoNodes').textContent = cy.nodes(':visible').length;
    document.getElementById('infoEdges').textContent = cy.edges(':visible').length;

    // Collapsed subnets drop their cards from the graph, so the total has to
    // come from the scan rather than from what's currently in it.
    const vmsDrawn = cy.nodes('[type="vm"]:visible').length;
    document.getElementById('infoVms').textContent = totalVmCount === vmsDrawn
        ? String(totalVmCount)
        : `${vmsDrawn} of ${totalVmCount} drawn`;
}


/**
 * Drill into a subnet: expand it if it was collapsed, dim everything else, and
 * zoom to its contents.
 *
 * The VM and NIC cards already came down with the scan, so this is a
 * visibility change rather than a fetch. The old path re-listed every NIC in
 * the subscription and issued a GET per VM on each click.
 */
function drillDownToSubnet(subnetNode) {
    if (drillDownActive) return;

    const subnetData = subnetNode.data();
    if (!subnetData.nic_count) {
        showStatus('detailStatus', 'No NICs or VMs in this subnet', 'info');
        return;
    }

    drillDownActive = true;
    document.getElementById('detailSection').style.display = 'block';

    // Expand this subnet even if the render limit had collapsed it.
    expandedSubnets.add(subnetNode.id());
    applyVisibility();
    layoutVisible();

    // Dim everything that isn't this subnet, its contents, or its containers.
    // A VM spanning subnets lives outside this one but is half the story of
    // what's in it, so anything wired to a NIC in here stays lit too.
    const linked = subnetNode.descendants().neighborhood('node[type="vm"]');
    const keep = subnetNode
        .union(subnetNode.ancestors())
        .union(subnetNode.descendants())
        .union(linked)
        .union(linked.descendants());

    cy.nodes().forEach(function(node) {
        node.style('opacity', keep.contains(node) ? 1 : 0.15);
    });
    cy.edges().forEach(function(edge) {
        const wired = keep.contains(edge.source()) && keep.contains(edge.target());
        edge.style('opacity', wired ? 1 : 0.1);
    });

    subnetNode.style({ 'border-color': '#e8374a', 'border-width': 3 });

    cy.animate({
        fit: {
            eles: subnetNode.union(subnetNode.descendants()),
            padding: 80,
        },
        duration: 500,
    });

    renderSubnetDetail(subnetNode);
}

/**
 * Fill the sidebar with what the drilled subnet holds, read from the graph.
 */
function renderSubnetDetail(subnetNode) {
    const vms = subnetNode.children('[type="vm"]');
    const nics = subnetNode.children('[type="nic"]');
    const data = subnetNode.data();

    let html = `
        <div class="detail-item">
            <span class="detail-label">Subnet:</span> ${data.label.replace('\n', ' ')}
        </div>
    `;

    if (vms.length > 0) {
        html += `<div class="detail-item"><span class="detail-label">VMs (${vms.length}):</span></div>`;
        vms.forEach(function(vm) {
            const d = vm.data();
            let line = `&nbsp;&nbsp;${d.label}`;
            if (d.vm_size) line += ` (${d.vm_size})`;
            if (d.os_type) line += ` - ${d.os_type}`;
            if (d.private_ips) line += ` - ${d.private_ips}`;
            html += `<div class="detail-item">${line}</div>`;
        });
    }

    const orphans = nics.filter(function(nic) { return nic.data('orphan'); });
    const attached = nics.difference(orphans);

    if (attached.length > 0) {
        html += `<div class="detail-item"><span class="detail-label">NICs of VMs spanning subnets (${attached.length}):</span></div>`;
        attached.forEach(function(nic) {
            const d = nic.data();
            let line = `&nbsp;&nbsp;${d.label.replace('\n', ' - ')}`;
            if (d.vm_name) line += ` &rarr; ${d.vm_name}`;
            html += `<div class="detail-item">${line}</div>`;
        });
    }

    if (orphans.length > 0) {
        html += `<div class="detail-item"><span class="detail-label">Unattached NICs (${orphans.length}):</span></div>`;
        orphans.forEach(function(nic) {
            html += `<div class="detail-item">&nbsp;&nbsp;${nic.data('label').replace('\n', ' - ')}</div>`;
        });
    }

    document.getElementById('detailPanel').innerHTML = html;
    showStatus('detailStatus',
        `${data.vm_count || 0} VMs, ${data.nic_count || 0} NICs`, 'success');
}


/**
 * Return to the full topology overview.
 */
function backToOverview() {
    drillDownActive = false;

    // Drop any drill-down expansion so the render limit governs again.
    expandedSubnets.clear();
    applyVisibility();
    layoutVisible();

    // Restore opacity on everything the drill-down dimmed.
    cy.nodes().style('opacity', 1);
    cy.edges().style('opacity', 1);

    // Reset the highlighted subnet border; the stylesheet re-applies the
    // workload marking on its own.
    cy.nodes('[type="subnet"]').removeStyle('border-color border-width');

    document.getElementById('detailSection').style.display = 'none';

    cy.animate({
        fit: { eles: cy.elements(':visible'), padding: 50 },
        duration: 500,
    });
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
        expandedSubnets = new Set();
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
