"""VLUX API v1: the stable contract between VLUX interfaces and Odoo.

Everything here is versioned. ``/vlux/api/v1`` never changes in a way that
breaks a client already in the field: an incompatible change becomes ``v2``.

Shape of every response:

    {"ok": true,  "data": {...},                    "request_id": "..."}
    {"ok": false, "error": "CODE", "message": "..." , "request_id": "..."}

Authentication is a bearer token (``vlux.api.token``): only its hash is stored
and it can never do more than the user behind it.
"""
import json
import logging
import uuid

from odoo import fields, http
from odoo.http import Response, request

from odoo.addons.vlux_core.models.api_token import SCOPES

_logger = logging.getLogger(__name__)

API_VERSION = "v1"
API_ROOT = "/vlux/api/" + API_VERSION
# Requests per minute and per token. Generous for a register, low enough that a
# leaked token cannot be used to trawl the catalog.
RATE_LIMIT = 600
RATE_WINDOW_SECONDS = 60


class VluxApiError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


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


def api_error(code, message, status=400, request_id=None):
    return api_response({"ok": False, "error": code, "message": message}, status, request_id)


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
    # From here on the request *is* that user: Odoo's own record rules and
    # access rights apply, so an endpoint never has to be trusted to check
    # them itself. The scope only narrows what the user may already do.
    request.update_env(user=token.user_id.id)
    return token


def api_route(path, scope, methods=("GET",)):
    """Declare an API endpoint: auth, scope, error envelope and request id."""

    def decorator(function):
        @http.route(
            API_ROOT + path, type="http", auth="public", methods=list(methods),
            csrf=False, save_session=False, sitemap=False,
        )
        def wrapper(self, *args, **kwargs):
            request_id = uuid.uuid4().hex
            try:
                token = authenticate(scope)
                data = function(self, token, *args, **kwargs)
                return api_response({"ok": True, "data": data}, 200, request_id)
            except VluxApiError as error:
                return api_error(error.code, error.message, error.status, request_id)
            except Exception:
                _logger.exception("VLUX API %s failed (request %s)", path, request_id)
                return api_error("INTERNAL_ERROR", "Error interno.", 500, request_id)

        wrapper.__name__ = function.__name__
        return wrapper

    return decorator


class VluxApiV1(http.Controller):

    @api_route("/me", scope="system:read")
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
                            "error": {
                                "type": "string",
                                "enum": [
                                    "MISSING_TOKEN", "INVALID_TOKEN", "FORBIDDEN_SCOPE",
                                    "RATE_LIMITED", "INTERNAL_ERROR",
                                ],
                            },
                            "message": {"type": "string"},
                            "request_id": {"type": "string"},
                        },
                    },
                },
            },
            "security": [{"vluxToken": []}],
            "x-scopes": SCOPES,
            "paths": {
                "/me": {
                    "get": {
                        "summary": "Identidad, alcances y tienda del token",
                        "security": [{"vluxToken": []}],
                        "responses": {
                            "200": {"description": "OK"},
                            "401": {"description": "Token ausente o inválido"},
                            "403": {"description": "Alcance insuficiente"},
                            "429": {"description": "Demasiadas solicitudes"},
                        },
                    }
                },
            },
        }
        return api_response({"ok": True, "data": spec})
