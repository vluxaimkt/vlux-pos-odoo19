import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE res_company
           SET name = 'Braille International',
               write_date = NOW()
         WHERE name = 'My Company'
            OR id = (
                SELECT res_id
                  FROM ir_model_data
                 WHERE module = 'base'
                   AND name = 'main_company'
                   AND model = 'res.company'
                 LIMIT 1
            )
    """)

    cr.execute("""
        UPDATE res_partner
           SET name = 'Asistente Braille',
               write_date = NOW()
         WHERE name IN ('OdooBot', 'Odoo Bot')
            OR id = (
                SELECT res_id
                  FROM ir_model_data
                 WHERE module = 'base'
                   AND name = 'partner_root'
                   AND model = 'res.partner'
                 LIMIT 1
            )
    """)

    cr.execute("""
        SELECT
            EXISTS(
                SELECT 1
                  FROM res_company
                 WHERE name = 'Braille International'
            ),
            EXISTS(
                SELECT 1
                  FROM res_partner
                 WHERE name = 'Asistente Braille'
            )
    """)

    company_ok, assistant_ok = cr.fetchone()

    if not company_ok or not assistant_ok:
        raise RuntimeError(
            "Braille identity validation failed: "
            f"company={company_ok}, assistant={assistant_ok}"
        )

    _logger.info(
        "Braille identity validated: company=%s assistant=%s",
        company_ok,
        assistant_ok,
    )
