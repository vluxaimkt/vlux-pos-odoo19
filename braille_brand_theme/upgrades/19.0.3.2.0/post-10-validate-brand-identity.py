import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE res_company SET name = 'Braille International', write_date = NOW()
         WHERE id = (SELECT res_id FROM ir_model_data WHERE module='base' AND name='main_company' AND model='res.company' LIMIT 1)
            OR name = 'My Company'
    """)
    cr.execute("""
        UPDATE res_partner SET name = 'Asistente Braille', write_date = NOW()
         WHERE id = (SELECT res_id FROM ir_model_data WHERE module='base' AND name='partner_root' AND model='res.partner' LIMIT 1)
            OR name IN ('OdooBot', 'Odoo Bot')
    """)
    _logger.info("Braille identity validation migration completed")
