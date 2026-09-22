"""VLUX API v1: the stable contract between VLUX interfaces and Odoo.

Everything here is versioned. ``/vlux/api/v1`` never changes in a way that
breaks a client already in the field: an incompatible change becomes ``v2``.

Shape of every response:

    {"ok": true,  "data": {...},                    "request_id": "..."}
    {"ok": false, "error": "CODE", "message": "..." , "request_id": "..."}

Authentication is a bearer token (``vlux.api.token``): only its hash is stored
and it can never do more than the user behind it.
"""
import base64
import json
import logging
import uuid
from datetime import datetime

from odoo import fields, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import Response, request

from odoo.addons.vlux_core.models.api_token import SCOPES

_logger = logging.getLogger(__name__)

API_VERSION = "v1"
API_ROOT = "/vlux/api/" + API_VERSION
# Requests per minute and per token. Generous for a register, low enough that a
# leaked token cannot be used to trawl the catalog.
RATE_LIMIT = 600
RATE_WINDOW_SECONDS = 60
# Every error code a client may see, with its HTTP status. The list only grows.
ERROR_CODES = {
    "MISSING_TOKEN": 401,
    "INVALID_TOKEN": 401,
    "FORBIDDEN_SCOPE": 403,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "VALIDATION_ERROR": 400,
    "INVALID_JSON": 400,
    "INVALID_CURSOR": 400,
    "RESYNC_REQUIRED": 409,
    "CONFLICT": 409,
    "NO_IMAGE": 404,
    "RATE_LIMITED": 429,
    "INTERNAL_ERROR": 500,
}
# Registered endpoints, for the OpenAPI document: path -> method -> metadata.
API_PATHS = {}


class VluxApiError(Exception):
    def __init__(self, code, message, status=None, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status or ERROR_CODES.get(code, 400)
        self.details = details


def api_response(payload, status=200, request_id=None):
    body = dict(payload)
    body["request_id"] = request_id or uuid.uuid4().hex
    response = Response(
        json.dumps(body, ensure_ascii=False, default=str),
        status=status,
        content_type="application/json; charset=utf-8",
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Vlux-Api-Version"] = API_VERSION
    response.headers["X-Request-Id"] = body["request_id"]
    return response


def api_error(code, message, status=400, request_id=None, details=None):
    body = {"ok": False, "error": code, "message": message}
    if details is not None:
        body["details"] = details
    return api_response(body, status, request_id)


def json_body():
    """The request body as a dict, or raise INVALID_JSON."""
    raw = request.httprequest.get_data(as_text=True)
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise VluxApiError("INVALID_JSON", "El cuerpo no es JSON válido.")
    if not isinstance(body, dict):
        raise VluxApiError("INVALID_JSON", "El cuerpo debe ser un objeto JSON.")
    return body


def int_param(value, name, default=None, minimum=None, maximum=None):
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise VluxApiError("VALIDATION_ERROR", "El parámetro %s debe ser un entero." % name)
    if minimum is not None and number < minimum:
        raise VluxApiError("VALIDATION_ERROR", "El parámetro %s debe ser al menos %s." % (name, minimum))
    if maximum is not None and number > maximum:
        number = maximum
    return number


# --- opaque cursors ---------------------------------------------------------
# A cursor is the position ``(timestamp, id)`` of the last record delivered,
# encoded so a client cannot build one by hand and rely on its shape.

def encode_cursor(stamp, record_id, initial=False):
    """``initial`` marks a cursor inside a first full sync: deletions are
    pointless for a client that has nothing yet, so they are skipped until
    the initial sync completes."""
    payload = {"t": stamp.isoformat(timespec="microseconds"), "i": int(record_id)}
    if initial:
        payload["f"] = 1
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(cursor):
    """Return ``(timestamp, id, initial)``; an absent cursor means "from the start"."""
    if not cursor:
        return datetime.min, 0, True
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return datetime.fromisoformat(payload["t"]), int(payload["i"]), bool(payload.get("f"))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise VluxApiError("INVALID_CURSOR", "El cursor no es válido.")


def authenticate(required_scope):
    """Return the token behind the request, or raise VluxApiError."""
    header = request.httprequest.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        raise VluxApiError("MISSING_TOKEN", "Falta el encabezado Authorization: Bearer.", 401)
    token = request.env["vlux.api.token"].sudo().authenticate(header[7:].strip())
    if not token:
        raise VluxApiError("INVALID_TOKEN", "El token no es válido o expiró.", 401)
    allowed = request.env["vlux.rate.limit"].sudo().consume(
        "api", token.id, RATE_LIMIT, RATE_WINDOW_SECONDS
    )
    if not allowed:
        raise VluxApiError("RATE_LIMITED", "Demasiadas solicitudes.", 429)
    if required_scope and not token.has_scope(required_scope):
        raise VluxApiError(
            "FORBIDDEN_SCOPE", "El token no tiene el alcance %s." % required_scope, 403
        )
    token.touch()
    # From here on the request *is* that user, in the token's company: Odoo's
    # own record rules and access rights apply, so an endpoint never has to be
    # trusted to check them itself. The scope only narrows what the user may
    # already do.
    user = token.user_id
    request.update_env(user=user.id, context={
        "lang": user.lang or "en_US",
        "tz": user.tz or "UTC",
        "allowed_company_ids": [token.company_id.id],
    })
    return token


def api_route(path, scope, methods=("GET",), summary=None, params=None):
    """Declare an API endpoint: auth, scope, error envelope and request id.

    ``summary`` and ``params`` (OpenAPI parameter objects) feed the published
    contract; the docstring is the description.
    """

    def decorator(function):
        doc = (function.__doc__ or "").strip()
        for method in methods:
            API_PATHS.setdefault(path, {})[method.lower()] = {
                "summary": summary or (doc.splitlines()[0] if doc else ""),
                "description": doc,
                "scope": scope,
                "params": params or [],
            }

        @http.route(
            API_ROOT + path, type="http", auth="public", methods=list(methods),
            csrf=False, save_session=False, sitemap=False,
        )
        def wrapper(self, *args, **kwargs):
            request_id = uuid.uuid4().hex
            try:
                token = authenticate(scope)
                data = function(self, token, *args, **kwargs)
                if isinstance(data, Response):
                    # Binary endpoints (images) build their own response and
                    # only borrow the request id and version headers.
                    data.headers["X-Request-Id"] = request_id
                    data.headers["X-Vlux-Api-Version"] = API_VERSION
                    data.headers["X-Content-Type-Options"] = "nosniff"
                    return data
                return api_response({"ok": True, "data": data}, 200, request_id)
            except VluxApiError as error:
                return api_error(error.code, error.message, error.status, request_id, error.details)
            except AccessError as error:
                return api_error("FORBIDDEN", str(error.args[0]) if error.args else "Sin permiso.", 403, request_id)
            except MissingError:
                return api_error("NOT_FOUND", "El registro no existe.", 404, request_id)
            except (ValidationError, UserError) as error:
                return api_error("VALIDATION_ERROR", str(error.args[0]) if error.args else "Datos inválidos.", 400, request_id)
            except Exception:
                _logger.exception("VLUX API %s failed (request %s)", path, request_id)
                return api_error("INTERNAL_ERROR", "Error interno.", 500, request_id)

        wrapper.__name__ = function.__name__
        return wrapper

    return decorator


class VluxApiV1(http.Controller):

    @api_route("/me", scope="system:read", summary="Identidad, alcances y tienda del token")
    def me(self, token, **kwargs):
        """Who the caller is, what it may do, and against which store."""
        user = request.env.user
        company = request.env.company
        return {
            "api_version": API_VERSION,
            "token": {"name": token.name, "prefix": token.token_prefix, "scopes": token.scopes.split()},
            "user": {"id": user.id, "name": user.name, "login": user.login},
            "company": {
                "id": company.id,
                "name": company.name,
                "country": company.country_id.code,
                "currency": company.currency_id.name,
            },
            "server_time": fields.Datetime.to_string(fields.Datetime.now()) + "Z",
        }

    @http.route(
        API_ROOT + "/openapi.json", type="http", auth="public", methods=["GET"],
        csrf=False, save_session=False, sitemap=False, readonly=True,
    )
    def openapi(self, **kwargs):
        """The contract itself, so a client can be generated from it."""
        version = request.env["vlux.core.system.info"].sudo()._release_version() or API_VERSION
        spec = {
            "openapi": "3.1.0",
            "info": {
                "title": "VLUX POS API",
                "version": version,
                "description": "Contrato estable entre las interfaces VLUX y Odoo.",
            },
            "servers": [{"url": API_ROOT}],
            "components": {
                "securitySchemes": {
                    "vluxToken": {"type": "http", "scheme": "bearer"},
                },
                "schemas": {
                    "Error": {
                        "type": "object",
                        "required": ["ok", "error", "message", "request_id"],
                        "properties": {
                            "ok": {"type": "boolean", "const": False},
                            "error": {"type": "string", "enum": list(ERROR_CODES)},
                            "message": {"type": "string"},
                            "request_id": {"type": "string"},
                            "details": {"type": "object"},
                        },
                    },
                },
            },
            "security": [{"vluxToken": []}],
            "x-scopes": SCOPES,
            "x-error-codes": ERROR_CODES,
            "paths": {
                path: {
                    method: {
                        "summary": meta["summary"],
                        "description": meta["description"],
                        "x-scope": meta["scope"],
                        "parameters": meta["params"],
                        "security": [{"vluxToken": []}],
                        "responses": {
                            "200": {"description": "OK"},
                            "401": {"description": "Token ausente o inválido"},
                            "403": {"description": "Alcance insuficiente o sin permiso"},
                            "429": {"description": "Demasiadas solicitudes"},
                        },
                    }
                    for method, meta in methods.items()
                }
                for path, methods in sorted(API_PATHS.items())
            },
        }
        return api_response({"ok": True, "data": spec})
