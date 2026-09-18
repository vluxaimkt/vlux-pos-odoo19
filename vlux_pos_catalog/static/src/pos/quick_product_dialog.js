import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { useAsyncLockedMethod } from "@point_of_sale/app/hooks/hooks";
import { prepareProductImage } from "./image_utils";

/**
 * Minimal product registration form shown when a scanned barcode is unknown.
 *
 * Only the fields a cashier can fill in a few seconds are exposed; everything
 * else gets a sensible default server-side. Authorisation is enforced by the
 * server method regardless of this dialog being visible.
 */
export class QuickProductDialog extends Component {
    static template = "vlux_pos_catalog.QuickProductDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        barcode: { type: String, optional: true },
    };

    setup() {
        this.pos = usePos();
        this.notification = useService("notification");
        this.nameInput = useRef("nameInput");
        this.fileInput = useRef("fileInput");
        this.state = useState({
            barcode: this.props.barcode || "",
            name: "",
            list_price: "",
            default_code: "",
            pos_categ_id: "",
            categ_id: "",
            taxes_ids: [],
            is_storable: true,
            initial_qty: "",
            imageDataUrl: "",
            imageBase64: "",
            imageInfo: "",
            saving: false,
            error: "",
            existing: null,
            defaultsLoaded: false,
        });
        this.confirm = useAsyncLockedMethod(this.confirm);
        onMounted(async () => {
            try {
                const defaults = await this.pos.vluxCatalogGetDefaults();
                this.state.taxes_ids = [...(defaults.taxes_ids || [])];
                this.state.is_storable = Boolean(defaults.is_storable);
                if (defaults.pos_categ_id) {
                    this.state.pos_categ_id = String(defaults.pos_categ_id);
                }
            } catch (error) {
                this.state.error = error?.data?.message || error?.message || _t("No fue posible cargar los valores por defecto.");
            } finally {
                this.state.defaultsLoaded = true;
            }
            this.nameInput.el?.focus();
        });
    }

    get posCategories() {
        return this.pos.models["pos.category"].getAll();
    }

    get productCategories() {
        return this.pos.models["product.category"].getAll();
    }

    get saleTaxes() {
        return this.pos.models["account.tax"]
            .getAll()
            .filter((tax) => !tax.type_tax_use || tax.type_tax_use === "sale");
    }

    get currencySymbol() {
        return this.pos.currency?.symbol || "$";
    }

    get isValid() {
        const price = Number(String(this.state.list_price).replace(",", "."));
        return (
            this.state.barcode.trim().length >= 3 &&
            this.state.name.trim().length > 0 &&
            Number.isFinite(price) &&
            price >= 0
        );
    }

    isTaxSelected(taxId) {
        return this.state.taxes_ids.includes(taxId);
    }

    toggleTax(taxId) {
        if (this.isTaxSelected(taxId)) {
            this.state.taxes_ids = this.state.taxes_ids.filter((id) => id !== taxId);
        } else {
            this.state.taxes_ids = [...this.state.taxes_ids, taxId];
        }
    }

    onKeydown(event) {
        if (event.key === "Enter" && !event.shiftKey && this.isValid && !this.state.saving) {
            event.preventDefault();
            this.confirm();
        }
    }

    // The button is the only way the file picker / camera opens: explicit user action.
    openImagePicker() {
        this.fileInput.el?.click();
    }

    async onImageSelected(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file) {
            return;
        }
        try {
            const prepared = await prepareProductImage(file);
            this.state.imageDataUrl = prepared.dataUrl;
            this.state.imageBase64 = prepared.base64;
            this.state.imageInfo = `${prepared.width}×${prepared.height} · ${Math.round(prepared.bytes / 1024)} KB`;
            this.state.error = "";
        } catch {
            this.state.error = _t("El archivo seleccionado no es una imagen valida.");
        }
    }

    clearImage() {
        this.state.imageDataUrl = "";
        this.state.imageBase64 = "";
        this.state.imageInfo = "";
    }

    payload() {
        const values = {
            barcode: this.state.barcode.trim(),
            name: this.state.name.trim(),
            list_price: Number(String(this.state.list_price).replace(",", ".")),
            default_code: this.state.default_code.trim(),
            taxes_ids: this.state.taxes_ids,
            is_storable: this.state.is_storable,
            initial_qty: Number(String(this.state.initial_qty || "0").replace(",", ".")) || 0,
        };
        if (this.state.pos_categ_id) {
            values.pos_categ_id = Number(this.state.pos_categ_id);
        }
        if (this.state.categ_id) {
            values.categ_id = Number(this.state.categ_id);
        }
        if (this.state.imageBase64) {
            values.image = this.state.imageBase64;
        }
        return values;
    }

    async confirm() {
        if (!this.isValid || this.state.saving) {
            return;
        }
        this.state.saving = true;
        this.state.error = "";
        this.state.existing = null;
        try {
            const result = await this.pos.vluxCatalogCreateProduct(this.payload());
            if (result.ok) {
                this.props.close();
                return;
            }
            if (result.code === "BARCODE_EXISTS") {
                this.state.existing = result.existing;
                this.state.error = _t("Este codigo ya pertenece a un producto.");
                return;
            }
            this.state.error = _t("No fue posible registrar el producto.");
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || _t("No fue posible registrar el producto.");
        } finally {
            this.state.saving = false;
        }
    }

    async useExisting() {
        if (!this.state.existing) {
            return;
        }
        this.state.saving = true;
        try {
            const product = await this.pos.vluxCatalogEnableExisting(this.state.existing.id);
            this.notification.add(_t("Producto agregado: %s", product.product.display_name), {
                type: "success",
            });
            this.props.close();
        } catch (error) {
            this.state.error = error?.data?.message || error?.message || _t("No fue posible usar el producto existente.");
        } finally {
            this.state.saving = false;
        }
    }
}
