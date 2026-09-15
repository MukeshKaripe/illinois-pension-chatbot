# Pilot Deployment — Design

**Date:** 2026-09-15
**Status:** Approved in brainstorming across three sections; built the same day.
**Scope:** Hosting the backend and frontend on Azure for a demonstration to real Illinois fund members, and leaving behind a recipe that ports to the client's subscription.

## 1. Why

Time-to-first-token was 25.4s at the median. Half a day of work on the agent's own waits brought it to 17.6s, and instrumentation said the rest was not thinking: **about 24 network round trips per turn**, at 238ms each, from a developer's machine in Hyderabad to Azure resources in the United States. The fix for that is not code.

Deploying is therefore a latency change first and a distribution change second. It was measured before it was designed — a Container App in Central US answered the same fifteen turns at a **median 8.23s against 15.30s locally**, 14 of 15 faster, paired and run back to back.

The second reason is narrower and shapes everything below. This deployment is temporary. It runs on an Azure free trial that **expires on 2026-09-17**, for a demonstration on 2026-09-16, after which the work moves to resources the client provides. So the deliverable is not the running instance — it is `deploy/deploy.ps1` and this document. The instance is what proves they work.

## 2. Decisions and their reasons

| Decision | Reason |
|---|---|
| Backend in **Central US**, no services moved. | Per turn: ~16–17 Postgres calls, 3–8 Search calls, 3–4 model calls. Central US already holds 23 of those ~27 hops. The four model calls then pay a ~40ms cross-region hop — under a tenth of the model's own call-to-call variance, and far cheaper than re-ingesting 4,766 documents into a new index and dump/restoring Postgres. |
| **A script, not a CI pipeline.** | The question that settled it: what actually ports? The image, the stage ordering and the findings port intact. A federated credential, repository secrets and a Static Web Apps deploy token do not — they are re-created in the client's tenant — and the workflow file itself is worthless if the client uses Azure DevOps. For a demo driven by one developer, CI buys convenience, not correctness. CI, when wanted, is a ~20-line wrapper: check out, log in, run the script. |
| **Migrations as a Container App Job**, not from wherever the deploy runs. | A laptop or a CI runner reaches Postgres from an address the firewall must admit, and a GitHub-hosted runner's address is as unpinnable as a Container App's. Run inside the environment, the migration is covered by whatever rule already lets the app connect — and it runs the *exact image* about to be deployed rather than a separately-resolved dependency tree. |
| **`min-replicas: 1`**, with a script to drop it to 0. | At zero, the first request after idle pays container start plus the ~5s `_warm_connections` warm-up: a measured 8s answer becomes ~30s for whoever asks first. In a live demonstration to members that is the worst possible moment. ~$25–30/mo, paid on demo days and not otherwise. |
| **No Key Vault.** Secrets are Container App secrets from a local `.env`. | One fewer resource to reproduce and one fewer place for a secret to go stale, for a demo whose credentials all rotate when the work moves anyway. Key Vault is named below as the client-environment substitution — one function in the script. |
| **Frontend as a static export** on Static Web Apps Free. | Verified: no server actions, no route handlers, no `cookies()`/`next/headers`; all five routes prerender. It needs no Node runtime, so it costs nothing and gets a CDN and managed TLS. |
| **Backend deployed before the frontend is built.** | `NEXT_PUBLIC_API_URL` is baked in at build time, not read at run time. Each half needs the other's address and only one can go first. |
| **Tests gate the deploy; lint reports.** | 1340 unit tests cover the deterministic layers and need no network — seconds, and a real gate. `ruff check .` finds 37 pre-existing issues, almost all import ordering and `Optional[x]` in Alembic's generated migration templates: files that are historical records of a schema change. Blocking on them would mean rewriting migrations to please a linter, or acquiring the habit of passing `-SkipTests`. |
| **Existing JWT auth; Entra External ID is Phase 2.** | `api/auth.py`, the `accounts`/profiles model and the RLS policies already work and already depend on it. Entra is a new CIAM tenant, a new user flow, and real work mapping a subject claim onto `accounts`. The seam is documented in §7 rather than discovered. |
| **SDK loggers pinned to WARNING**, independent of `LOG_LEVEL`. | At DEBUG, `azure.core`'s HTTP policy logs every request and response header. Shipped to Log Analytics that is thousands of lines a turn, an ingestion bill, and — now that real members are involved — request metadata retained where nobody decided to retain it. Raising our own level to debug our own code must not turn the transport into a firehose. |
| **One database and index for both development and the pilot.** | Accepted deliberately: the deployment is throwaway, the budget is a nearly-spent trial, and the real environment is the client's. **This is the decision to revisit first** — see §6. |

## 3. Topology

Everything in `centralus` except the models.

| Resource | Status | Cost |
|---|---|---|
| `pensionspikeacr` — container registry, Basic | reused from the latency spike | ~$5/mo |
| `pension-spike-env` — Container Apps environment | reused; name is historical | — |
| `ca-pension-api` — backend, min 1 / max 3, 1 vCPU / 2 GiB | new | ~$25–30/mo |
| `swa-pension-web` — Static Web App, Free | new | $0 |
| `psql-pension-dev` — Postgres B1ms, `mediq-rg` | existing | ~$13/mo |
| `search-mediq-dev` — AI Search **Basic** | existing | **~$75/mo** |
| `maniyarjaffer-1642-resource` — Azure OpenAI, **westus3** | existing | usage |
| `pension-chatbot-logs-dev` — Log Analytics | existing | usage |

Net new: **~$30–35/mo**, essentially all of it the always-on replica.

## 4. The deploy script

`deploy/deploy.ps1`, nine idempotent stages. On a subscription with nothing it creates everything; on one that already has them it updates. Parameters — subscription, resource group, region, resource names — are a block at the top, so porting is editing eight lines.

1. **Preflight** — logged in, right subscription, Docker up, `.env` complete. Each check corresponds to a way a deploy actually broke.
2. **Test** — `pytest` gates, `ruff` reports.
3. **Infrastructure** — create registry / environment if absent. The environment is created with a Log Analytics destination from the start; one created with `--logs-destination none` loses a failed replica's output the moment it is reaped, which is how a failing job stayed undiagnosable long enough to matter.
4. **Build and push** — tagged with the git SHA, never `:latest`, so a revision names the commit that produced it and a rollback means naming a tag that still exists.
5. **Migrate** — `alembic upgrade head` as a job on the just-pushed image. Failure aborts the deploy with the previous revision still serving.
6. **Backend** — `az containerapp update --image <sha>`; read back the FQDN.
7. **Frontend** — `next build` with `NEXT_PUBLIC_API_URL` from step 6; upload to Static Web Apps.
8. **CORS** — the backend learns the frontend's hostname, closing the circular dependency.
9. **Smoke** — health 200, then **a real turn that streams a real token**. A deploy is not "the script exited 0": the spike's failure was a process that started perfectly and could not reach a dependency, and a check that a broken deploy can pass is worse than no check.

Companions: `scale.ps1 -Replicas 0|1` (the demo-day switch) and `teardown.ps1` (dry-run by default; deliberately does not touch the database, the index or the OpenAI resource, and reminds you the Postgres firewall rule lives in another resource group).

## 5. What the spike cost, so it is not rediscovered

Four findings, each of which cost a failed run.

- **ACR Tasks is not permitted on this subscription** (`TasksOperationsNotAllowed`). No `az acr build`, no `az containerapp up --source`. The image is built wherever the deploy runs and pushed.
- **A Consumption environment's egress IP cannot be pinned.** The environment's `staticIp` is *ingress*; egress comes from a shared SNAT pool. Measured: `40.77.80.56`, then `104.43.193.0`, then `104.43.204.253` across three consecutive executions. A single-IP firewall rule is not a design, it is a coincidence.
- **Postgres drops packets from an unknown address rather than refusing them**, so the client waits rather than failing. Every one of fifteen benchmark turns spent a full 60s timeout inside the checkpointer and reported only "Something went wrong". `bench_ttft.py` now reports its own egress address and refuses to start against an unreachable database.
- **`LOG_LEVEL=DEBUG` in `.env`** turned the container's logs into Azure SDK request and response headers.

## 6. Accepted risks

| Risk | Why accepted | What removes it |
|---|---|---|
| Real member conversations land in `session_messages` on a database named `-dev` that development also targets. | Throwaway infrastructure, spent trial, real environment is the client's. | A separate database wherever real pension conversations persist. **Required, not optional, on client infrastructure.** |
| `AllowAzureServices-pilot` lets any Azure resource reach Postgres at network level. | Credentials and RLS still apply; egress cannot be pinned; a NAT Gateway is $35/mo of a nearly-spent trial. | VNet integration with a NAT Gateway, or a private endpoint. |
| A public URL with an LLM behind it and a trial balance behind that. | Per-turn cost is bounded by `BUDGETS` (20 tool calls / 90s) and the deployment's TPM quota; the window is ~2 days. | A budget alert, an unpublished URL, `teardown.ps1` between demos. Rate limiting by session token if the URL goes anywhere public. |
| A migration must stay backwards-compatible with the running revision for the length of a deploy. | One replica, pilot traffic, a window of seconds. | Nothing cheap — it is inherent to deploying migrations ahead of code. Worth knowing before writing a `DROP COLUMN`. |
| One replica: a restart costs the next asker ~30s. | Pilot traffic is one or two people. | More replicas, which costs proportionally. |
| **The trial expires 2026-09-17.** The deployed URL has a ~2-day life, and the index and database are disabled with it. | The demonstration is on the 16th. | A Pay-As-You-Go upgrade, at ~$90/mo for the existing resource set — of which AI Search Basic is ~$75. A decision due independently of this deployment. |

## 7. Deferred, with the seam named

- **Entra External ID** replacing the application's JWT. `accounts` would hang off an Entra subject claim rather than an email/password row; `api/auth.py` validates Entra tokens; `src/lib/api.ts` acquires them. The RLS policies, keyed to `employee_id`, do not change — which is what makes this a seam rather than a rewrite.
- **Key Vault** replacing Container App secrets: one function in `deploy.ps1`.
- **VNet + NAT Gateway, or a private endpoint** for Postgres, replacing the allow-Azure-services rule.
- **A separate pilot database.**
- **CI** as a thin wrapper around the script.
- **Custom domain and WAF.**
- **The model deployment's tail.** One turn in fifteen took 28.07s, 21s of it inside a single model call; the same shape appeared locally at 26.4s. That is the deployment stalling, not this code, and it is now the largest threat to any latency promise. The one structured log line per turn (TTFT, tokens, guardrail verdict, whether a figure was computed) is what makes it visible if it recurs.

## 8. Outcome, and one thing to do differently

Deployed 2026-09-15.

| | |
|---|---|
| Backend | `https://ca-pension-api.victoriousgrass-6e1958ec.centralus.azurecontainerapps.io` |
| Frontend | `https://red-desert-0145ab00f.5.azurestaticapps.net` |
| Health | 200 |
| A real turn | 518 token events, cited answer from §11-134, `done` received |
| CORS | preflight 200 with the frontend origin echoed |
| TTFT through the public endpoint, measured from India | 8.4s / 11.0s / 16.7s |

The last number is **not** comparable to the 8.23s benchmark. That was measured
inside Azure against a seeded persona; this is a handful of anonymous samples
carrying a developer's own transcontinental latency and doing different work
(no member record to fetch). For a member in Illinois the in-Azure figure is the
better predictor.

**`deploy.ps1` should be ported to bash.** It took nine runs to get through,
and with one exception every failure was Windows PowerShell rather than Azure:
`Join-Path`'s three-argument form does not exist in 5.1; an advanced function
acquires common parameters, so az's `-o` is refused as ambiguous before az sees
it; a native command's redirected stderr becomes ErrorRecords that terminate
the script over an informational warning; `@($args[1..n])` in argument position
is an array subexpression rather than splatting, so npm received the literal
string `args[0]`; and `npm ci` exits non-zero over EPERM warnings from its own
cleanup after installing perfectly. Each fix was correct and each revealed the
next. The frontend stage was ultimately completed by running the same commands
in bash, where they worked first time.

PowerShell was chosen because it is the author's shell and `pwsh` is
cross-platform. That reasoning is outweighed: bash avoids the entire
ErrorRecord class, Git Bash is already present on the machine, and the client's
CI will most likely be Linux. **The stage logic in this document is correct and
proven; the language it is written in is not the one to port.**

The genuinely non-PowerShell failures are worth keeping: `Microsoft.Web` was
unregistered (now handled in preflight, along with three others), and
`az containerapp job update` cannot replace a secret set, so the migration job
is deleted and recreated each run.

## 9. What "deployed" means

1. `GET /api/v1/health` returns 200.
2. A turn through the deployed API streams a token — automated in stage 9.
3. `bench_ttft.py` from inside the Container App confirms TTFT is still ~8s.
4. The frontend loads; anonymous chat works; a signed-in profile works.
5. `evals/fund_smoke.yaml` passes 18/18 against the deployed stack.
