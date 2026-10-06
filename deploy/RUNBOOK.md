# Production deploy runbook: pension-chat backend v1 (2026-10-07)

**Target:** the client's Kubernetes cluster, internal network only.
**Front end:** built by the client's team in Blazor against `docs/client-integration/blazor-frontend-api-spec.md`.
**Release:** the `main` branch at the tag `v1.0.0`.

## 0. Before the day: client actions

| # | Action | Who | Needed for |
|---|---|---|---|
| 1 | **Rotate keys:** Azure OpenAI (`novitas-ai`), AI Search (`novitasaisearch`) admin key, the Postgres `pension_app` password. They appeared in a development session's output. | Client Azure admin | Secret |
| 2 | **Create the read-only PBMS login:** run `deploy/sql/pbms_readonly_login.sql` on `newncgsql`. Never deploy with `sa`. | Client DBA | Secret |
| 3 | **Confirm the PBMS database:** the demo used `PbsMeabDev`; production may be a different database | Client | ConfigMap |
| 4 | Provide the cluster, a container registry, a namespace, the secret store, an internal ingress class, a hostname and a TLS certificate | Client platform team | Steps 2–5 |
| 5 | Give the Blazor app's browser origin (for CORS), and say whether it calls from the browser (WebAssembly) or the server | Client front-end team | ConfigMap |
| 6 | **Network egress from the cluster:** to `novitas-ai.openai.azure.com`, `novitasaisearch.search.windows.net`, `novitasaidb.postgres.database.azure.com:5432`, and `newncgsql.centralus.cloudapp.azure.com:2022` | Client network team | Start-up |

## 1. Build and push the image

```bash
git checkout v1.0.0
docker build -t <registry>/pension-chat:v1.0.0 .
docker push <registry>/pension-chat:v1.0.0
```

The image has no secrets in it (`.dockerignore` excludes `.env`). It includes Microsoft ODBC Driver 18 for the PBMS reads.

## 2. Configure

1. Edit `deploy/k8s/configmap.yaml`. Every `CONFIRM` value must be replaced: `CORS_ORIGINS` and `PBMS_SQL_DATABASE`.
2. Create the Secret from the client's secret store. The keys are listed in `deploy/k8s/secret.example.yaml`.
   - **`PGUSER` must be `pension_app`.** `novitasai_admin` bypasses row-level security.
   - **`JWT_SECRET_KEY` must be 32+ random characters.** With `APP_ENV=production`, the app refuses to start otherwise.
3. Set the image in `deploy/k8s/deployment.yaml`.

## 3. Database

The database is already at the current migration (009). Check before deploying:

```bash
# from a machine with the repo and the production .env, or a one-off pod using the image
uv run alembic current        # expect 009 (head)
```

No migration runs as part of this release.

## 4. Apply

```bash
kubectl -n <ns> apply -f deploy/k8s/configmap.yaml
kubectl -n <ns> apply -f deploy/k8s/deployment.yaml
kubectl -n <ns> apply -f <the client's ingress, adapted from deploy/k8s/ingress.example.yaml>
kubectl -n <ns> rollout status deploy/pension-chat --timeout=5m
```

Start-up takes about 20–60 s: it warms connections, loads the corpus graph and the PBMS name directory. **One replica only** (see the comment in `deployment.yaml`).

**If you use an ingress other than NGINX:** response buffering must be off and the read timeout at least 180 s, or streamed answers arrive all at once or get cut off.

## 5. Smoke test (about 5 minutes)

```bash
kubectl -n <ns> logs deploy/pension-chat | grep -E "Warmed|name directory loaded|ERROR"
# expect: Warmed database/search/model/corpus connection; "PBMS name directory loaded: <n> names"

curl -s https://<host>/api/v1/health
# expect {"status":"healthy",...,"record_source":"pbms"}
```

Then run the API smoke test from a machine on the internal network:

```bash
uv run python scripts/api_smoke.py https://<host>/api/v1
```

It creates a session, asks three questions (a statute question, a described-employee calculation, a what-if), and checks citations, cards, and reload.

**Expected:**
- the statute answer cites §8-137;
- the calculation gives **$15,417.60 a year** with a card;
- the follow-up gives **$30,720.00 at 67**;
- reload returns 6 messages.

Note the "first word" times it prints: these are the real in-region latencies.

Also ask one named-employee question through the Blazor UI once it's connected, to confirm PBMS reads work from the cluster.

## 6. Go / no-go

| Check | Go if |
|---|---|
| Health | 200, `record_source: pbms` |
| PBMS | Name directory loaded; a named-employee question returns their record |
| Smoke test | All three answers verified, both figures exact |
| Secrets | Rotated keys in use; PBMS login is not `sa`; `PGUSER=pension_app` |
| Logs | No `ERROR` lines after start-up |

## 7. Rollback

```bash
kubectl -n <ns> rollout undo deploy/pension-chat
```

The release makes no schema changes, so rolling back the image is the whole rollback.

**If figures look wrong for some benefit:** set `DERIVED_RECIPE_EXECUTION_POLICY=no_execute` in the ConfigMap and restart. Only approved formulas then produce figures; other benefits get a cited statute answer without a number.

## 8. Known limits of v1

- **One replica, conversations in memory:** a restart or redeploy ends open conversations (v1 scope: single-session chats).
- **No sign-in.** Access control is the internal network. Anyone who can reach the service can look up employees by name.
- **First-time calculations for benefits without an approved formula** take 25–60 s, and the result is shown as an estimate.
- **Latency targets** (3 s for most turns) aren't met yet: about 5 s estimated in-region. This release's job is to measure the real figure.
