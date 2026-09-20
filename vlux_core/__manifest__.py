{
    "name": "VLUX Core",
    "summary": "Roles, metadata y diagnostico base para VLUX POS",
    "version": "19.0.1.3.1",
    "category": "Point of Sale",
    "author": "VLUX",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "stock", "product"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/config_parameter_data.xml",
        "views/res_company_views.xml",
        "views/api_token_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "vlux_core/static/src/pos/receipt_fiscal.xml",
        ],
        "web.assets_tests": [
            "vlux_core/static/tests/tours/**/*.js",
        ],
    },
    "installable": True,
    "application": False,
}
