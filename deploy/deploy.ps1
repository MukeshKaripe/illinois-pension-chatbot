<#
.SYNOPSIS
    Build and deploy the Illinois Pension Code chatbot to Azure Container Apps.

.DESCRIPTION
    One script, nine idempotent stages. Running it on a subscription that has
    nothing creates everything; running it again updates what changed. That is
    the point: this file, not the running instance, is the deliverable. The
    pilot it deploys is expected to be torn down, and the same script is meant
    to be pointed at a client's subscription by editing the parameters below.

    WHY THERE IS NO CI PIPELINE HERE.
    A pipeline would hold this logic in a form that only GitHub Actions can
    run, and the identity wiring it needs -- a federated credential, repository
    secrets, a Static Web Apps deploy token -- is exactly the part that cannot
    be ported to another tenant. The logic lives here instead. CI, when it is
    wanted, is a wrapper: check out, `azure/login`, run this. About 20 lines,
    and they can be written for GitHub Actions or Azure DevOps without touching
    anything below.

    WHAT THIS DEPLOYMENT DELIBERATELY DOES NOT DO, and what a client
    environment must add:
      * Secrets live as Container App secrets, sourced from a local .env. A
        real deployment should read them from Key Vault -- one function here.
      * Postgres is reached through its "allow Azure services" firewall rule,
        because a Consumption environment's egress address comes from a shared
        SNAT pool and changes between executions (measured: three different
        addresses across three runs). A real deployment needs VNet integration
        with a NAT Gateway, or a private endpoint.
      * Auth is the application's own JWT. Entra External ID is the documented
        Phase 2.
      * One database serves both development and the pilot. Where real member
        conversations persist, they need their own.

.EXAMPLE
    ./deploy.ps1                      # full deploy
    ./deploy.ps1 -SkipTests           # redeploy unchanged code
    ./deploy.ps1 -BackendOnly         # only the API changed
    ./deploy.ps1 -FrontendOnly        # only the web app changed
#>

[CmdletBinding()]
param(
    # ── Everything that changes between subscriptions lives here ──
    [string] $SubscriptionId = "43159d01-4ca3-4954-b696-4432b2e42576",
    [string] $ResourceGroup  = "pension-spike-rg",
    [string] $Location       = "centralus",
    [string] $AcrName        = "pensionspikeacr",
    [string] $EnvName        = "pension-spike-env",
    [string] $AppName        = "ca-pension-api",
    [string] $MigrateJobName = "pension-migrate-job",
    [string] $SwaName        = "swa-pension-web",
    # Postgres lives in its own resource group here because it predates this
    # deployment. On a fresh subscription it is usually $ResourceGroup.
    [string] $PgResourceGroup = "mediq-rg",
    [string] $PgServerName    = "psql-pension-dev",
    [string] $LogsResourceGroup = "mediq-rg",
    [string] $LogsWorkspace     = "pension-chatbot-logs-dev",

    # Nested rather than Join-Path's three-argument form, which Windows
    # PowerShell 5.1 does not have.
    [string] $EnvFile = (Join-Path (Join-Path $PSScriptRoot "..") ".env"),
    [switch] $SkipTests,
    [switch] $BackendOnly,
    [switch] $FrontendOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }
function Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }
function Fail($text) { Write-Host "`nFAILED: $text" -ForegroundColor Red; exit 1 }

# ── Talking to az from Windows PowerShell ───────────────────────
#
# Windows PowerShell turns a native command's redirected stderr into
# ErrorRecords, one per line. Under `$ErrorActionPreference = "Stop"` that
# means az terminates this script by printing an *informational* message --
# "the behavior of this command has been altered by the following extension:
# containerapp" is a warning, and it killed a deploy at the infrastructure
# stage. Exit codes are what az actually uses to report failure, so these two
# helpers read those and leave stderr alone.

# Both are deliberately *simple* functions reading `$args`, not advanced ones
# with a [Parameter(ValueFromRemainingArguments)] block. An advanced function
# acquires PowerShell's common parameters, and az's own flags then collide with
# them by prefix: `-o json` is refused as ambiguous between -OutVariable and
# -OutBuffer before az ever sees it. The same trap is waiting for -e, -w, -i,
# -v, -d and -p. A simple function has no parameters to collide with.

function Get-AzJson {
    # An `az ... -o json` that is allowed to fail. Returns the parsed object, or
    # $null when the resource does not exist -- which is how every "create it
    # only if absent" check below is written.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = & az @args 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
        return ($raw | ConvertFrom-Json)
    } finally { $ErrorActionPreference = $previous }
}

function Invoke-Native {
    # Run any native executable and return its exit code, which is the only
    # thing it actually uses to report failure.
    #
    # `docker build`, `npm` and `uv` all write ordinary progress to stderr, and
    # Windows PowerShell turns a native command's stderr into ErrorRecords
    # whenever the stream is redirected -- including by whoever invoked this
    # script with `2>&1`. Under ErrorActionPreference 'Stop' that terminates
    # the deploy mid-build, having printed nothing but "#0 building with
    # desktop-linux instance". The script must not care how it was called.
    # Output goes through Write-Host, not into the pipeline and not to Out-Host.
    # Into the pipeline, a PowerShell function returns it all, so the caller's
    # `-ne 0` would compare docker's entire build log against an integer. To
    # Out-Host, it bypasses redirection -- which is how a failing `npm ci`
    # reported "npm ci failed" into a log that contained not one line of npm's
    # own explanation.
    #
    # The arguments are splatted from a named variable. `@($args[1..n])` in
    # argument position is an array *subexpression*, not splatting: npm was
    # handed the literal string "args[0]" and said so.
    $exe = $args[0]
    $rest = @()
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $exe @rest 2>&1 | ForEach-Object { Write-Host $_ }
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
}

function Get-NativeOutput {
    # The capturing counterpart: for a command run *for* its output rather than
    # its effect, such as asking git for the current commit.
    $exe = $args[0]
    $rest = @()
    if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        return (& $exe @rest 2>$null)
    } finally { $ErrorActionPreference = $previous }
}

function Invoke-Az {
    # An az command that must succeed. Returns its stdout, and fails the deploy
    # on a non-zero exit code rather than on anything written to stderr.
    # The failure message names the command but not what was passed to it.
    #
    # `--secrets` carries every credential in the .env, and az's own error
    # output echoes the whole command line when it rejects a flag -- which put
    # the database password and both API keys into a deploy log. Nothing can be
    # done about what az prints, but this script will not add to it: on failure
    # it reports the first two tokens, which is enough to say which call broke.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $summary = ($args | Select-Object -First 3) -join ' '
    try {
        $output = & az @args
        if ($LASTEXITCODE -ne 0) { Fail "``az $summary ...`` exited $LASTEXITCODE (see the output above)" }
        return $output
    } finally { $ErrorActionPreference = $previous }
}

# ── 1. Preflight ────────────────────────────────────────────────
#
# Every check here corresponds to a way an earlier deploy actually broke:
# Docker not running, a stale registry login, and -- the expensive one -- a
# container that started cleanly and could not reach its database, which cost
# fifteen turns of 60-second timeouts before anything said so.
function Invoke-Preflight {
    Step "Preflight"

    $account = Get-AzJson account show -o json
    if (-not $account) { Fail "not logged in. Run: az login" }
    if ($account.id -ne $SubscriptionId) {
        Note "switching subscription to $SubscriptionId"
        Invoke-Az account set --subscription $SubscriptionId
    }
    Note "subscription: $($account.name)"

    if (-not $FrontendOnly) {
        if ((Invoke-Native docker info) -ne 0) { Fail "the Docker daemon is not running." }
        Note "docker: up"
    }

    # Resource providers, registered before anything needs them.
    #
    # A subscription that has never created one of these resource types refuses
    # to, with MissingSubscriptionRegistration -- and it is refused at the
    # create call, deep into a deploy, rather than at the start. Three of these
    # were unregistered on the subscription this was first run against, each
    # discovered by a separate failed run. Registration is free, idempotent and
    # takes a couple of minutes, so it belongs in preflight where a fresh
    # subscription meets it once.
    foreach ($provider in @("Microsoft.App", "Microsoft.ContainerRegistry", "Microsoft.Web",
                            "Microsoft.OperationalInsights")) {
        $state = Get-AzJson provider show -n $provider --query registrationState -o json
        if ($state -ne "Registered") {
            Note "registering $provider (this takes a minute)"
            Invoke-Az provider register -n $provider --wait -o none
        }
    }

    if (-not (Test-Path $EnvFile)) { Fail "no .env at $EnvFile" }
    $script:Settings = Read-DotEnv $EnvFile
    $required = @("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_SEARCH_API_KEY",
                  "AZURE_SEARCH_ENDPOINT", "PGHOST", "PGUSER", "PGPASSWORD",
                  "PGDATABASE", "JWT_SECRET_KEY")
    $missing = $required | Where-Object { -not $script:Settings.ContainsKey($_) }
    if ($missing) { Fail "the .env is missing: $($missing -join ', ')" }
    Note "$($script:Settings.Count) settings loaded from .env (values not shown)"

    $script:ImageTag = ((Get-NativeOutput git -C $RepoRoot rev-parse --short HEAD) | Out-String).Trim()
    if ((Get-NativeOutput git -C $RepoRoot status --porcelain) -ne $null) {
        $script:ImageTag = "$($script:ImageTag)-dirty"
        Note "working tree is dirty; tagging $($script:ImageTag)"
    }
    Note "image tag: $($script:ImageTag)"
}

# Parses a .env into a hashtable. Values are never echoed by this script; they
# travel from here into Container App secrets and nowhere else.
function Read-DotEnv([string] $Path) {
    $out = @{}
    foreach ($line in Get-Content $Path) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        $i = $t.IndexOf("=")
        if ($i -lt 1) { continue }
        $name = $t.Substring(0, $i).Trim()
        $value = $t.Substring($i + 1).Trim().Trim('"')
        if ($value -ne "") { $out[$name] = $value }
    }
    return $out
}

# ── 2. Test ─────────────────────────────────────────────────────
function Invoke-Tests {
    if ($SkipTests) { Note "tests skipped"; return }
    Step "Tests"
    Push-Location $RepoRoot
    try {
        # The tests gate; the linter reports.
        #
        # 1340 unit tests cover the deterministic layers and need no network, so
        # they are a real gate that costs seconds and a deploy should not pass
        # without them. `ruff check .` currently finds 37 pre-existing issues,
        # almost all import ordering and `Optional[x]` in Alembic's generated
        # migration templates -- files that are historical records of a schema
        # change, not code anybody edits. Blocking on those would mean either
        # rewriting migrations to satisfy a linter or, far worse, getting into
        # the habit of passing -SkipTests to get around it.
        if ((Invoke-Native uv run ruff check . --output-format=concise) -ne 0) {
            Note "lint findings above are advisory; they do not block."
        }
        if ((Invoke-Native uv run pytest -q) -ne 0) { Fail "tests failed. Nothing was deployed." }
    } finally { Pop-Location }
}

# ── 3. Infrastructure, created only if absent ───────────────────
function Initialize-Infrastructure {
    Step "Infrastructure"

    if ((Invoke-Az group exists -n $ResourceGroup) -ne "true") {
        Invoke-Az group create -n $ResourceGroup -l $Location -o none
        Note "created resource group $ResourceGroup"
    }

    $acr = Get-AzJson acr show -n $AcrName -g $ResourceGroup -o json
    if (-not $acr) {
        Invoke-Az acr create -g $ResourceGroup -n $AcrName --sku Basic --admin-enabled true -o none
        Note "created registry $AcrName"
    }
    $script:AcrServer = "$AcrName.azurecr.io"

    $capps = Get-AzJson containerapp env show -g $ResourceGroup -n $EnvName -o json
    if (-not $capps) {
        # Logs go somewhere durable from the start. An environment created with
        # --logs-destination none loses a failed replica's output the moment it
        # is reaped, which is how a failing job stayed undiagnosable long enough
        # to matter.
        $wsId  = Invoke-Az monitor log-analytics workspace show -g $LogsResourceGroup -n $LogsWorkspace --query customerId -o tsv
        $wsKey = Invoke-Az monitor log-analytics workspace get-shared-keys -g $LogsResourceGroup -n $LogsWorkspace --query primarySharedKey -o tsv
        Invoke-Az containerapp env create -g $ResourceGroup -n $EnvName -l $Location `
            --logs-destination log-analytics --logs-workspace-id $wsId --logs-workspace-key $wsKey -o none
        Note "created Container Apps environment $EnvName"
    }
    Note "environment: $EnvName"
}

# ── 4. Build and push ───────────────────────────────────────────
function Publish-Image {
    Step "Build and push image"
    Push-Location $RepoRoot
    try {
        # ACR Tasks is not available on this subscription
        # (TasksOperationsNotAllowed), so the image is built here and pushed,
        # rather than built server-side from an uploaded context.
        Invoke-Az acr login -n $AcrName -o none
        $image = "$($script:AcrServer)/pension-backend:$($script:ImageTag)"
        if ((Invoke-Native docker build -t $image -f Dockerfile .) -ne 0) { Fail "docker build failed." }
        if ((Invoke-Native docker push $image) -ne 0) { Fail "docker push failed." }
        $script:Image = $image
        Note "pushed $image"
    } finally { Pop-Location }
}

# Builds the --secrets and --env-vars argument lists.
#
# Every .env value becomes a Container App secret and every environment
# variable is a secretref to one. Deciding which of them are sensitive would be
# a judgement made once and wrong later; treating them uniformly cannot be.
function Get-ConfigArgs([hashtable] $Overrides) {
    $secretArgs = @(); $envArgs = @()
    foreach ($name in $script:Settings.Keys) {
        $secretName = $name.ToLower().Replace("_", "-")
        $secretArgs += "$secretName=$($script:Settings[$name])"
        $envArgs    += "$name=secretref:$secretName"
    }
    foreach ($name in $Overrides.Keys) {
        # A plain value, not a secret: these are settings, and a setting that
        # is visible in a diff is one somebody can check.
        $envArgs = @($envArgs | Where-Object { -not $_.StartsWith("$name=") })
        $envArgs += "$name=$($Overrides[$name])"
    }
    return @{ Secrets = $secretArgs; EnvVars = $envArgs }
}

# ── 5. Migrate, as a job inside Azure ───────────────────────────
#
# Not from wherever this script is running. A developer's laptop or a CI
# runner reaches Postgres from an address its firewall would have to admit,
# and a GitHub-hosted runner's address is as unpinnable as a Container App's.
# Run as a job in the same environment as the app, the migration is covered by
# whatever rule already lets the app connect -- and it runs the exact image
# about to be deployed, not a separately-resolved dependency tree.
function Invoke-Migrations {
    Step "Database migrations"
    $config = Get-ConfigArgs @{ APP_ENV = "pilot"; LOG_LEVEL = "INFO" }
    $acrUser = Invoke-Az acr credential show -n $AcrName --query username -o tsv
    $acrPass = Invoke-Az acr credential show -n $AcrName --query "passwords[0].value" -o tsv

    # `--args` takes SPACE-separated values, one argv entry each. Passing
    # "upgrade,head" as one string is not two arguments -- alembic received the
    # single token `upgrade,head` and refused it as an invalid choice. Note also
    # that az's own parser swallows anything beginning with a dash, so a flag
    # cannot be passed this way at all; a command needing one has to be wrapped
    # in a shell, or read its setting from the environment.
    # Deleted and recreated rather than updated.
    #
    # `az containerapp job update` has no way to replace the secret set, and
    # updating only the image leaves a job created with wrong arguments keeping
    # them for ever -- which is exactly what happened: a job whose entrypoint
    # was `alembic upgrade,head` stayed broken across a redeploy that changed
    # the image underneath it. This job is stateless and manually triggered, so
    # recreating it costs about twenty seconds and removes the whole class of
    # "the update did not change that" bug.
    $exists = Get-AzJson containerapp job show -g $ResourceGroup -n $MigrateJobName -o json
    if ($exists) {
        Invoke-Az containerapp job delete -g $ResourceGroup -n $MigrateJobName --yes -o none
    }
    Invoke-Az containerapp job create -g $ResourceGroup -n $MigrateJobName --environment $EnvName `
        --trigger-type Manual --replica-timeout 600 --replica-retry-limit 0 `
        --replica-completion-count 1 --parallelism 1 `
        --image $script:Image --registry-server $script:AcrServer `
        --registry-username $acrUser --registry-password $acrPass `
        --cpu 0.5 --memory 1.0Gi `
        --command "alembic" --args "upgrade" "head" `
        --secrets $config.Secrets --env-vars $config.EnvVars -o none

    $execution = (Get-AzJson containerapp job start -g $ResourceGroup -n $MigrateJobName -o json).name
    Note "migration job: $execution"
    $status = "Running"
    $waited = 0
    while ($status -eq "Running" -and $waited -lt 600) {
        Start-Sleep -Seconds 10; $waited += 10
        $status = Get-AzJson containerapp job execution show -g $ResourceGroup -n $MigrateJobName `
            --job-execution-name $execution --query properties.status -o json
    }
    if ($status -ne "Succeeded") {
        Fail "migrations did not succeed (status: $status). The previous revision is still serving. Logs: az containerapp job logs show -g $ResourceGroup -n $MigrateJobName --execution $execution"
    }
    Note "migrations applied"
}

# ── 6. Backend ──────────────────────────────────────────────────
function Deploy-Backend {
    Step "Backend"
    $config = Get-ConfigArgs @{ APP_ENV = "pilot"; LOG_LEVEL = "INFO" }
    $exists = Get-AzJson containerapp show -g $ResourceGroup -n $AppName -o json

    if ($exists) {
        Invoke-Az containerapp update -g $ResourceGroup -n $AppName --image $script:Image `
            --set-env-vars $config.EnvVars -o none
    } else {
        $acrUser = Invoke-Az acr credential show -n $AcrName --query username -o tsv
        $acrPass = Invoke-Az acr credential show -n $AcrName --query "passwords[0].value" -o tsv
        # min-replicas 1 is a deliberate cost decision. At zero, the first
        # request after an idle period pays container start plus the ~5s
        # warm-up that `_warm_connections` does, which turns a measured 8s
        # answer into a 30s one in front of whoever asks first. `scale.ps1`
        # drops it to zero between demos.
        Invoke-Az containerapp create -g $ResourceGroup -n $AppName --environment $EnvName `
            --image $script:Image --registry-server $script:AcrServer `
            --registry-username $acrUser --registry-password $acrPass `
            --target-port 8000 --ingress external --transport auto `
            --min-replicas 1 --max-replicas 3 --cpu 1.0 --memory 2.0Gi `
            --secrets $config.Secrets --env-vars $config.EnvVars -o none
    }

    $script:ApiFqdn = Invoke-Az containerapp show -g $ResourceGroup -n $AppName `
        --query properties.configuration.ingress.fqdn -o tsv
    $script:ApiBase = "https://$($script:ApiFqdn)/api/v1"
    Note "backend: https://$($script:ApiFqdn)"
}

# ── 7. Frontend ─────────────────────────────────────────────────
function Deploy-Frontend {
    Step "Frontend"
    if (-not $script:ApiFqdn) {
        $script:ApiFqdn = Invoke-Az containerapp show -g $ResourceGroup -n $AppName `
            --query properties.configuration.ingress.fqdn -o tsv
        $script:ApiBase = "https://$($script:ApiFqdn)/api/v1"
    }

    $swa = Get-AzJson staticwebapp show -g $ResourceGroup -n $SwaName -o json
    if (-not $swa) {
        Invoke-Az staticwebapp create -g $ResourceGroup -n $SwaName -l eastus2 --sku Free -o none
        $swa = Get-AzJson staticwebapp show -g $ResourceGroup -n $SwaName -o json
        Note "created static web app $SwaName"
    }

    Push-Location (Join-Path $RepoRoot "frontend")
    try {
        # Baked in at build time, which is why the backend had to exist first.
        $env:NEXT_PUBLIC_API_URL = $script:ApiBase
        # Judged by what they produced, not only by what they returned.
        #
        # `npm ci` on Windows exits non-zero over EPERM warnings from its own
        # cleanup -- unable to remove a directory another process still holds --
        # having installed all 776 packages perfectly well. Failing the deploy
        # on that is failing on a file lock. What actually matters is whether
        # node_modules is populated and whether the export produced a page, so
        # that is what is checked.
        $npmCi = Invoke-Native npm ci
        if (-not (Test-Path "node_modules")) { Fail "npm ci did not install node_modules (exit $npmCi)." }
        if ($npmCi -ne 0) { Note "npm ci exited $npmCi but node_modules is present; continuing." }

        $build = Invoke-Native npm run build
        if (-not (Test-Path (Join-Path "out" "index.html"))) {
            Fail "the frontend build produced no out/index.html (exit $build)."
        }

        $token = Invoke-Az staticwebapp secrets list -g $ResourceGroup -n $SwaName `
            --query "properties.apiKey" -o tsv
        if ((Invoke-Native npx --yes @azure/static-web-apps-cli deploy ./out --deployment-token $token --env production) -ne 0) {
            Fail "the frontend upload failed."
        }
    } finally { Pop-Location; Remove-Item Env:NEXT_PUBLIC_API_URL -ErrorAction SilentlyContinue }

    $script:WebHost = $swa.defaultHostname
    Note "frontend: https://$($script:WebHost)"
}

# ── 8. Close the loop ───────────────────────────────────────────
#
# Each of the two needs the other's address and only one can go first, so the
# backend learns where the frontend lives once the frontend exists.
function Update-Cors {
    Step "CORS"
    $origins = "https://$($script:WebHost)"
    Invoke-Az containerapp update -g $ResourceGroup -n $AppName `
        --set-env-vars "CORS_ORIGINS=$origins" -o none
    Note "backend now accepts requests from $origins"
}

# ── 9. Smoke ────────────────────────────────────────────────────
#
# A deploy is not "the script exited 0". The spike's failure was a process
# that started perfectly and could not reach a dependency, so the check that
# matters is a real turn producing a real token.
function Test-Deployment {
    Step "Smoke test"

    $health = "https://$($script:ApiFqdn)/api/v1/health"
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $health -TimeoutSec 10 -UseBasicParsing
            if ($r.StatusCode -eq 200) { Note "health: 200"; break }
        } catch { Start-Sleep -Seconds 5 }
        if ($i -eq 29) { Fail "health never returned 200 at $health" }
    }

    $session = Invoke-RestMethod -Uri "https://$($script:ApiFqdn)/api/v1/sessions" -Method Post `
        -ContentType "application/json" -Body "{}" -TimeoutSec 30
    $sessionId = $session.session_id
    if (-not $sessionId) { Fail "could not open an anonymous session." }
    Note "anonymous session: $sessionId"

    $body = @{ content = "What does Section 11-134 say?" } | ConvertTo-Json
    $started = Get-Date
    $answer = Invoke-WebRequest -Uri "https://$($script:ApiFqdn)/api/v1/sessions/$sessionId/messages" `
        -Method Post -ContentType "application/json" `
        -Headers @{ "X-Session-Token" = $session.session_token } `
        -Body $body -TimeoutSec 180 -UseBasicParsing
    $elapsed = ((Get-Date) - $started).TotalSeconds

    # `event: token` specifically, not the word anywhere in the body -- the
    # session token is in this exchange too, and a check that a failing deploy
    # can pass is worse than no check.
    if ($answer.Content -notmatch "event: token") {
        Fail "the API answered but streamed no tokens. It is running and cannot reach something it needs."
    }
    if ($answer.Content -match "event: error") {
        Fail "the stream carried an error event. See the app logs."
    }
    Note ("a real turn answered in {0:N1}s" -f $elapsed)
}

# ── Run ─────────────────────────────────────────────────────────
Invoke-Preflight
if (-not $FrontendOnly) {
    Invoke-Tests
    Initialize-Infrastructure
    Publish-Image
    Invoke-Migrations
    Deploy-Backend
}
if (-not $BackendOnly) {
    Deploy-Frontend
    Update-Cors
}
if (-not $FrontendOnly) { Test-Deployment }

Write-Host "`nDeployed." -ForegroundColor Green
if ($script:ApiFqdn) { Write-Host "  api  https://$($script:ApiFqdn)" }
if ($script:WebHost) { Write-Host "  web  https://$($script:WebHost)" }
Write-Host "  Between demos, ./deploy/scale.ps1 -Replicas 0 stops the compute charge."
