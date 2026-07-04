#!/usr/bin/env bash
# =============================================================================
# setup_azure_oidc.sh
#
# One-shot script to wire GitHub Actions to Azure using **OIDC Workload
# Identity Federation** (recommended) instead of a long-lived client secret.
#
# Why OIDC and not a service-principal secret?
#   - No secret to leak in GitHub Secrets or in PR build logs.
#   - Short-lived tokens (5 min) issued per run; rotated by Azure AD.
#   - GitHub publishes the token's claims (repo, ref, job) to Azure AD so the
#     federated credential can be scoped (e.g. only the `main` branch can
#     deploy to prod).
#
# Prerequisites:
#   - Azure CLI 2.50+ installed and logged in (`az login`).
#   - Permissions: `User Access Administrator` + `Contributor` on the target
#     subscription (only needed once, for the initial setup).
#   - Your GitHub repository URL (e.g. https://github.com/me/azure-data-integration).
#
# After running this script, set the values it prints in your GitHub
# repository (Settings -> Secrets and variables -> Actions):
#   - AZURE_CLIENT_ID
#   - AZURE_TENANT_ID
#   - AZURE_SUBSCRIPTION_ID
# =============================================================================
set -euo pipefail

SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
TENANT_ID="$(az account show --query tenantId -o tsv)"
APP_NAME="${APP_NAME:-github-azure-data-integration}"
FEDERATED_CRED_NAME="${FEDERATED_CRED_NAME:-github-main}"
GITHUB_REPO="${GITHUB_REPO:-}"

if [[ -z "$GITHUB_REPO" ]]; then
  read -rp "GitHub repo (org/name, e.g. acme/azure-data-integration): " GITHUB_REPO
fi

GITHUB_SUBJECT="repo:${GITHUB_REPO}:ref:refs/heads/main"
GITHUB_PR_SUBJECT="repo:${GITHUB_REPO}:pull_request"

echo "==> Subscription : $SUBSCRIPTION_ID"
echo "==> Tenant       : $TENANT_ID"
echo "==> GitHub repo  : $GITHUB_REPO"
echo ""

# --- 1. Create the App Registration (service principal) -------------------
echo "==> Creating App Registration '$APP_NAME' (if missing)..."
APP_ID=$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv || true)
if [[ -z "$APP_ID" || "$APP_ID" == "null" ]]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
  echo "    created: $APP_ID"
else
  echo "    reusing: $APP_ID"
fi

# --- 2. Create the service principal -------------------------------------
echo "==> Creating service principal..."
SP_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query '[0].id' -o tsv || true)
if [[ -z "$SP_ID" || "$SP_ID" == "null" ]]; then
  az ad sp create --id "$APP_ID" >/dev/null
  SP_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query '[0].id' -o tsv)
fi
echo "    sp id: $SP_ID"

# --- 3. Assign Contributor on the subscription ---------------------------
echo "==> Assigning Contributor at subscription scope..."
az role assignment create \
  --assignee "$APP_ID" \
  --role Contributor \
  --scope "/subscriptions/$SUBSCRIPTION_ID" \
  --only-show-errors >/dev/null || true

# --- 4. Create the federated credential (main branch) --------------------
echo "==> Creating federated credential for $GITHUB_SUBJECT..."
az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters \
    "{\"name\":\"$FEDERATED_CRED_NAME\",\"issuer\":\"https://token.actions.githubusercontent.com\",\"subject\":\"$GITHUB_SUBJECT\",\"audiences\":[\"api://AzureADTokenExchange\"]}" \
  --only-show-errors >/dev/null \
  || echo "    (already exists, skipped)"

# Allow PRs from any branch as well so the CI workflow can run `az login` to
# call `az sql`/`az keyvault` for cheap verifications without persisting data.
echo "==> Creating federated credential for pull requests..."
az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters \
    "{\"name\":\"github-pr\",\"issuer\":\"https://token.actions.githubusercontent.com\",\"subject\":\"$GITHUB_PR_SUBJECT\",\"audiences\":[\"api://AzureADTokenExchange\"]}" \
  --only-show-errors >/dev/null \
  || echo "    (already exists, skipped)"

cat <<EOF


============================================================
OIDC setup complete.

Add these to GitHub -> Settings -> Secrets and variables -> Actions:

  AZURE_CLIENT_ID       = $APP_ID
  AZURE_TENANT_ID       = $TENANT_ID
  AZURE_SUBSCRIPTION_ID = $SUBSCRIPTION_ID

Then in workflow files replace:
  uses: azure/login@v2
  with:
    creds: \${{ secrets.AZURE_CREDENTIALS }}
with:
  uses: azure/login@v2
  with:
    client-id:     \${{ secrets.AZURE_CLIENT_ID }}
    tenant-id:     \${{ secrets.AZURE_TENANT_ID }}
    subscription-id: \${{ secrets.AZURE_SUBSCRIPTION_ID }}

The federated credential only accepts tokens whose `sub` claim matches
'$GITHUB_SUBJECT' (i.e. pushes to the main branch). Tokens for pull requests
get read-only access via the 'github-pr' credential; rotate scope as needed.
============================================================
EOF