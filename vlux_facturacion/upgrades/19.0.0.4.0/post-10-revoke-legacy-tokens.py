def migrate(cr, version):
    cr.execute(
        """
        UPDATE vlux_fiscal_request
           SET access_token = NULL,
               access_token_hash = NULL,
               token_expires_at = NULL,
               token_revoked_at = NOW()
         WHERE access_token IS NOT NULL
            OR access_token_hash IS NOT NULL
        """
    )
