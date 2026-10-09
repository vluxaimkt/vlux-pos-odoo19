"""Close a register left open past its closing time (store option, off by default).

At the register's closing time a session still open is closed as if the
drawer held exactly the expected cash, and marked ``vlux_auto_closed`` ("sin
conteo") with a note, so the owner reviews it; the next day the register opens
clean. A closing the server cannot do (orders in draft, accounting errors)
leaves the session open and says why in its chatter: nothing is forced.
"""
import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Every half hour from 16:00 to 03:00: stores close in the evening, some after midnight.
CLOSE_TIMES = [
    "%02d:%02d" % (hour % 24, minute)
    for hour in range(16, 28) for minute in (0, 30)
    if not (hour == 27 and minute == 30)
]
DEFAULT_TZ = "America/Mexico_City"
AUTO_CLOSE_NOTE = "Cierre automático, sin conteo"


class PosConfig(models.Model):
    _inherit = "pos.config"

    vlux_auto_close = fields.Boolean(
        string="Cierre automático de caja",
        help="Si la caja sigue abierta a la hora de cierre, se cierra sola con el efectivo esperado como contado "
             "y queda marcada 'sin conteo' para que el dueño la revise.",
    )
    vlux_auto_close_time = fields.Selection(
        [(value, value) for value in CLOSE_TIMES],
        string="Hora del cierre automático",
        default="23:00", required=True,
        help="Hora local de la tienda.",
    )

    def _vlux_auto_close_tz(self):
        self.ensure_one()
        return pytz.timezone(self.company_id.partner_id.tz or self.env.user.tz or DEFAULT_TZ)

    def _vlux_last_closing(self, now):
        """The most recent closing instant (UTC, naive) at or before ``now`` (UTC, naive)."""
        self.ensure_one()
        tz = self._vlux_auto_close_tz()
        hour, minute = (int(part) for part in self.vlux_auto_close_time.split(":"))
        local_now = pytz.utc.localize(now).astimezone(tz)
        closing = tz.localize(datetime.combine(local_now.date(), time(hour, minute)))
        if closing > local_now:
            closing = tz.localize(datetime.combine(local_now.date() - timedelta(days=1), time(hour, minute)))
        return closing.astimezone(pytz.utc).replace(tzinfo=None)

    def _vlux_auto_close_due(self, now):
        """The open session to close: it was opened before a closing time that has already passed."""
        self.ensure_one()
        session = self.current_session_id
        if not (self.vlux_auto_close and session and session.state in ("opened", "closing_control")):
            return self.env["pos.session"]
        start = session.start_at or session.create_date
        return session if start and start < self._vlux_last_closing(now) else self.env["pos.session"]

    @api.model
    def _vlux_cron_auto_close(self, now=None):
        """Every 15 minutes: close the registers past their closing time. One register's failure never blocks another."""
        now = now or fields.Datetime.now()
        for config in self.sudo().search([("vlux_auto_close", "=", True)]):
            session = config._vlux_auto_close_due(now)
            if not session:
                continue
            try:
                with self.env.cr.savepoint():
                    session._vlux_auto_close()
            except Exception as error:  # noqa: BLE001 - reported on the session, the next register goes on
                _logger.warning("VLUX auto close of %s failed: %s", session.name, error)
                session.message_post(body=_("No se pudo cerrar sola la caja: %s. Ciérrala a mano.", error))


class PosSession(models.Model):
    _inherit = "pos.session"

    vlux_auto_closed = fields.Boolean(
        string="Cierre automático, sin conteo", readonly=True, copy=False,
        help="La caja se cerró sola a la hora configurada, con el efectivo esperado como contado: revisa el conteo real.",
    )

    def _vlux_auto_close(self):
        """Close as counted == expected; refuse (raise) rather than force anything."""
        self.ensure_one()
        session = self.sudo()
        if session.config_id.cash_control:
            expected = session.cash_register_balance_end
            result = session.post_closing_cash_details(expected)
            if not result.get("successful"):
                raise ValueError(result.get("message") or _("no se pudo registrar el efectivo"))
        session.update_closing_control_state_session(AUTO_CLOSE_NOTE)
        result = session.close_session_from_ui([])
        if not result.get("successful"):
            raise ValueError(result.get("message") or _("no se pudo cerrar"))
        session.vlux_auto_closed = True
        session.message_post(body=_(
            "%s a las %s: el efectivo contado se tomó igual al esperado. Revisa el conteo real.",
            AUTO_CLOSE_NOTE, session.config_id.vlux_auto_close_time,
        ))
        _logger.info("VLUX auto closed %s", session.name)
