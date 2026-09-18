import { patch } from "@web/core/utils/patch";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";

patch(ProductScreen.prototype, {
    /**
     * Unknown barcode + quick-create permission -> open the registration
     * form instead of the generic "unknown barcode" toast. Known barcodes and
     * users without the permission follow the standard Odoo path untouched.
     */
    async _barcodeProductAction(code) {
        if (!this.pos.vluxCatalogCanQuickCreate) {
            return super._barcodeProductAction(code);
        }
        const product = await this._getProductByBarcode(code);
        if (product) {
            return super._barcodeProductAction(code);
        }
        this.sound.play("scan-error");
        // Deliberately not awaited: the barcode mutex must not wait for the cashier.
        this.pos.vluxCatalogOpenQuickCreate(code.base_code);
    },
});
