# Statement upload

The form sends a CSV and the entered institution, account and period to the HTTP Function. The Function validates a Microsoft Entra v2 access token, then writes the CSV to `incoming-statements/<Entra object ID>/<institution>-<account>-<period>-<generated statement ID>.csv`. Filename components are sanitized; an omitted period becomes `unknown`. The form details are stored as base64-encoded UTF-8 JSON in the blob's `details_base64` metadata. This does not create PostgreSQL records or process transactions.

## Configuration

1. Register an Entra API and configure the Function app settings `ENTRA_TENANT_ID` and `ENTRA_API_AUDIENCE` (the API audience in the access token). Require v2 access tokens. The client must obtain an access token for this API, not an ID token or a Microsoft Graph token.
2. The Function uses `DefaultAzureCredential` to access the storage account. Grant its managed identity **Storage Blob Data Contributor** on the target container. For local development, sign in with Azure CLI using an identity with equivalent access.
3. Install Function dependencies with `python -m pip install -r requirements.txt` in the Function project's virtual environment. Set the two Entra settings in local.settings.json for local execution; do not commit secrets.
4. Serve the form over HTTP(S), not `file://`. In the LibreChat-Dev Entra app registration, add the form's exact origin plus `/` as a **Single-page application** redirect URI (for local testing: `http://localhost:8080/`). The form uses Microsoft MSAL to request the `upload_statement` delegated scope; the user may need admin consent. Add the form's exact origin to the Function App's CORS allowlist. The upload endpoint is configured in `app.js`.

The HTTP trigger is anonymous at the Functions key layer because it validates the Entra bearer token itself; never replace token validation with a client-supplied user ID. The form signs in directly to Entra; LibreChat integration and seamless single sign-on are not implemented.

## Run the form as a container

From this directory, build and run the image locally:

```powershell
docker build -t financial-pandit-upload:1.0 .
docker run --rm --name financial-pandit-upload -p 8080:80 financial-pandit-upload:1.0
```

Open `http://localhost:8080`. The image contains only the static form, not the Function, virtual environments, settings, or credentials.

To push the same image to the existing Azure Container Registry:

```powershell
az acr login --name financialpanditdev
docker tag financial-pandit-upload:1.0 financialpanditdev.azurecr.io/financial-pandit-upload:1.0
docker push financialpanditdev.azurecr.io/financial-pandit-upload:1.0
```

The registry tag can then be used by Azure Container Apps as
`financialpanditdev.azurecr.io/financial-pandit-upload:1.0`.

This container serves a standalone page using Microsoft Entra sign-in. The SPA redirect URI, delegated consent, and Function App CORS must be configured for the form's actual origin. It does not automatically inherit LibreChat's session; embedding or single sign-on requires separate integration.
