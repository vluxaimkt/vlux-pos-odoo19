{
    "name": "VLUX POS App",
    "summary": "La caja VLUX como app web instalable (PWA) sobre la API VLUX v1",
    "description": (
        "Sirve la PWA del punto de venta VLUX en /vlux-pos/: se instala en escritorio o "
        "tablet, abre sin red y vende con la API v1 (vlux_pos_api). La app compilada vive "
        "en static/dist; su codigo fuente esta en frontend/pos del repositorio."
    ),
    "version": "19.0.0.1.0",
    "category": "Sales/Point of Sale",
    "author": "VLUX",
    "website": "https://vlux.com.mx",
    "license": "LGPL-3",
    "depends": ["vlux_pos_api"],
    "data": [],
    "installable": True,
    "application": False,
}
