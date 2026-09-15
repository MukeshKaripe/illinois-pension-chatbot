<#
.SYNOPSIS
    Delete everything this deployment created, and nothing it did not.

.DESCRIPTION
    Deliberately narrow. The resource group also holds nothing else today, but
    a script that deletes a resource group is a script that one day deletes the
    wrong one, so this removes named resources and leaves the group standing.

    What it does NOT touch, because this deployment did not create them and a
    demo ending is not a reason to lose them:
        psql-pension-dev      the database, its migrations and its personas
        search-mediq-dev      the index, and 4,766 documents that cost an
                              ingestion run and an embedding bill to produce
        the Azure OpenAI resource
        pension-chatbot-logs-dev

    It also reminds you about the Postgres firewall rule, which lives in a
    different resource group and so cannot be cleaned up by deleting this one.

        ./teardown.ps1              # show what would be deleted
        ./teardown.ps1 -Confirm     # actually delete it
#>

[CmdletBinding()]
param(
    [string] $ResourceGroup   = "pension-spike-rg",
    [string] $AppName         = "ca-pension-api",
    [string] $MigrateJobName  = "pension-migrate-job",
    [string] $BenchJobName    = "pension-bench-job",
    [string] $SwaName         = "swa-pension-web",
    [string] $EnvName         = "pension-spike-env",
    [string] $AcrName         = "pensionspikeacr",
    [string] $PgResourceGroup = "mediq-rg",
    [string] $PgServerName    = "psql-pension-dev",
    [switch] $Confirm
)

$ErrorActionPreference = "Stop"

$targets = @(
    @{ Kind = "containerapp";     Name = $AppName },
    @{ Kind = "containerapp job"; Name = $MigrateJobName },
    @{ Kind = "containerapp job"; Name = $BenchJobName },
    @{ Kind = "staticwebapp";     Name = $SwaName },
    @{ Kind = "containerapp env"; Name = $EnvName },
    @{ Kind = "acr";              Name = $AcrName }
)

if (-not $Confirm) {
    Write-Host "Would delete from ${ResourceGroup}:" -ForegroundColor Yellow
    $targets | ForEach-Object { Write-Host "  $($_.Kind.PadRight(18)) $($_.Name)" }
    Write-Host "`nWould NOT touch: $PgServerName, search-mediq-dev, the OpenAI resource, the logs workspace."
    Write-Host "Re-run with -Confirm to delete."
    exit 0
}

foreach ($t in $targets) {
    Write-Host "deleting $($t.Kind) $($t.Name)..." -NoNewline
    switch ($t.Kind) {
        "containerapp"     { az containerapp delete -g $ResourceGroup -n $t.Name --yes -o none 2>$null }
        "containerapp job" { az containerapp job delete -g $ResourceGroup -n $t.Name --yes -o none 2>$null }
        "staticwebapp"     { az staticwebapp delete -g $ResourceGroup -n $t.Name --yes -o none 2>$null }
        "containerapp env" { az containerapp env delete -g $ResourceGroup -n $t.Name --yes -o none 2>$null }
        "acr"              { az acr delete -g $ResourceGroup -n $t.Name --yes -o none 2>$null }
    }
    Write-Host " done"
}

Write-Host "`nOne thing left, and it is in another resource group:" -ForegroundColor Yellow
Write-Host "  The Postgres firewall rule that lets Azure services reach $PgServerName."
Write-Host "  Check it with:"
Write-Host "    az postgres flexible-server firewall-rule list -g $PgResourceGroup -s $PgServerName -o table"
Write-Host "  and remove any rule this deployment added once nothing needs it."
