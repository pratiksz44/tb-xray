#!/usr/bin/env bash
# One-time setup, run by a person with Owner (or Contributor + User Access Administrator) rights:
#   1. resource group + shared infrastructure (registry, storage, identities, Container Apps environment)
#   2. an Entra ID app that GitHub Actions signs in as via OIDC (no stored passwords or keys)
#   3. role assignments for that app and for you (so you can upload models from your laptop)
#
# Usage:  GITHUB_REPO=<owner>/<repo> bash infra/bootstrap.sh
set -euo pipefail

GITHUB_REPO="${GITHUB_REPO:?set GITHUB_REPO=<owner>/<repo>}"
RESOURCE_GROUP="${RESOURCE_GROUP:-tb-xray-rg}"
LOCATION="${LOCATION:-centralindia}"
NAME_PREFIX="${NAME_PREFIX:-tbx}"
# Storage account holding the model files (the account in backend/config.yaml -> model.blob_url)
MODEL_STORAGE_ACCOUNT="${MODEL_STORAGE_ACCOUNT:-pratik}"
APP_NAME="${APP_NAME:-github-${GITHUB_REPO//\//-}-deploy}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Registering resource providers"
for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.OperationalInsights Microsoft.Storage \
          Microsoft.ManagedIdentity; do
  az provider register --namespace "$ns" --wait -o none
done

echo "==> Resource group $RESOURCE_GROUP ($LOCATION)"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" -o none

echo "==> Shared infrastructure (deployApps=false)"
az deployment group create --resource-group "$RESOURCE_GROUP" --name bootstrap \
  --template-file "$SCRIPT_DIR/main.bicep" --parameters namePrefix="$NAME_PREFIX" deployApps=false -o none

out() { az deployment group show -g "$RESOURCE_GROUP" -n bootstrap --query "properties.outputs.$1.value" -o tsv; }
ACR_NAME="$(out acrName)"
STORAGE_NAME="$(out storageAccountName)"
SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
RG_ID="$(az group show -n "$RESOURCE_GROUP" --query id -o tsv)"
STORAGE_ID="$(az storage account list --query "[?name=='$MODEL_STORAGE_ACCOUNT'].id | [0]" -o tsv)"
[[ -n "$STORAGE_ID" ]] || { echo "storage account $MODEL_STORAGE_ACCOUNT not found" >&2; exit 1; }

echo "==> Entra ID app for GitHub Actions: $APP_NAME"
CLIENT_ID="$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)"
if [[ -z "$CLIENT_ID" ]]; then
  CLIENT_ID="$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)"
  az ad sp create --id "$CLIENT_ID" -o none
fi
SP_OBJECT_ID="$(az ad sp show --id "$CLIENT_ID" --query id -o tsv)"

add_federated_credential() {  # name subject
  if ! az ad app federated-credential list --id "$CLIENT_ID" --query "[?name=='$1']" -o tsv | grep -q .; then
    az ad app federated-credential create --id "$CLIENT_ID" -o none --parameters "{
      \"name\": \"$1\", \"issuer\": \"https://token.actions.githubusercontent.com\",
      \"subject\": \"$2\", \"audiences\": [\"api://AzureADTokenExchange\"]}"
  fi
}
add_federated_credential "main-branch" "repo:${GITHUB_REPO}:ref:refs/heads/main"
add_federated_credential "production-environment" "repo:${GITHUB_REPO}:environment:production"

assign() {  # principal-id principal-type role scope
  az role assignment create --assignee-object-id "$1" --assignee-principal-type "$2" \
    --role "$3" --scope "$4" -o none 2>/dev/null || true
}
echo "==> Role assignments"
assign "$SP_OBJECT_ID" ServicePrincipal "Contributor" "$RG_ID"
assign "$SP_OBJECT_ID" ServicePrincipal "Role Based Access Control Administrator" "$RG_ID"  # Bicep role assignments
assign "$SP_OBJECT_ID" ServicePrincipal "Storage Blob Data Contributor" "$STORAGE_ID"        # CD downloads models
ME="$(az ad signed-in-user show --query id -o tsv)"
assign "$ME" User "Storage Blob Data Contributor" "$STORAGE_ID"                                # you upload models

cat <<EOF

Done. Add these to GitHub -> Settings -> Secrets and variables -> Actions:

  Secrets:
    AZURE_CLIENT_ID        $CLIENT_ID
    AZURE_TENANT_ID        $TENANT_ID
    AZURE_SUBSCRIPTION_ID  $SUBSCRIPTION_ID
  Variables:
    AZURE_RESOURCE_GROUP   $RESOURCE_GROUP
    AZURE_NAME_PREFIX      $NAME_PREFIX

Also create an environment named "production" (Settings -> Environments); add reviewers if you want a
manual approval before each deploy.

Upload your model files (after unzipping tb_export.zip), then run the CD workflow:
  az storage blob upload-batch --auth-mode login --account-name $MODEL_STORAGE_ACCOUNT \\
    --destination tb-classi --source ./tb_export --overwrite
(Role assignments can take a few minutes to apply.)

With the GitHub CLI you can set everything in one go:
  gh secret set AZURE_CLIENT_ID -b "$CLIENT_ID"; gh secret set AZURE_TENANT_ID -b "$TENANT_ID"
  gh secret set AZURE_SUBSCRIPTION_ID -b "$SUBSCRIPTION_ID"
  gh variable set AZURE_RESOURCE_GROUP -b "$RESOURCE_GROUP"; gh variable set AZURE_NAME_PREFIX -b "$NAME_PREFIX"
Registry: $ACR_NAME
EOF
