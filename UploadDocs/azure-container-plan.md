# Upload form: Azure Container Apps deployment plan

- Subscription: Azure subscription 1 (`c9c7537c-f522-4009-b97d-99ad81879f9f`)
- Resource group and region: `rg-librechat-dev`, Central US
- Existing environment: `cae-financial-pandit-dev`
- Existing registry: `financialpanditdev.azurecr.io`
- Proposed new container app: `ca-financial-pandit-upload-dev`
- Image: `financialpanditdev.azurecr.io/financial-pandit-upload:1.0`
- Container port: 80; external HTTPS ingress
- Source: `UploadDocs/Dockerfile`, `index.html`, `styles.css`, `app.js`

The page signs users in directly with Entra and calls the deployed Function. Configure the final form URL as an Entra SPA redirect URI and allow its origin in the Function's CORS settings. LibreChat session integration and PostgreSQL ingestion are not implemented. A new Container App and registry storage/build may incur charges; estimate depends on configuration and usage and has not been calculated.

The image build and deployment are not yet executed. Prior to deploying, inspect existing app names, registry permissions, environment network/ingress, choose registry authentication, and verify the live Function endpoint. After deploying, verify the served page and set an appropriate CORS origin once the actual form origin and authentication design are known.
