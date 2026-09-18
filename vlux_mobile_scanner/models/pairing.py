import hashlib
import secrets
from datetime import timedelta

from odoo import api, fields, models


PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIR_CODE_LENGTH = 8
PAIR_CODE_TTL_MINUTES = 10
MOBILE_TOKEN_TTL_HOURS = 8
# Server-side anti-bounce for the same barcode. 0 = disabled: the phone client
# owns repeat detection (it can tell "still in frame" from "scanned again") and
# network retries are idempotent through the client request_id.
DEFAULT_COOLDOWN_MS = 0
PAIRING_RETENTION_DAYS = 30
# last_seen_at is informational (shown in the pairing dialog). Writing it on
# every authenticated request turned each scan into an extra UPDATE; a
# throttled write keeps the value fresh enough without the amplification.
LAST_SEEN_THROTTLE_SECONDS = 60
PUSH_CHANNEL_PREFIX = "vlux_mobile_scanner"


class VluxMobileScannerPairing(models.Model):
    _name = "vlux.mobile.scanner.pairing"
    _description = "VLUX Mobile Scanner Pairing"
    _order = "create_date desc"

    pair_code = fields.Char(required=True, index=True, readonly=True)
    pos_config_id = fields.Many2one("pos.config", required=True, index=True, ondelete="cascade")
    pos_session_id = fields.Many2one("pos.session", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", related="pos_config_id.company_id", store=True, index=True)
    device_identifier = fields.Char(required=True, index=True)
    state = fields.Selection(
        [
            ("waiting", "Esperando dispositivo"),
            ("paired", "Emparejado"),
            ("revoked", "Revocado"),
            ("expired", "Expirado"),
        ],
        default="waiting",
        required=True,
        index=True,
    )
    pair_expires_at = fields.Datetime(required=True, index=True)
    token_hash = fields.Char(index=True, readonly=True)
    token_expires_at = fields.Datetime(index=True, readonly=True)
    paired_at = fields.Datetime(readonly=True)
    revoked_at = fields.Datetime(readonly=True)
    last_seen_at = fields.Datetime(readonly=True)
    cooldown_ms = fields.Integer(default=DEFAULT_COOLDOWN_MS, required=True)
    push_channel = fields.Char(compute="_compute_push_channel")

    _pair_code_unique = models.Constraint(
        "unique (pair_code)",
        "El codigo de emparejamiento debe ser unico.",
    )
    _token_hash_unique = models.Constraint(
        "unique (token_hash)",
        "El token movil debe ser unico.",
    )

    @api.depends("token_hash")
    def _compute_push_channel(self):
        """Bus channel the phone subscribes to for push results.

        Derived from the token hash (never from the raw token), so it can be
        recomputed server-side without storing another secret and cannot be
        reversed into the bearer token. It is only handed to the phone that
        already holds the token.
        """
        for pairing in self:
            if pairing.token_hash and pairing.state == "paired":
                digest = hashlib.sha256(f"push:{pairing.token_hash}".encode("utf-8")).hexdigest()
                pairing.push_channel = f"{PUSH_CHANNEL_PREFIX}:{digest[:40]}"
            else:
                pairing.push_channel = False

    @api.model
    def _new_pair_code(self):
        for _attempt in range(30):
            code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LENGTH))
            if not self.sudo().search_count([("pair_code", "=", code)]):
                return code
        raise RuntimeError("No fue posible generar un codigo de emparejamiento unico.")

    @api.model
    def _hash_token(self, token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @api.model
    def create_waiting_pairing(self, pos_session, device_identifier):
        """V1: una vinculacion movil activa por dispositivo POS."""
        now = fields.Datetime.now()
        old_pairings = self.sudo().search([
            ("pos_session_id", "=", pos_session.id),
            ("device_identifier", "=", device_identifier),
            ("state", "in", ["waiting", "paired"]),
        ])
        if old_pairings:
            old_pairings.action_revoke()

        return self.sudo().create({
            "pair_code": self._new_pair_code(),
            "pos_config_id": pos_session.config_id.id,
            "pos_session_id": pos_session.id,
            "device_identifier": device_identifier,
            "pair_expires_at": now + timedelta(minutes=PAIR_CODE_TTL_MINUTES),
            "cooldown_ms": DEFAULT_COOLDOWN_MS,
        })

    def action_revoke(self):
        now = fields.Datetime.now()
        for pairing in self:
            if pairing.state not in ("revoked", "expired"):
                pairing.sudo().write({
                    "state": "revoked",
                    "revoked_at": now,
                    "token_hash": False,
                })
        return True

    def refresh_state(self):
        now = fields.Datetime.now()
        for pairing in self.sudo():
            values = {}
            if pairing.state == "waiting" and pairing.pair_expires_at <= now:
                values["state"] = "expired"
            elif pairing.state == "paired" and pairing.token_expires_at and pairing.token_expires_at <= now:
                values.update({"state": "expired", "token_hash": False})
            elif pairing.state in ("waiting", "paired") and pairing.pos_session_id.state == "closed":
                values.update({"state": "revoked", "revoked_at": now, "token_hash": False})
            if values:
                pairing.write(values)
        return self

    def issue_mobile_token(self):
        self.ensure_one()
        locked = self.try_lock_for_update()
        if not locked:
            return False
        self = locked
        self.refresh_state()
        if self.state != "waiting":
            return False
        now = fields.Datetime.now()
        raw_token = secrets.token_urlsafe(32)
        self.sudo().write({
            "state": "paired",
            "token_hash": self._hash_token(raw_token),
            "paired_at": now,
            "last_seen_at": now,
            "token_expires_at": now + timedelta(hours=MOBILE_TOKEN_TTL_HOURS),
        })
        return raw_token

    def touch_last_seen(self, force=False):
        """Refresh last_seen_at, at most once per LAST_SEEN_THROTTLE_SECONDS unless forced."""
        self.ensure_one()
        now = fields.Datetime.now()
        if (
            force
            or not self.last_seen_at
            or (now - self.last_seen_at).total_seconds() >= LAST_SEEN_THROTTLE_SECONDS
        ):
            self.sudo().write({"last_seen_at": now})
            return True
        return False

    @api.model
    def authenticate_mobile_token(self, raw_token):
        """Resolve a bearer token to its paired record. Read-only on the hot path."""
        if not raw_token or len(raw_token) > 256:
            return self.browse()
        digest = self._hash_token(raw_token)
        pairing = self.sudo().search([("token_hash", "=", digest), ("state", "=", "paired")], limit=1)
        if not pairing:
            return pairing
        pairing.refresh_state()
        if pairing.state != "paired":
            return self.browse()
        pairing.touch_last_seen()
        return pairing

    @api.autovacuum
    def _gc_expired_pairings(self):
        self.sudo().search([("state", "in", ["waiting", "paired"])]).refresh_state()
        cutoff = fields.Datetime.now() - timedelta(days=PAIRING_RETENTION_DAYS)
        self.sudo().search(
            [
                ("state", "in", ["revoked", "expired"]),
                ("write_date", "<", cutoff),
            ]
        ).unlink()
