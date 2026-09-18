# tools

Standalone helpers that sit alongside Azure Mapper. They are not part of the
app and the app does not call them.

## Get-AzureNetworkState.ps1

A point-in-time **text** report of a resource group's network objects — VMs and
their interfaces, load balancers (frontends, pools, rules, probes), NSGs, route
tables, and optionally backend health. Where the app draws the topology, this
prints it, for pasting into a ticket or a client email.

Needs the Azure CLI (`az`) on PATH and a signed-in session; it reads through
`az`, not the Python app, so it works on its own.

```powershell
.\Get-AzureNetworkState.ps1 -ResourceGroup vw-hub-prod
.\Get-AzureNetworkState.ps1 -ResourceGroup vw-hub-prod -OutFile state.txt
.\Get-AzureNetworkState.ps1 -ResourceGroup vw-hub-prod -IncludeHealth -Full
```

| Parameter | Purpose |
|---|---|
| `-ResourceGroup` | Which group to report on |
| `-OutFile` | Also write the report to a file, without colour |
| `-IncludeHealth` | Query `DipAvailability` per backend — much the slowest part |
| `-Full` | Don't truncate long cells (member lists, address prefixes) |
| `-MaxCellWidth` | Truncation width, default 42 |
| `-NoColor` | Plain output |

**Use `-OutFile` rather than `>`.** `Write-Host` writes to PowerShell's
information stream, not standard output, so `.\Get-AzureNetworkState.ps1 >
state.txt` writes an empty file. Every line is buffered as it prints, and
`-OutFile` saves exactly what you saw.

Runs on PowerShell 7 (`pwsh`) on Windows, macOS or Linux, and on Windows
PowerShell 5.1.
