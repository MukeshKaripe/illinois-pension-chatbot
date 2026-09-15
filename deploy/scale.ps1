<#
.SYNOPSIS
    Set the backend's minimum replica count. The demo-day switch.

.DESCRIPTION
    The pilot runs with min-replicas 1 because a Consumption app that has
    scaled to zero pays container start plus the warm-up `_warm_connections`
    does -- the corpus scan, the connection pools -- before it can answer. That
    turns a measured 8s first answer into roughly 30s for whoever asks first,
    which is the worst possible moment for it: a live demonstration.

    An always-on replica is about $25-30/month, so it is worth paying on the
    days it is used and not otherwise.

        ./scale.ps1 -Replicas 1     # before a demo
        ./scale.ps1 -Replicas 0     # after

    At zero the app still answers -- it just makes the person who wakes it
    wait. Use `teardown.ps1` to stop paying for everything else too.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateRange(0, 3)][int] $Replicas,
    [string] $ResourceGroup = "pension-spike-rg",
    [string] $AppName       = "ca-pension-api"
)

$ErrorActionPreference = "Stop"

az containerapp update -g $ResourceGroup -n $AppName --min-replicas $Replicas -o none
if ($LASTEXITCODE -ne 0) { Write-Host "Could not scale $AppName." -ForegroundColor Red; exit 1 }

$fqdn = az containerapp show -g $ResourceGroup -n $AppName `
    --query properties.configuration.ingress.fqdn -o tsv

if ($Replicas -gt 0) {
    Write-Host "min-replicas = $Replicas. https://$fqdn is warm." -ForegroundColor Green
    Write-Host "Give it a minute before demonstrating: the first request after a scale-up"
    Write-Host "still pays the corpus scan, and you would rather that were yours."
} else {
    Write-Host "min-replicas = 0. Compute charges stop when the last replica drains." -ForegroundColor Yellow
    Write-Host "The next request will take roughly 30s while it starts and warms."
}
