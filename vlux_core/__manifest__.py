{
    "name": "VLUX Core",
    "summary": "Roles, metadata y diagnostico base para VLUX POS",
    "version": "19.0.1.0.0",
    "category": "Point of Sale",
    "author": "VLUX",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "stock", "product"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/config_parameter_data.xml",
    ],
    "installable": True,
    "application": False,
}
