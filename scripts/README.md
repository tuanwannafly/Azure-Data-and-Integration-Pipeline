# scripts/

One-shot setup helpers for the Azure side of CI/CD. Run them **once** from a
machine that already has `az login` access at the subscription level.

| Script | Purpose |
|---|---|
| `setup_azure_oidc.sh` | Create an Azure AD App Registration + federated credentials for GitHub Actions. Prints the `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` to copy into GitHub Secrets. |
| `setup_acr.sh`        | Create the Azure Container Registry used by the Container App job and grant `AcrPush` to the OIDC service principal. |

After both scripts run successfully, all required credentials are
GitHub-managed (OIDC) — **no client secret is ever stored in GitHub**.

To run on Windows (PowerShell 7+):

    bash scripts/setup_azure_oidc.sh
    bash scripts/setup_acr.sh