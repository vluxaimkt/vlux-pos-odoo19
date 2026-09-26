import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ConnectionLostError } from "@web/core/network/rpc";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";

patch(ProductScreen.prototype, {
    /**
     * Unknown barcode + quick-create permission -> open the registration
     * form instead of the generic "unknown barcode" toast. Known barcodes and
     * users without the permission follow the standard Odoo path untouched.
     *
     * Offline, a barcode the register has not loaded cannot be looked up on
     * the server, and a product cannot be created there either: the cashier
     * gets a clear message instead of a connection error.
     */
    async _barcodeProductAction(code) {
        if (!this.pos.vluxCatalogCanQuickCreate) {
            return super._barcodeProductAction(code);
        }
        let product;
        try {
            product = await this._getProductByBarcode(code);
        } catch (error) {
            if (error instanceof ConnectionLostError) {
                this.sound.play("scan-error");
                this.notification.add(
                    _t(
                        "Sin conexión: el código %s no está en esta caja. Podrás registrarlo cuando vuelva el internet.",
                        code.base_code
                    ),
                    { type: "warning" }
                );
                return;
            }
            throw error;
        }
        if (product) {
            return super._barcodeProductAction(code);
        }
        this.sound.play("scan-error");
        // Deliberately not awaited: the barcode mutex must not wait for the cashier.
        this.pos.vluxCatalogOpenQuickCreate(code.base_code);
    },
});
