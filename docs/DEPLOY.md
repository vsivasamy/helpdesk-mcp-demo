# Deploy: Neon + GitHub/GHCR + Azure Container Apps

This environment is already live. The public MCP URL is:

`https://helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io/mcp`

Order matters if you rebuild it. Every step that **creates something external** is marked 🔒 (needs a yes in chat first).
Names used: Neon project `helpdesk-demo`, GitHub repo `helpdesk-mcp-demo`, Azure RG `rg-mcp-demo`,
environment `cae-mcp-demo`, app `helpdesk-mcp`, budget `budget-mcp-demo`.

**Region:** the Neon project is in AWS `ap-southeast-1` (Singapore). Container Apps is in **`southeastasia`** (Singapore) so app↔DB latency stays low. `centralindia` also supports the Consumption plan (closer to users in India, but every query then crosses to Singapore). The Consumption meter rates are identical in both regions.

---

## 1. Neon database ✅ done

Project `helpdesk-demo` (id `fragrant-recipe-18449662`), Postgres 17, AWS `ap-southeast-1`, free plan. Seeded with **400 tickets, 30 open**.

Seeding runs from the box with one command. It reads `.neon.env`, uses the **direct** endpoint for DDL, creates the `helpdesk_ro`/`helpdesk_rw` roles and is idempotent (drop and recreate):

```bash
cd /workspace/mcp-demo
.venv/bin/python scripts/seed_neon.py            # seed (SEED_TICKETS=400 default; e.g. SEED_TICKETS=100 for smaller)
.venv/bin/python scripts/seed_neon.py --check    # row counts as helpdesk_ro via the pooled endpoint
```

`.neon.env` (mode 600, git-ignored) holds `NEON_ADMIN_URL` (owner, pooled), `NEON_RO_PASSWORD`, `NEON_RW_PASSWORD`, `NEON_RO_URL` and `NEON_RW_URL`. Never commit it.

The box's network blocks raw Postgres on 5432, so the script tunnels automatically over Neon's official WebSocket endpoint (`wss://<host>/v2`, via `scripts/neon_ws_bridge.py`). On a normal machine, in CI or in Azure, 5432 works directly and no bridge is used.

If `CREATE ROLE` were refused, the seed still commits the data and prints the options: create the role with `neon roles create --name helpdesk_ro --access read_only` and re-run with `SEED_TICKETS=... SEED_SKIP_ROLES=true`, or run the server with the owner URL (every read is still forced into a READ ONLY transaction). On this project it worked: `helpdesk_owner` has CREATEROLE.

The server's `DATABASE_URL` is `NEON_RO_URL`: the **pooled** host (`-pooler`) with `sslmode=require&channel_binding=require`. The server is pooler-safe because it uses no session `SET`s, only `SET LOCAL` inside each transaction, and no server-side prepared statements.

## 2. ✅ GitHub repo + image

Repo: [vsivasamy/helpdesk-mcp-demo](https://github.com/vsivasamy/helpdesk-mcp-demo) (public). Image: `ghcr.io/vsivasamy/helpdesk-mcp-demo:latest` (public).

```bash
cd /workspace/mcp-demo
git init -b main && git add . && git status --short   # confirm .env / .neon.env / .local are NOT listed
git commit -m "Helpdesk MCP demo"
gh repo create helpdesk-mcp-demo --public --source . --push     # or --private (the package can still be public)
gh run watch                                                     # workflow build-and-push → ghcr.io/vsivasamy/helpdesk-mcp-demo
```

The package is created private on the first push; this one was then made public. Check with:

```bash
docker manifest inspect ghcr.io/vsivasamy/helpdesk-mcp-demo:latest >/dev/null && echo "public pull OK"
# or without Docker:
curl -s "https://ghcr.io/token?scope=repository:vsivasamy/helpdesk-mcp-demo:pull" | grep -q token && echo "anonymous token OK"
```

## 3. Azure (Pay-As-You-Go subscription) ✅ done

```bash
az login --use-device-code
az account set --subscription "Pay-As-You-Go"
az extension add --name containerapp --upgrade

# one-off per subscription (free; idempotent)
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait

LOC=southeastasia
OWNER=vsivasamy
```

🔒 **3a. Resource group** — created as `rg-mcp-demo` in `southeastasia`.
```bash
az group create -n rg-mcp-demo -l $LOC --tags purpose=mcp-demo owner=vignesh
```

🔒 **3b. Budget alert (₹100/month on the RG).** Created as `budget-mcp-demo`. It alerts by email but does **not** cap spend. The amount is in the **billing currency** (INR on this subscription).
```bash
# Example of the notifications payload; use the Budgets REST API or Cost Management in the portal
# if `az consumption budget` is unavailable. Alerts at 50% and 100% actual, and 100% forecast.
az consumption budget create-with-rg -g rg-mcp-demo -n budget-mcp-demo \
  --amount 100 --category Cost --time-grain Monthly \
  --time-period start-date=2026-11-01 end-date=2027-11-01 \
  --notifications @/tmp/budget-notifications.json
```
Portal alternative: Cost Management → Budgets → + Add → scope `rg-mcp-demo` → Monthly, amount 100 → alert conditions 50/100 % actual and 100 % forecast → email.

🔒 **3c. Container Apps environment.** Created as `cae-mcp-demo`. Consumption-only, **no Log Analytics** (avoids ingestion charges).
```bash
az containerapp env create -n cae-mcp-demo -g rg-mcp-demo -l $LOC \
  --logs-destination none
# don't add dedicated workload profiles, private endpoints or planned maintenance (those carry a $0.10/h management fee)
```

🔒 **3d. Container app** — created as `helpdesk-mcp`. Public GHCR image, so no registry credentials; scale to zero; `DATABASE_URL` stored as secret `database-url`; `ENABLE_WRITES=false`.
```bash
NEON_RO_URL='<paste from .neon.env - never commit>'
az containerapp create -n helpdesk-mcp -g rg-mcp-demo --environment cae-mcp-demo \
  --image ghcr.io/$OWNER/helpdesk-mcp-demo:latest \
  --ingress external --target-port 8000 --transport auto \
  --min-replicas 0 --max-replicas 1 \
  --cpu 0.25 --memory 0.5Gi \
  --scale-rule-name http-rule --scale-rule-type http --scale-rule-http-concurrency 20 \
  --secrets database-url="$NEON_RO_URL" \
  --env-vars DATABASE_URL=secretref:database-url ENABLE_WRITES=false ALLOWED_HOSTS='*.azurecontainerapps.io' STATEMENT_TIMEOUT_MS=5000
unset NEON_RO_URL

FQDN=$(az containerapp show -n helpdesk-mcp -g rg-mcp-demo --query properties.configuration.ingress.fqdn -o tsv)
echo "https://$FQDN/mcp"
```
- No `MCP_API_KEY` means no-auth mode (the agreed default for this fake-data demo). To lock it down later: `az containerapp secret set ... --secrets mcp-key=<random>` then `az containerapp update ... --set-env-vars MCP_API_KEY=secretref:mcp-key`.
- **Cold start:** the first request after idle waits while a replica starts (about 30 seconds observed) and Neon resumes (a few hundred ms). Warm requests take about 2 seconds. The server is stateless (no MCP session affinity needed), opens DB connections lazily and retries once on a dropped connection. `/health` doesn't touch the DB.

## 4. Verify (read-only, no cost beyond free grants) ✅ done 14 Nov 2026

The live FQDN is `helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io`.

```bash
FQDN=helpdesk-mcp.agreeablecoast-bdbb216a.southeastasia.azurecontainerapps.io
curl -s https://$FQDN/health                 # {"status":"ok","auth":"none",...}
curl -s https://$FQDN/health/db              # wakes Neon; {"db":"ok"}
MCP_URL=https://$FQDN/mcp SKIP_HOST_CHECKS=true .venv/bin/python tests/smoke_client.py
npx @modelcontextprotocol/inspector --cli https://$FQDN/mcp --transport http --method tools/list
```

Results: smoke test **20/20** passed. Inspector `tools/list` showed `search_tickets`, `get_ticket`, `ticket_stats`, `list_open_by_priority` and `sla_breaches` only (no write tools). Cold start about 29 seconds; warm about 2 seconds. Client recipes: [CONNECT.md](CONNECT.md).

## Updating the app
Push to `main`, wait for the build, then:
```bash
az containerapp update -n helpdesk-mcp -g rg-mcp-demo --image ghcr.io/vsivasamy/helpdesk-mcp-demo:sha-<7charsha>
```

A docs-only push still rebuilds the image (`:latest` and `:sha-<sha>`), but does not change the running app until you run the update (or enable the optional CI deploy job).

### Optional: CI deploy (OIDC, no stored passwords)
1. 🔒 Create an Entra app registration and service principal, then add a **federated credential** for `repo:vsivasamy/helpdesk-mcp-demo:ref:refs/heads/main`.
2. 🔒 Grant it **Contributor on `rg-mcp-demo` only**: `az role assignment create --assignee <appId> --role Contributor --scope /subscriptions/<sub>/resourceGroups/rg-mcp-demo`.
3. Repo secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`; repo variable `DEPLOY_TO_ACA=true`.

The `deploy` job in `build-and-push.yml` then rolls out `:sha-<sha>` after each build. It's skipped while the variable is unset.

## Cost summary (Nov 2026 list prices)
| Item | Free allowance | After that |
|---|---|---|
| ACA Consumption | 180,000 vCPU-s, 360,000 GiB-s, 2M requests per subscription per month | $0.000024/vCPU-s active, $0.000003/GiB-s, $0.40 per 1M requests |
| This app (0.25 vCPU / 0.5 GiB) | ≈ 200 replica-hours a month free (each wake lasts at least the 300 s cooldown, so ≈ 2,400 wake-ups) | ≈ $0.027 per replica-hour |
| Log Analytics | not created (`--logs-destination none`) | would be 5 GB/month free, then $3.22/GB in centralindia |
| Neon Free | 100 CU-h per project, 1 GB storage, 5 GB egress, scale-to-zero after 5 min | hard limits: compute suspends, no bill |
| GHCR public package | free | free |
| Budget alert | `budget-mcp-demo` at ₹100/month on `rg-mcp-demo` (email only; does not cap spend) | — |
