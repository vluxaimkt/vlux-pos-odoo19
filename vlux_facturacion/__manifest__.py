{
    "name": "VLUX Facturación",
    "summary": "Autofacturación automática con arquitectura abierta para PAC",
    "version": "19.0.0.3.0",
    "category": "Accounting/Accounting",
    "author": "VLUX",
    "license": "LGPL-3",
    "depends": ["account", "mail", "point_of_sale", "l10n_mx"],
    "data": [
        "security/ir.model.access.csv",
        "data/sequence_data.xml",
        "data/pac_provider_data.xml",
        "data/fiscal_config_data.xml",
        "views/pac_provider_views.xml",
        "views/fiscal_config_views.xml",
        "views/fiscal_request_views.xml",
        "views/portal_facturacion_templates.xml",
        "views/menu_views.xml"
    ],
    "application": True,
    "installable": True,
    "auto_install": False
}
