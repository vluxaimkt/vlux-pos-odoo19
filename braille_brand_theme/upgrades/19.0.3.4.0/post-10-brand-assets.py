import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE res_company
           SET name = 'Braille International',
               write_date = NOW()
         WHERE name IN ('My Company', 'Braille International')
    """)

    cr.execute("""
        UPDATE res_partner
           SET name = 'Asistente Braille',
               write_date = NOW()
         WHERE name IN ('OdooBot', 'Odoo Bot', 'Asistente Braille')
    """)

    _logger.info("Braille V6.4 plain-CSS migration completed")
