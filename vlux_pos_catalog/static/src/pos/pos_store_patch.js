import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { QuickProductDialog } from "./quick_product_dialog";

patch(PosStore.prototype, {
    async setup() {
        await super.setup(...arguments);
        // Barcode currently being registered. The mobile scanner reads it to
        // report "registration prompted" instead of a plain "not found".
        this.vluxCatalogPendingBarcode = null;
        this.vluxCatalogDefaults = null;
    },

    get vluxCatalogCanQuickCreate() {
        return Boolean(this.user?.vlux_catalog_can_quick_create);
    },

    async vluxCatalogGetDefaults() {
        if (!this.vluxCatalogDefaults) {
            this.vluxCatalogDefaults = await this.data.call(
                "product.template",
                "vlux_pos_quick_create_defaults",
                [this.config.id]
            );
        }
        return this.vluxCatalogDefaults;
    },

    /**
     * Open the quick product form for an unknown barcode. Not awaited by the
     * barcode pipeline so the scanner mutex keeps flowing for other codes.
     */
    vluxCatalogOpenQuickCreate(barcode) {
        if (!this.vluxCatalogCanQuickCreate || !barcode) {
            return false;
        }
        if (this.vluxCatalogPendingBarcode === barcode) {
            return true;
        }
        this.vluxCatalogPendingBarcode = barcode;
        this.dialog.add(
            QuickProductDialog,
            { barcode },
            {
                onClose: () => {
                    if (this.vluxCatalogPendingBarcode === barcode) {
                        this.vluxCatalogPendingBarcode = null;
                    }
                },
            }
        );
        return true;
    },

    /**
     * Load only the freshly created template into the session models and add
     * the variant to the current order. No full POS reload.
     */
    async vluxCatalogLoadAndAdd(result) {
        await this.loadNewProducts([["id", "=", result.product_tmpl_id]]);
        const product = this.models["product.product"].get(result.product_id);
        if (!product) {
            throw new Error(_t("El producto se creo pero no pudo cargarse en la caja."));
        }
        await this.addLineToCurrentOrder(
            { product_id: product, product_tmpl_id: product.product_tmpl_id },
            {},
            false
        );
        return product;
    },

    async vluxCatalogCreateProduct(values) {
        const result = await this.data.call("product.template", "vlux_pos_quick_create", [
            values,
            this.config.id,
        ]);
        if (!result.ok) {
            return result;
        }
        const product = await this.vluxCatalogLoadAndAdd(result);
        this.notification.add(_t("Producto registrado: %s", product.display_name), {
            type: "success",
        });
        return { ...result, product };
    },

    async vluxCatalogEnableExisting(productId) {
        const result = await this.data.call("product.template", "vlux_pos_enable_existing", [
            productId,
            this.config.id,
        ]);
        const product = await this.vluxCatalogLoadAndAdd(result);
        return { ...result, product };
    },
});
