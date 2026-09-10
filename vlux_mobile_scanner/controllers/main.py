import base64
import io
import re
import uuid
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

import qrcode

from odoo import fields, http
from odoo.http import request


BARCODE_MAX_LENGTH = 128
JSON_BODY_MAX_BYTES = 16 * 1024
PAIR_CODE_RE = re.compile(r"^[A-Z2-9]{8}$")


class VluxMobileScannerController(http.Controller):

    def _json_payload(self):
        payload = request.httprequest.get_json(silent=True)
        return payload if isinstance(payload, dict) else {}

    def _validate_json_request(self):
        if not request.httprequest.is_json:
            return self._error(
                "El contenido debe ser JSON.",
                status=415,
                code="INVALID_CONTENT_TYPE",
            )
        if (request.httprequest.content_length or 0) > JSON_BODY_MAX_BYTES:
            return self._error(
                "La solicitud supera el tamaño permitido.",
                status=413,
                code="REQUEST_TOO_LARGE",
            )
        return None

    def _json(self, payload, status=200):
        response = request.make_json_response(payload, status=status)
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def _error(self, message, status=400, code="ERROR"):
        return self._json({"ok": False, "code": code, "message": message}, status=status)

    def _require_pos_user(self):
        return request.env.user.has_group("point_of_sale.group_pos_user")

    def _require_pos_csrf(self):
        token = request.httprequest.headers.get("X-CSRF-Token", "")
        return bool(token and request.validate_csrf(token))

    def _client_identity(self):
        return request.httprequest.remote_addr or "unknown"

    def _consume_rate_limit(self, scope, identity, limit, window_seconds):
        return request.env["vlux.mobile.scanner.rate.limit"].sudo().consume(
            scope,
            identity,
            limit,
            window_seconds,
        )

    def _rate_limit_error(self):
        response = self._error(
            "Demasiadas solicitudes. Intente nuevamente más tarde.",
            status=429,
            code="RATE_LIMITED",
        )
        response.headers["Retry-After"] = "60"
        return response

    def _accessible_pos_session(self, session_id, config_id=None):
        domain = [
            ("id", "=", session_id),
            ("company_id", "in", request.env.companies.ids),
        ]
        if config_id is not None:
            domain.append(("config_id", "=", config_id))
        return request.env["pos.session"].search(domain, limit=1)

    def _mobile_token(self):
        authorization = request.httprequest.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return request.httprequest.headers.get("X-VLUX-Scanner-Token", "").strip()

    def _authenticate_mobile(self):
        return request.env["vlux.mobile.scanner.pairing"].authenticate_mobile_token(self._mobile_token())

    def _normalize_base_url(self, value):
        value = str(value or "").strip()
        if not value:
            return ""
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            port_number = parsed.port
        except ValueError:
            return ""
        if parsed.scheme not in ("http", "https") or not host:
            return ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{port_number}" if port_number else ""
        return f"{parsed.scheme}://{host}{port}"

    def _is_loopback_url(self, value):
        try:
            host = (urlsplit(value).hostname or "").lower()
        except ValueError:
            return True
        return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

    def _scanner_base_url(self):
        params = request.env["ir.config_parameter"].sudo()

        configured = self._normalize_base_url(
            params.get_param("vlux_mobile_scanner.base_url", "")
        )
        if configured:
            return configured

        current = self._normalize_base_url(request.httprequest.host_url)
        if current and not self._is_loopback_url(current):
            return current

        web_base = self._normalize_base_url(params.get_param("web.base.url", ""))
        if web_base and not self._is_loopback_url(web_base):
            return web_base

        return current or web_base

    def _scanner_url(self, pair_code):
        base_url = self._scanner_base_url()
        query = urlencode({"db": request.db, "pair": pair_code})
        return f"{base_url}/vlux/scanner?{query}"

    def _qr_data_uri(self, text):
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=7,
            border=2,
        )
        qr.add_data(text)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        encoded = base64.b64encode(stream.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _validate_barcode(self, value):
        if not isinstance(value, str):
            return False, "El codigo debe ser texto."
        barcode = value.strip()
        if not barcode:
            return False, "El codigo esta vacio."
        if len(barcode) > BARCODE_MAX_LENGTH:
            return False, "El codigo supera la longitud maxima permitida."
        # Permitimos GS (ASCII 29) para futura compatibilidad GS1.
        if any(ord(char) < 32 and char != "\x1d" for char in barcode):
            return False, "El codigo contiene caracteres de control no permitidos."
        return barcode, False

    @http.route(
        "/vlux/scanner",
        type="http",
        auth="public",
        methods=["GET"],
        sitemap=False,
        save_session=False,
    )
    def scanner_page(self, **kwargs):
        pair_code = str(kwargs.get("pair") or "").strip().upper()
        response = request.render(
            "vlux_mobile_scanner.scanner_page",
            {"db_name": request.db or "", "pair_code": pair_code},
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["Permissions-Policy"] = "camera=(self)"
        return response

    @http.route(
        "/vlux/mobile/pair",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def mobile_pair(self, **kwargs):
        invalid_request = self._validate_json_request()
        if invalid_request:
            return invalid_request
        if not self._consume_rate_limit(
            "pair",
            self._client_identity(),
            5,
            60,
        ):
            return self._rate_limit_error()
        payload = self._json_payload()
        pair_code = str(payload.get("code") or "").strip().upper()
        if not PAIR_CODE_RE.match(pair_code):
            return self._error("Codigo de conexion invalido.", status=400, code="INVALID_PAIR_CODE")

        pairing = request.env["vlux.mobile.scanner.pairing"].sudo().search(
            [("pair_code", "=", pair_code)], limit=1
        )
        if not pairing:
            return self._error(
                "El codigo no existe, expiro o ya fue utilizado.",
                status=400,
                code="PAIR_UNAVAILABLE",
            )
        pairing.refresh_state()
        if pairing.state != "waiting":
            return self._error(
                "El codigo no existe, expiro o ya fue utilizado.",
                status=400,
                code="PAIR_UNAVAILABLE",
            )
        if pairing.pos_session_id.state not in ("opening_control", "opened"):
            pairing.action_revoke()
            return self._error(
                "La caja ya no tiene una sesion POS activa.", status=409, code="POS_SESSION_CLOSED"
            )

        raw_token = pairing.issue_mobile_token()
        if not raw_token:
            return self._error(
                "No fue posible completar el emparejamiento.", status=409, code="PAIR_FAILED"
            )
        return self._json(
            {
                "ok": True,
                "token": raw_token,
                "pos_name": pairing.pos_config_id.name,
                "session_name": pairing.pos_session_id.name,
                "token_expires_at": fields.Datetime.to_string(pairing.token_expires_at),
                "cooldown_ms": pairing.cooldown_ms,
            }
        )

    @http.route(
        "/vlux/mobile/heartbeat",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def mobile_heartbeat(self, **kwargs):
        invalid_request = self._validate_json_request()
        if invalid_request:
            return invalid_request
        pairing = self._authenticate_mobile()
        if not pairing:
            return self._error(
                "La vinculacion movil no es valida o expiro.", status=401, code="INVALID_TOKEN"
            )
        if not self._consume_rate_limit("heartbeat", pairing.id, 12, 60):
            return self._rate_limit_error()
        if pairing.pos_session_id.state not in ("opening_control", "opened"):
            pairing.action_revoke()
            return self._error(
                "La sesion del punto de venta ya no esta activa.", status=409, code="POS_SESSION_CLOSED"
            )
        return self._json(
            {
                "ok": True,
                "connected": True,
                "pos_name": pairing.pos_config_id.name,
                "session_name": pairing.pos_session_id.name,
                "cooldown_ms": pairing.cooldown_ms,
            }
        )

    @http.route(
        "/vlux/mobile/disconnect",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def mobile_disconnect(self, **kwargs):
        invalid_request = self._validate_json_request()
        if invalid_request:
            return invalid_request
        pairing = self._authenticate_mobile()
        if not pairing:
            return self._json({"ok": True, "revoked": False})
        if not self._consume_rate_limit("disconnect", pairing.id, 5, 60):
            return self._rate_limit_error()
        pairing.action_revoke()
        return self._json({"ok": True, "revoked": True})

    @http.route(
        "/vlux/mobile/scan",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def mobile_scan(self, **kwargs):
        invalid_request = self._validate_json_request()
        if invalid_request:
            return invalid_request
        pairing = self._authenticate_mobile()
        if not pairing:
            return self._error(
                "La vinculacion movil no es valida o expiro.", status=401, code="INVALID_TOKEN"
            )
        if not self._consume_rate_limit("scan", pairing.id, 120, 60):
            return self._rate_limit_error()
        if pairing.pos_session_id.state not in ("opening_control", "opened"):
            pairing.action_revoke()
            return self._error("La caja no tiene una sesion activa.", status=409, code="POS_SESSION_CLOSED")

        payload = self._json_payload()
        barcode, validation_error = self._validate_barcode(payload.get("barcode"))
        if validation_error:
            return self._error(validation_error, status=400, code="INVALID_BARCODE")

        cooldown_seconds = max(pairing.cooldown_ms, 0) / 1000.0
        if cooldown_seconds:
            cutoff = fields.Datetime.now() - timedelta(seconds=cooldown_seconds)
            duplicate = request.env["vlux.mobile.scanner.event"].sudo().search_count(
                [
                    ("pairing_id", "=", pairing.id),
                    ("barcode", "=", barcode),
                    ("state", "in", ["queued", "delivered"]),
                    ("create_date", ">=", cutoff),
                ]
            )
            if duplicate:
                return self._error(
                    "Lectura duplicada bloqueada por el tiempo de seguridad.",
                    status=429,
                    code="DUPLICATE_COOLDOWN",
                )

        request_id = str(uuid.uuid4())
        event = request.env["vlux.mobile.scanner.event"].sudo().create(
            {
                "request_id": request_id,
                "pairing_id": pairing.id,
                "barcode": barcode,
                "device_identifier": pairing.device_identifier,
            }
        )

        # Mecanismo nativo del POS 19: pos.config._notify -> Odoo Bus/WebSocket.
        pairing.pos_config_id.sudo()._notify(
            "VLUX_MOBILE_BARCODE",
            {
                "request_id": request_id,
                "event_id": event.id,
                "pairing_id": pairing.id,
                "pos_config_id": pairing.pos_config_id.id,
                "pos_session_id": pairing.pos_session_id.id,
                "device_identifier": pairing.device_identifier,
                "barcode": barcode,
            },
        )
        return self._json(
            {
                "ok": True,
                "status": "queued",
                "request_id": request_id,
                "message": "Codigo enviado a la caja.",
            },
            status=202,
        )

    @http.route(
        "/vlux/mobile/result",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def mobile_result(self, **kwargs):
        invalid_request = self._validate_json_request()
        if invalid_request:
            return invalid_request
        pairing = self._authenticate_mobile()
        if not pairing:
            return self._error(
                "La vinculacion movil no es valida o expiro.", status=401, code="INVALID_TOKEN"
            )
        if not self._consume_rate_limit("result", pairing.id, 180, 60):
            return self._rate_limit_error()
        payload = self._json_payload()
        request_id = str(payload.get("request_id") or "").strip()
        event = request.env["vlux.mobile.scanner.event"].sudo().search(
            [("request_id", "=", request_id), ("pairing_id", "=", pairing.id)], limit=1
        )
        if not event:
            return self._error("No se encontro la solicitud.", status=404, code="REQUEST_NOT_FOUND")
        return self._json(
            {
                "ok": True,
                "request_id": event.request_id,
                "status": event.state,
                "result_code": event.result_code or "",
                "message": event.result_message or "",
                "product": (
                    {
                        "id": event.product_id.id,
                        "name": event.product_name or event.product_id.display_name,
                        "unit_price": event.unit_price,
                    }
                    if event.product_id or event.product_name
                    else None
                ),
            }
        )

    @http.route("/vlux/pos/pairing/create", type="http", auth="user", methods=["POST"], csrf=False)
    def pos_pairing_create(self, **kwargs):
        if not self._require_pos_user():
            return self._error("Acceso denegado.", status=403, code="ACCESS_DENIED")
        if not self._require_pos_csrf():
            return self._error("Solicitud inválida.", status=403, code="INVALID_CSRF")
        invalid_request = self._validate_json_request()
        if invalid_request:
            return invalid_request
        payload = self._json_payload()
        try:
            session_id = int(payload.get("pos_session_id"))
            config_id = int(payload.get("pos_config_id"))
        except (TypeError, ValueError):
            return self._error("Sesion o caja invalida.", code="INVALID_POS")

        device_identifier = str(payload.get("device_identifier") or "").strip()
        if not device_identifier or len(device_identifier) > 128:
            return self._error("No se pudo identificar este dispositivo POS.", code="INVALID_DEVICE")

        session = self._accessible_pos_session(session_id, config_id=config_id)
        if (
            not session
            or session.config_id.id != config_id
            or session.state not in ("opening_control", "opened")
        ):
            return self._error("La sesion POS no esta activa.", status=409, code="POS_SESSION_CLOSED")

        pairing = request.env["vlux.mobile.scanner.pairing"].create_waiting_pairing(
            session, device_identifier
        )
        scanner_url = self._scanner_url(pairing.pair_code)
        return self._json(
            {
                "ok": True,
                "pairing": {
                    "id": pairing.id,
                    "code": pairing.pair_code,
                    "status": pairing.state,
                    "expires_at": fields.Datetime.to_string(pairing.pair_expires_at),
                    "scanner_url": scanner_url,
                    "qr_data_uri": self._qr_data_uri(scanner_url),
                    "pos_name": pairing.pos_config_id.name,
                    "cooldown_ms": pairing.cooldown_ms,
                },
            }
        )

    def _validate_pos_pairing_scope(self, pairing, session_id, device_identifier):
        if not pairing:
            return False
        return (
            pairing.pos_session_id.id == session_id
            and pairing.device_identifier == device_identifier
            and bool(self._accessible_pos_session(session_id))
        )

    @http.route("/vlux/pos/pairing/status", type="http", auth="user", methods=["POST"], csrf=False)
    def pos_pairing_status(self, **kwargs):
        if not self._require_pos_user():
            return self._error("Acceso denegado.", status=403, code="ACCESS_DENIED")
        if not self._require_pos_csrf():
            return self._error("Solicitud inválida.", status=403, code="INVALID_CSRF")
        invalid_request = self._validate_json_request()
        if invalid_request:
            return invalid_request
        payload = self._json_payload()
        try:
            pairing_id = int(payload.get("pairing_id"))
            session_id = int(payload.get("pos_session_id"))
        except (TypeError, ValueError):
            return self._error("Vinculacion invalida.", code="INVALID_PAIRING")

        device_identifier = str(payload.get("device_identifier") or "").strip()
        pairing = request.env["vlux.mobile.scanner.pairing"].sudo().browse(pairing_id).exists()
        if not pairing:
            return self._error("Vinculacion no encontrada.", status=404)

        if not self._validate_pos_pairing_scope(pairing, session_id, device_identifier):
            return self._error(
                "La vinculacion no corresponde a esta caja.",
                status=403,
                code="PAIRING_MISMATCH",
            )

        pairing.refresh_state()
        return self._json(
            {
                "ok": True,
                "status": pairing.state,
                "last_seen_at": (
                    fields.Datetime.to_string(pairing.last_seen_at)
                    if pairing.last_seen_at
                    else None
                ),
            }
        )

    @http.route("/vlux/pos/pairing/revoke", type="http", auth="user", methods=["POST"], csrf=False)
    def pos_pairing_revoke(self, **kwargs):
        if not self._require_pos_user():
            return self._error("Acceso denegado.", status=403, code="ACCESS_DENIED")
        if not self._require_pos_csrf():
            return self._error("Solicitud inválida.", status=403, code="INVALID_CSRF")
        invalid_request = self._validate_json_request()
        if invalid_request:
            return invalid_request
        payload = self._json_payload()
        try:
            pairing_id = int(payload.get("pairing_id"))
            session_id = int(payload.get("pos_session_id"))
        except (TypeError, ValueError):
            return self._error("Vinculacion invalida.", code="INVALID_PAIRING")

        device_identifier = str(payload.get("device_identifier") or "").strip()
        pairing = request.env["vlux.mobile.scanner.pairing"].sudo().browse(pairing_id).exists()
        if not pairing:
            return self._json({"ok": True, "revoked": False})

        if not self._validate_pos_pairing_scope(pairing, session_id, device_identifier):
            return self._error(
                "La vinculacion no corresponde a esta caja.",
                status=403,
                code="PAIRING_MISMATCH",
            )

        pairing.action_revoke()
        return self._json({"ok": True, "revoked": True})

    @http.route("/vlux/pos/ack", type="http", auth="user", methods=["POST"], csrf=False)
    def pos_ack(self, **kwargs):
        if not self._require_pos_user():
            return self._error("Acceso denegado.", status=403, code="ACCESS_DENIED")
        if not self._require_pos_csrf():
            return self._error("Solicitud inválida.", status=403, code="INVALID_CSRF")
        invalid_request = self._validate_json_request()
        if invalid_request:
            return invalid_request
        payload = self._json_payload()
        request_id = str(payload.get("request_id") or "").strip()
        event = request.env["vlux.mobile.scanner.event"].sudo().search(
            [("request_id", "=", request_id)], limit=1
        )
        if not event:
            return self._error("Solicitud no encontrada.", status=404)

        try:
            session_id = int(payload.get("pos_session_id"))
        except (TypeError, ValueError):
            return self._error("Sesion invalida.", code="INVALID_SESSION")

        device_identifier = str(payload.get("device_identifier") or "").strip()
        pairing = event.pairing_id
        if not self._validate_pos_pairing_scope(
            pairing,
            session_id,
            device_identifier,
        ):
            return self._error(
                "La respuesta no corresponde a esta caja.", status=403, code="PAIRING_MISMATCH"
            )

        status = str(payload.get("status") or "failed")
        if status not in ("delivered", "not_found", "failed"):
            status = "failed"

        product = request.env["product.product"]
        product_id = payload.get("product_id")
        if product_id:
            try:
                product = request.env["product.product"].search(
                    [
                        ("id", "=", int(product_id)),
                        ("company_id", "in", [False, *request.env.companies.ids]),
                    ],
                    limit=1,
                )
            except (TypeError, ValueError):
                product = request.env["product.product"]

        try:
            unit_price = float(payload.get("unit_price") or 0.0)
        except (TypeError, ValueError):
            unit_price = 0.0

        event.sudo().write(
            {
                "state": status,
                "result_code": str(payload.get("result_code") or "")[:128],
                "result_message": str(payload.get("message") or "")[:500],
                "product_id": product.id if product else False,
                "product_name": str(payload.get("product_name") or "")[:255],
                "unit_price": unit_price,
                "processed_at": fields.Datetime.now(),
            }
        )
        return self._json({"ok": True})
