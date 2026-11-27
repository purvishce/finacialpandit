import base64
import json
import logging
import os
import re
from uuid import UUID, uuid4

import azure.functions as func
import jwt
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

app = func.FunctionApp()
MAX_FILE_SIZE = 10 * 1024 * 1024
ACCOUNT_URL = "https://uploadfinancedatadev4629.blob.core.windows.net"
CONTAINER = "incoming-statements"


def response(message, status, **extra):
    return func.HttpResponse(json.dumps({"message": message, **extra}), status_code=status,
                             mimetype="application/json")


def filename_part(value):
    """Make a predictable blob filename component from form input."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return cleaned[:60].strip("-") or "unknown"


def authenticated_user(req):
    tenant = str(UUID(os.environ["ENTRA_TENANT_ID"]))
    audience = os.environ["ENTRA_API_AUDIENCE"]
    authorization = req.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise ValueError("A Microsoft Entra access token is required.")
    token = authorization[7:]
    jwks = jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys")
    signing_key = jwks.get_signing_key_from_jwt(token)
    claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=audience,
                        issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
                        options={"require": ["exp", "iss", "aud", "oid", "tid"]})
    if claims["tid"] != tenant or not claims.get("scp"):
        raise ValueError("A delegated user token from the configured tenant is required.")
    return str(UUID(claims["oid"]))


@app.route(route="upload_statement", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def upload_statement(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Statement upload request received")
    try:
        user_id = authenticated_user(req)
    except KeyError:
        logging.error("Entra authentication is not configured")
        return response("Upload service authentication is not configured.", 503)
    except jwt.PyJWKClientError:
        logging.exception("Unable to obtain Entra signing keys")
        return response("Authentication service is unavailable.", 503)
    except (ValueError, jwt.PyJWTError):
        logging.warning("Statement upload rejected: authentication failed")
        return response("A valid Microsoft Entra access token is required.", 401)

    uploaded = req.files.get("file")
    if not uploaded or not (uploaded.filename or "").lower().endswith(".csv"):
        logging.warning("Statement upload rejected: CSV file missing or invalid")
        return response("Select a CSV file.", 400)
    data = uploaded.stream.read(MAX_FILE_SIZE + 1)
    if not data or len(data) > MAX_FILE_SIZE:
        logging.warning("Statement upload rejected: invalid file size")
        return response("CSV file must be between 1 byte and 10 MB.", 400)
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        logging.warning("Statement upload rejected: invalid UTF-8 encoding")
        return response("CSV file must use UTF-8 encoding.", 400)
    institution = req.form.get("institution", "").strip()
    account = req.form.get("account", "").strip()
    period = req.form.get("period", "").strip()
    if not institution or not account or len(institution) > 200 or len(account) > 200 or len(period) > 7:
        logging.warning("Statement upload rejected: invalid form fields")
        return response("Provide a financial institution and account.", 400)

    statement_id = str(uuid4())
    filename = "-".join((filename_part(institution), filename_part(account),
                         filename_part(period), statement_id)) + ".csv"
    blob_name = f"{user_id}/{filename}"
    # Blob metadata is ASCII-only; encode the form fields as UTF-8 JSON.
    details = base64.b64encode(json.dumps({"institution": institution, "account": account,
                                           "period": period}, ensure_ascii=False).encode()).decode()
    try:
        with DefaultAzureCredential() as credential:
            service = BlobServiceClient(account_url=ACCOUNT_URL, credential=credential)
            with service:
                service.get_blob_client(container=CONTAINER, blob=blob_name).upload_blob(
                    data, overwrite=False, metadata={"details_base64": details},
                    content_settings=ContentSettings(content_type="text/csv; charset=utf-8"))
    except Exception as error:
        logging.error("Statement upload failed: statement_id=%s error_type=%s",
                      statement_id, type(error).__name__)
        return response("Unable to upload the statement. Please try again.", 503)
    logging.info("Statement upload completed: statement_id=%s size_bytes=%d",
                 statement_id, len(data))
    return response("Statement uploaded.", 201, statement_id=statement_id, filename=filename)
