def migrate(cr, version):
    cr.execute(
        """
        DELETE FROM ir_config_parameter
         WHERE key IN (
             'vlux_owner.demo_api_token',
             'vlux_owner.demo_user_id'
         )
        """
    )
