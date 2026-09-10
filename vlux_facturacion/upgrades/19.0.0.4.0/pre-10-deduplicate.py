def _move_thread_records(cr, model, canonical_id, duplicate_id):
    cr.execute(
        """
        UPDATE ir_attachment
           SET res_id = %s
         WHERE res_model = %s
           AND res_id = %s
        """,
        [canonical_id, model, duplicate_id],
    )
    cr.execute(
        """
        UPDATE mail_message
           SET res_id = %s
         WHERE model = %s
           AND res_id = %s
        """,
        [canonical_id, model, duplicate_id],
    )
    cr.execute(
        """
        UPDATE mail_activity
           SET res_id = %s
         WHERE res_model = %s
           AND res_id = %s
        """,
        [canonical_id, model, duplicate_id],
    )
    cr.execute(
        """
        DELETE FROM mail_followers
         WHERE res_model = %s
           AND res_id = %s
        """,
        [model, duplicate_id],
    )


def _deduplicate_fiscal_requests(cr):
    cr.execute(
        """
        SELECT pos_order_id,
               ARRAY_AGG(
                   id
                   ORDER BY
                       CASE state
                           WHEN 'stamped' THEN 100
                           WHEN 'simulation_completed' THEN 90
                           WHEN 'cancelled' THEN 80
                           WHEN 'stamping' THEN 70
                           WHEN 'ready_to_stamp' THEN 60
                           WHEN 'confirmed' THEN 50
                           WHEN 'validated' THEN 40
                           WHEN 'stamp_error' THEN 30
                           WHEN 'cancel_requested' THEN 20
                           ELSE 10
                       END DESC,
                       id ASC
               )
          FROM vlux_fiscal_request
         WHERE pos_order_id IS NOT NULL
         GROUP BY pos_order_id
        HAVING COUNT(*) > 1
        """
    )
    for _pos_order_id, request_ids in cr.fetchall():
        canonical_id, *duplicate_ids = request_ids
        for duplicate_id in duplicate_ids:
            _move_thread_records(
                cr,
                "vlux.fiscal.request",
                canonical_id,
                duplicate_id,
            )
            cr.execute(
                "DELETE FROM vlux_fiscal_request WHERE id = %s",
                [duplicate_id],
            )


def _deduplicate_fiscal_configs(cr):
    cr.execute(
        """
        SELECT company_id, ARRAY_AGG(id ORDER BY active DESC, id ASC)
          FROM vlux_fiscal_config
         WHERE company_id IS NOT NULL
         GROUP BY company_id
        HAVING COUNT(*) > 1
        """
    )
    for _company_id, config_ids in cr.fetchall():
        canonical_id, *duplicate_ids = config_ids
        for duplicate_id in duplicate_ids:
            cr.execute(
                """
                UPDATE vlux_fiscal_request
                   SET config_id = %s
                 WHERE config_id = %s
                """,
                [canonical_id, duplicate_id],
            )
            _move_thread_records(
                cr,
                "vlux.fiscal.config",
                canonical_id,
                duplicate_id,
            )
            cr.execute(
                "DELETE FROM vlux_fiscal_config WHERE id = %s",
                [duplicate_id],
            )


def _deduplicate_pac_providers(cr):
    cr.execute(
        """
        SELECT code, ARRAY_AGG(id ORDER BY active DESC, id ASC)
          FROM vlux_pac_provider
         WHERE code IS NOT NULL
         GROUP BY code
        HAVING COUNT(*) > 1
        """
    )
    for _code, provider_ids in cr.fetchall():
        canonical_id, *duplicate_ids = provider_ids
        for duplicate_id in duplicate_ids:
            cr.execute(
                """
                UPDATE vlux_fiscal_config
                   SET pac_provider_id = %s
                 WHERE pac_provider_id = %s
                """,
                [canonical_id, duplicate_id],
            )
            cr.execute(
                """
                UPDATE vlux_fiscal_request
                   SET pac_provider_id = %s
                 WHERE pac_provider_id = %s
                """,
                [canonical_id, duplicate_id],
            )
            cr.execute(
                "DELETE FROM vlux_pac_provider WHERE id = %s",
                [duplicate_id],
            )


def migrate(cr, version):
    _deduplicate_fiscal_requests(cr)
    _deduplicate_fiscal_configs(cr)
    _deduplicate_pac_providers(cr)
