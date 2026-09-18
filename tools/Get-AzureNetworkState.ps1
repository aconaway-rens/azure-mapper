<#
.SYNOPSIS
    Point-in-time text report of an Azure resource group's network objects.

.DESCRIPTION
    Prints VMs and their interfaces, load balancers (frontends, pools, rules,
    probes), NSGs, route tables, and optional backend health, as aligned plain
    text meant to be shared.

    Every line is also collected into a buffer, so -OutFile writes exactly what
    you saw. That matters: Write-Host writes to the information stream, not to
    standard output, so `.\Get-AzureNetworkState.ps1 > state.txt` captures
    nothing at all. Use -OutFile, or redirect the information stream with 6>.

.PARAMETER ResourceGroup
    Resource group to report on.

.PARAMETER OutFile
    Also write the report to this path, without colour.

.PARAMETER MaxCellWidth
    Truncate any cell longer than this, so a long member list can't push the
    table past a terminal width. Use -Full to disable.

.PARAMETER Full
    Never truncate cell contents.

.PARAMETER NoColor
    Plain output, for terminals that mangle colour.

.PARAMETER IncludeHealth
    Query DipAvailability metrics for each load balancer's backends. Off by
    default because it is the slowest part of the report by a wide margin.

.EXAMPLE
    .\Get-AzureNetworkState.ps1 -ResourceGroup vw-hub-prod

.EXAMPLE
    .\Get-AzureNetworkState.ps1 -ResourceGroup vw-hub-prod -OutFile state.txt -IncludeHealth
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'vw-hub-prod',
    [string]$OutFile,
    [int]$MaxCellWidth = 42,
    [switch]$Full,
    [switch]$NoColor,
    [switch]$IncludeHealth
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- output ---

$script:Buffer = New-Object System.Collections.Generic.List[string]

function Write-Line {
    param([string]$Text = '', [string]$Colour)

    $script:Buffer.Add($Text)
    if ($NoColor -or [string]::IsNullOrEmpty($Colour)) { Write-Host $Text }
    else { Write-Host $Text -ForegroundColor $Colour }
}

function Write-Banner {
    param([string]$Title, [string]$Subtitle)

    Write-Line ''
    Write-Line ('=' * 78) 'Cyan'
    if ($Subtitle) { Write-Line ("  $Title  --  $Subtitle") 'Cyan' }
    else { Write-Line ("  $Title") 'Cyan' }
    Write-Line ('=' * 78) 'Cyan'
}

# Empty values print as '-' rather than nothing, so a blank column reads as
# "no value" instead of "this script forgot to fill it in".
function Format-Cell {
    param([object]$Value)

    $text = if ($null -eq $Value) { '-' } else { [string]$Value }
    if ($text -eq '') { $text = '-' }

    if (-not $Full -and $MaxCellWidth -gt 4 -and $text.Length -gt $MaxCellWidth) {
        $text = $text.Substring(0, $MaxCellWidth - 3) + '...'
    }
    return $text
}

function Get-Widths {
    param([object[]]$Rows, [string[]]$Header)

    $widths = @()
    for ($i = 0; $i -lt $Header.Count; $i++) {
        $max = $Header[$i].Length
        foreach ($row in $Rows) {
            $len = ([string]$row[$i]).Length
            if ($len -gt $max) { $max = $len }
        }
        $widths += $max
    }
    return ,$widths
}

# One renderer for every table in the report, so columns line up the same way
# everywhere and a long value can never break the alignment of its neighbours.
function Write-Table {
    param(
        [string[]]$Header,
        [object[]]$Rows,
        [int]$Indent = 4,
        [string]$EmptyText = '(none)'
    )

    $pad = ' ' * $Indent

    if (-not $Rows -or $Rows.Count -eq 0) {
        Write-Line ($pad + $EmptyText) 'DarkGray'
        return
    }

    $clean = @()
    foreach ($row in $Rows) {
        $cells = @()
        foreach ($cell in $row) { $cells += (Format-Cell $cell) }
        $clean += , $cells
    }

    $widths = Get-Widths $clean $Header

    $headerCells = for ($i = 0; $i -lt $Header.Count; $i++) {
        $Header[$i].PadRight($widths[$i])
    }
    Write-Line ($pad + (($headerCells -join '  ').TrimEnd())) 'DarkGray'

    foreach ($row in $clean) {
        $cells = for ($i = 0; $i -lt $row.Count; $i++) {
            if ($i -eq $row.Count - 1) { $row[$i] } else { $row[$i].PadRight($widths[$i]) }
        }
        Write-Line ($pad + (($cells -join '  ').TrimEnd()))
    }
}

function Get-LeafName {
    param([string]$ResourceId)
    if ([string]::IsNullOrEmpty($ResourceId)) { return $null }
    return ($ResourceId -split '/')[-1]
}

function Invoke-Az {
    param([string[]]$Arguments, [string]$What)

    $json = & az @Arguments -o json
    if ($LASTEXITCODE -ne 0) {
        throw "az $($Arguments -join ' ') failed while reading $What"
    }
    if ([string]::IsNullOrWhiteSpace($json)) { return @() }
    return ($json | ConvertFrom-Json)
}

# ------------------------------------------------------------- collection ---

Write-Host "Collecting from $ResourceGroup ..." -ForegroundColor DarkGray

$account = Invoke-Az @('account', 'show') 'the signed-in account'
$vms     = @(Invoke-Az @('vm', 'list', '-g', $ResourceGroup, '-d') 'virtual machines')
$nics    = @(Invoke-Az @('network', 'nic', 'list', '-g', $ResourceGroup) 'network interfaces')
$pips    = @(Invoke-Az @('network', 'public-ip', 'list', '-g', $ResourceGroup) 'public IPs')
$lbs     = @(Invoke-Az @('network', 'lb', 'list', '-g', $ResourceGroup) 'load balancers')
$nsgs    = @(Invoke-Az @('network', 'nsg', 'list', '-g', $ResourceGroup) 'network security groups')
$routes  = @(Invoke-Az @('network', 'route-table', 'list', '-g', $ResourceGroup) 'route tables')

# Index by ID once. The originals re-scanned the whole list for every lookup,
# which is invisible at this size but turns quadratic on a large subscription.
$nicById = @{}
foreach ($nic in $nics) { $nicById[$nic.id] = $nic }
$pipById = @{}
foreach ($pip in $pips) { $pipById[$pip.id] = $pip }

# ----------------------------------------------------------------- header ---

$now = Get-Date
Write-Line ''
Write-Line 'AZURE NETWORK STATE' 'Cyan'
Write-Line ("Generated     : {0:yyyy-MM-dd HH:mm:ss} local / {1:yyyy-MM-dd HH:mm:ss}Z" -f $now, $now.ToUniversalTime())
Write-Line ("Subscription  : {0}" -f $account.name)
Write-Line ("                {0}" -f $account.id)
Write-Line ("Resource group: {0}" -f $ResourceGroup)
Write-Line ("Contents      : {0} VMs, {1} NICs, {2} load balancers, {3} NSGs, {4} route tables" -f `
    $vms.Count, $nics.Count, $lbs.Count, $nsgs.Count, $routes.Count)

# -------------------------------------------------------------------- VMs ---

Write-Banner 'VIRTUAL MACHINES' ("{0} found" -f $vms.Count)

$vmRows = @()
foreach ($vm in $vms | Sort-Object name) {
    $vmNics = @($vm.networkProfile.networkInterfaces | ForEach-Object { $nicById[$_.id] } | Where-Object { $_ })
    $vnet = '-'
    if ($vmNics.Count -gt 0 -and $vmNics[0].ipConfigurations.Count -gt 0) {
        $parts = $vmNics[0].ipConfigurations[0].subnet.id -split '/'
        $vnet = $parts[-3]
    }
    $vmRows += , @($vm.name, $vm.hardwareProfile.vmSize, $vm.location, $vm.powerState, $vnet, $vmNics.Count)
}
Write-Table @('HOST', 'SIZE', 'REGION', 'POWER', 'VNET', 'NICS') $vmRows 2

# A flat interface table rather than a sub-table per host: one header instead
# of one per VM, and it stays greppable when someone pastes it into a ticket.
Write-Line ''
Write-Line '  Interfaces' 'DarkGray'

$nicRows = @()
foreach ($nic in $nics | Sort-Object name) {
    $owner = Get-LeafName $nic.virtualMachine.id
    if (-not $owner) { $owner = '(unattached)' }

    foreach ($cfg in @($nic.ipConfigurations)) {
        $subnetParts = $cfg.subnet.id -split '/'
        $public = '-'
        if ($cfg.publicIPAddress) {
            $pip = $pipById[$cfg.publicIPAddress.id]
            $public = if ($pip) { $pip.ipAddress } else { '(not in this RG)' }
        }
        $nicRows += , @(
            $owner,
            $nic.name,
            $subnetParts[-1],
            $cfg.privateIPAddress,
            $public,
            (Get-LeafName $nic.networkSecurityGroup.id)
        )
    }
}
Write-Table @('HOST', 'NIC', 'SUBNET', 'PRIVATE', 'PUBLIC', 'NSG') ($nicRows | Sort-Object { $_[0] }, { $_[1] }) 2

# --------------------------------------------------------- load balancers ---

Write-Banner 'LOAD BALANCERS' ("{0} found" -f $lbs.Count)

$lbRows = @()
foreach ($lb in $lbs | Sort-Object name) {
    $type = if (@($lb.frontendIPConfigurations | Where-Object { $_.publicIPAddress }).Count -gt 0) { 'public' } else { 'internal' }
    $lbRows += , @(
        $lb.name, $lb.sku.name, $lb.sku.tier, $type, $lb.location,
        @($lb.frontendIPConfigurations).Count,
        @($lb.loadBalancingRules).Count,
        @($lb.backendAddressPools).Count
    )
}
Write-Table @('LOADBALANCER', 'SKU', 'TIER', 'TYPE', 'REGION', 'FRONTENDS', 'RULES', 'POOLS') $lbRows 2

foreach ($lb in $lbs | Sort-Object name) {
    Write-Line ''
    Write-Line ("  {0}" -f $lb.name) 'Cyan'

    $feRows = @()
    foreach ($fe in @($lb.frontendIPConfigurations)) {
        if ($fe.publicIPAddress) {
            $pip = $pipById[$fe.publicIPAddress.id]
            $address = if ($pip) { "$($pip.ipAddress) ($($pip.name))" } else { '(public IP not in this RG)' }
            $allocation = if ($pip) { $pip.publicIPAllocationMethod } else { '-' }
            $zones = if ($pip -and $pip.zones) { $pip.zones -join ',' } else { 'none' }
            $feRows += , @($fe.name, 'public', $address, $allocation, $zones)
        }
        else {
            $zones = if ($fe.zones) { $fe.zones -join ',' } else { 'none' }
            $feRows += , @($fe.name, 'internal', $fe.privateIPAddress, $fe.privateIPAllocationMethod, $zones)
        }
    }
    Write-Table @('FRONTEND', 'KIND', 'ADDRESS', 'ALLOCATION', 'ZONES') $feRows 4

    $poolRows = @()
    foreach ($pool in @($lb.backendAddressPools)) {
        $members = @()
        $kind = 'nic'
        if ($pool.backendIPConfigurations) {
            # /.../networkInterfaces/<nic>/ipConfigurations/<cfg> -> the NIC
            $members = @($pool.backendIPConfigurations.id | ForEach-Object { ($_ -split '/')[8] } | Sort-Object -Unique)
        }
        elseif ($pool.loadBalancerBackendAddresses) {
            $kind = 'address'
            $members = @($pool.loadBalancerBackendAddresses | ForEach-Object {
                    if ($_.ipAddress) { "$($_.name) ($($_.ipAddress))" } else { $_.name }
                })
        }
        $poolRows += , @($pool.name, $kind, $members.Count, ($members -join ', '))
    }
    Write-Table @('BACKEND POOL', 'KIND', 'COUNT', 'MEMBERS') $poolRows 4

    $ruleRows = @()
    foreach ($rule in @($lb.loadBalancingRules) | Sort-Object name) {
        $ports = if ($rule.protocol -eq 'All' -and $rule.frontendPort -eq 0) {
            'HA ports (all)'
        }
        else {
            "{0} {1}->{2}" -f $rule.protocol, $rule.frontendPort, $rule.backendPort
        }
        $ruleRows += , @(
            $rule.name,
            $ports,
            (Get-LeafName $rule.frontendIPConfiguration.id),
            (Get-LeafName $rule.backendAddressPool.id),
            $(if ($rule.enableFloatingIP) { 'yes' } else { 'no' }),
            "$($rule.idleTimeoutInMinutes)m"
        )
    }
    Write-Table @('RULE', 'PORTS', 'FRONTEND', 'POOL', 'FLOATING', 'IDLE') $ruleRows 4

    $probeRows = @()
    foreach ($probe in @($lb.probes) | Sort-Object name) {
        $probeRows += , @(
            $probe.name, $probe.protocol, $probe.port,
            "every $($probe.intervalInSeconds)s",
            "$($probe.numberOfProbes) to mark down"
        )
    }
    Write-Table @('PROBE', 'PROTOCOL', 'PORT', 'INTERVAL', 'THRESHOLD') $probeRows 4
}

# ------------------------------------------------------------------- NSGs ---

Write-Banner 'NETWORK SECURITY GROUPS' ("{0} found" -f $nsgs.Count)

foreach ($nsg in $nsgs | Sort-Object name) {
    $onSubnets = @($nsg.subnets | ForEach-Object { Get-LeafName $_.id } | Sort-Object)
    $onNics = @($nsg.networkInterfaces | ForEach-Object { Get-LeafName $_.id } | Sort-Object)

    Write-Line ''
    Write-Line ("  {0}" -f $nsg.name) 'Cyan'
    # Attachment named, not just counted: on a NIC-attached design, which
    # interfaces are covered is the whole question.
    Write-Line ("    subnets: {0}" -f $(if ($onSubnets) { $onSubnets -join ', ' } else { 'none' })) 'DarkGray'
    Write-Line ("    nics   : {0}" -f $(if ($onNics) { $onNics -join ', ' } else { 'none' })) 'DarkGray'

    $ruleRows = @()
    foreach ($rule in @($nsg.securityRules) | Sort-Object direction, priority) {
        $src = if ($rule.sourceAddressPrefixes) { $rule.sourceAddressPrefixes -join ',' } else { $rule.sourceAddressPrefix }
        $dst = if ($rule.destinationAddressPrefixes) { $rule.destinationAddressPrefixes -join ',' } else { $rule.destinationAddressPrefix }
        $ports = if ($rule.destinationPortRanges) { $rule.destinationPortRanges -join ',' } else { $rule.destinationPortRange }
        $ruleRows += , @($rule.priority, $rule.direction, $rule.access, $rule.protocol, $src, $dst, $ports, $rule.name)
    }
    Write-Table @('PRI', 'DIR', 'ACCESS', 'PROTO', 'SOURCE', 'DEST', 'PORTS', 'NAME') $ruleRows 4 '(no custom rules - defaults only)'
}

# ----------------------------------------------------------- route tables ---

Write-Banner 'ROUTE TABLES' ("{0} found" -f $routes.Count)

foreach ($table in $routes | Sort-Object name) {
    $onSubnets = @($table.subnets | ForEach-Object { Get-LeafName $_.id } | Sort-Object)

    Write-Line ''
    Write-Line ("  {0}" -f $table.name) 'Cyan'
    Write-Line ("    subnets        : {0}" -f $(if ($onSubnets) { $onSubnets -join ', ' } else { 'none' })) 'DarkGray'
    Write-Line ("    bgp propagation: {0}" -f $(if ($table.disableBgpRoutePropagation) { 'disabled' } else { 'enabled' })) 'DarkGray'

    $routeRows = @()
    foreach ($route in @($table.routes) | Sort-Object addressPrefix) {
        $routeRows += , @($route.name, $route.addressPrefix, $route.nextHopType, $route.nextHopIpAddress)
    }
    Write-Table @('ROUTE', 'PREFIX', 'NEXT HOP TYPE', 'NEXT HOP IP') $routeRows 4
}

# ----------------------------------------------------------------- health ---

if ($IncludeHealth) {
    Write-Banner 'BACKEND HEALTH' 'DipAvailability, last 30 minutes'

    foreach ($lb in $lbs | Sort-Object name) {
        Write-Line ''
        Write-Line ("  {0}" -f $lb.name) 'Cyan'

        $raw = & az monitor metrics list --resource $lb.id `
            --metric DipAvailability --interval PT1M --offset 30m `
            --aggregation Average --filter "BackendIPAddress eq '*'" -o json

        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) {
            Write-Line '    metrics query returned nothing' 'DarkGray'
            continue
        }

        $metrics = $raw | ConvertFrom-Json
        $healthRows = @()
        foreach ($series in @($metrics.value.timeseries)) {
            $ip = ($series.metadatavalues | Where-Object { $_.name.value -eq 'backendipaddress' }).value
            $points = @($series.data | Where-Object { $null -ne $_.average })
            if ($points.Count -eq 0) { continue }

            $last = [double]$points[-1].average
            # Spelled out rather than colour-coded: colour does not survive a
            # copy-paste into a ticket, and this report exists to be pasted.
            $state = if ($last -ge 100) { 'UP' }
            elseif ($last -le 0) { 'DOWN  <<' }
            else { 'FLAPPING  <<' }

            $healthRows += , @($ip, ("{0}%" -f [math]::Round($last)), $state, $points.Count)
        }
        Write-Table @('BACKEND IP', 'AVAILABILITY', 'STATE', 'SAMPLES') $healthRows 4 '    no probe data in window'
    }
}
else {
    Write-Line ''
    Write-Line 'Backend health not queried. Re-run with -IncludeHealth for DipAvailability.' 'DarkGray'
}

Write-Line ''

# ------------------------------------------------------------------- save ---

if ($OutFile) {
    $script:Buffer | Set-Content -Path $OutFile -Encoding UTF8
    Write-Host ("Report written to {0} ({1} lines)" -f $OutFile, $script:Buffer.Count) -ForegroundColor Green
}
