#!/usr/bin/env bash
# =============================================================================
# setup_acr.sh
#
# Creates the Azure Container Registry used by the Container App job in the
# Deploy workflow. Grants the OIDC service principal (see
# scripts/setup_azure_oidc.sh) the AcrPush role so CI can `docker push` images
# without distributing registry credentials.
# =============================================================================
set -euo pipefail

SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-dataintegration-dev}"
LOCATION="${LOCATION:-eastus}"
ACR_NAME="${ACR_NAME:-acrdataintegration01}"
APP_ID="${AZURE_CLIENT_ID:-}"

if [[ -z "$APP_ID" ]]; then
  echo "AZURE_CLIENT_ID env var must be set (the App registration created by setup_azure_oidc.sh)."
  exit 1
fi

echo "==> Creating/updating ACR '$ACR_NAME' in $RESOURCE_GROUP..."
az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1 || \
  az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACR_NAME" \
    --sku Basic \
    --admin-enabled false \
    --tags project=azure-data-integration \
    --output none

# AcrPush lets the principal push images; AcrPull lets Container Apps pull
# them. We grant both so the same principal handles end-to-end.
echo "==> Granting AcrPush to the OIDC service principal..."
ACR_ID=$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
az role assignment create \
  --assignee "$APP_ID" \
  --role AcrPush \
  --scope "$ACR_ID" \
  --only-show-errors >/dev/null || echo "    (assignment exists)"
az role assignment create \
  --assignee "$APP_ID" \
  --role AcrPull \
  --scope "$ACR_ID" \
  --only-show-errors >/dev/null || echo "    (assignment exists)"

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
echo
echo "ACR ready: $ACR_LOGIN_SERVER"
echo "Set the ACR_LOGIN_SERVER environment variable in GitHub Deploy workflow:"
echo "  ACR_LOGIN_SERVER=$ACR_LOGIN_SERVER"