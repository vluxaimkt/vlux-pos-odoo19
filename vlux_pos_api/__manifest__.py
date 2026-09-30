{
    "name": "VLUX POS API",
    "summary": "API v1 fase C: abrir y cerrar caja y registrar ventas desde una interfaz VLUX",
    "description": (
        "Operacion de venta por la API VLUX v1: estado de la caja, empleados con PIN, "
        "apertura y corte con el mismo limite de diferencia que el POS, cotizacion con "
        "precios e impuestos calculados en el servidor y ventas idempotentes por uuid "
        "sobre pos.order.sync_from_ui."
    ),
    "version": "19.0.1.1.0",
    "category": "Sales/Point of Sale",
    "author": "VLUX",
    "website": "https://vlux.com.mx",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "pos_hr", "vlux_core"],
    "data": [],
    "installable": True,
    "application": False,
}
