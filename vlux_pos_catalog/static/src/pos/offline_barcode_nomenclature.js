import { patch } from "@web/core/utils/patch";
import { BarcodeParser } from "@barcodes/js/barcode_parser";

/**
 * Keep the USB scanner working when the POS page is reloaded offline.
 *
 * The POS barcode reader fetches its nomenclature from the server when the
 * page starts. Odoo's service worker deliberately does not cache data calls,
 * so a reload without internet leaves the reader without a parser: every scan
 * then shows "Unable to parse barcode" and the cashier can only tap products.
 *
 * Each successful fetch is kept in this browser; when the server cannot be
 * reached, the last copy is used. The nomenclature (barcode rules) changes
 * rarely and holds no secret.
 */
const STORAGE_PREFIX = "vlux_pos_catalog.barcode_nomenclature.";

function storageKey(id) {
    return `${STORAGE_PREFIX}${odoo.info?.db || "db"}.${id}`;
}

patch(BarcodeParser, {
    async fetchNomenclature(orm, id) {
        try {
            const nomenclature = await super.fetchNomenclature(orm, id);
            try {
                localStorage.setItem(storageKey(id), JSON.stringify(nomenclature));
            } catch {
                // storage full or disabled: the online path still works
            }
            return nomenclature;
        } catch (error) {
            let cached = null;
            try {
                cached = JSON.parse(localStorage.getItem(storageKey(id)) || "null");
            } catch {
                cached = null;
            }
            if (cached) {
                return cached;
            }
            throw error;
        }
    },
});
