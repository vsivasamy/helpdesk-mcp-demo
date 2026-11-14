# Teardown

**Pausing needs no action.** With `--min-replicas 0`, the container app `helpdesk-mcp` scales to zero about 5 minutes after the last request, and Neon project `helpdesk-demo` suspends compute after 5 minutes idle. Idle cost is $0 on Azure (no environment fee on the Consumption plan without dedicated profiles) and $0 on the Neon free plan.

To remove everything (each step is irreversible):

```bash
# 1. Azure: delete the whole resource group (container app, environment, budget)
az group delete -n rg-mcp-demo --yes
# optional: return immediately and wait separately
# az group delete -n rg-mcp-demo --yes --no-wait
# az group wait -n rg-mcp-demo --deleted
# If the budget still lists after the group is gone:
az consumption budget delete-with-rg -g rg-mcp-demo -n budget-mcp-demo 2>/dev/null || true
# Container Apps also creates a managed infra RG (ME_... / MC_...): confirm it disappeared
az group list --query "[?contains(name,'mcp-demo')].name" -o tsv

# 2. Sign the Azure CLI out on this computer after the demo
az logout
az account clear

# 3. Neon: delete project helpdesk-demo only (id fragrant-recipe-18449662).
#    Do NOT delete vigneshcraft or any other project.
#    Console → helpdesk-demo → Settings → Delete project, or:
neon projects delete fragrant-recipe-18449662

# 4. GitHub: delete the package, then the repo, if you want them gone
#    GitHub → Packages → helpdesk-mcp-demo → Package settings → Delete this package
gh repo delete vsivasamy/helpdesk-mcp-demo --yes

# 5. Local secrets
rm -f .env .neon.env
```

## Verify zero cost
- Azure portal → Cost Management → Cost analysis, scope = subscription, group by resource group: `rg-mcp-demo` should stop accruing (data can lag up to ~24 h). `az resource list -g rg-mcp-demo` should report that the RG doesn't exist.
- Neon Console: `helpdesk-demo` is gone from the list; leave `vigneshcraft` alone. The free plan has no bill anyway.
- GitHub → Settings → Billing: Packages/Actions show $0.
