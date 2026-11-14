# Teardown

**Pausing needs no action.** With `--min-replicas 0`, the container app scales to zero about 5 minutes after the last request, and Neon suspends compute after 5 minutes idle. Idle cost is $0 on Azure (no environment fee on the Consumption plan without dedicated profiles) and $0 on the Neon free plan.

To remove everything (each step is irreversible):

```bash
# 1. Azure: delete the whole resource group (container app, environment, anything else inside)
az group delete -n rg-mcp-demo --yes --no-wait
az group wait -n rg-mcp-demo --deleted        # optional: block until gone
# The budget is scoped to the RG; delete it if it still lists:
az consumption budget delete-with-rg -g rg-mcp-demo -n budget-mcp-demo 2>/dev/null || true
# Container Apps also creates a managed infra RG (ME_... / MC_...): confirm it disappeared
az group list --query "[?contains(name,'mcp-demo')].name" -o tsv

# 2. Neon: delete the project (Console → helpdesk-demo → Settings → Delete project), or with the CLI:
neon projects delete <project-id>      # helpdesk-demo; do NOT touch vigneshcraft

# 3. GitHub: delete the package, then the repo
#    GitHub → Packages → helpdesk-mcp-demo → Package settings → Delete this package
gh repo delete <owner>/helpdesk-mcp-demo --yes

# 4. Local secrets
rm -f .env .neon.env
```

## Verify zero cost
- Azure portal → Cost Management → Cost analysis, scope = subscription, group by resource group: `rg-mcp-demo` should stop accruing (data can lag up to ~24 h). `az resource list -g rg-mcp-demo` should report that the RG doesn't exist.
- Neon Console: the project is gone from the list; the free plan has no bill anyway.
- GitHub → Settings → Billing: Packages/Actions show $0.
