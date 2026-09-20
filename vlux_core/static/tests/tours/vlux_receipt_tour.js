import { registry } from "@web/core/registry";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";

/*
 * A printed ticket must carry the fiscal identity of the store. Odoo prints the
 * name, address, phone and VAT (the RFC in Mexico); VLUX adds the tax regime and
 * the legend that keeps a ticket from being mistaken for a tax document.
 */
registry.category("web_tour.tours").add("VluxReceiptFiscalTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            // The test database carries a large synthetic catalog: search first.
            ProductScreen.searchProduct("VLUX Recibo Producto"),
            ProductScreen.clickDisplayedProduct("VLUX Recibo Producto"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("VLUX Recibo Efectivo"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.receiptIsThere(),
            {
                content: "the receipt shows the company RFC",
                trigger: ".pos-receipt:contains('VLX010101AAA')",
            },
            {
                content: "the receipt shows the tax regime",
                trigger: ".vlux-receipt-regime:contains('601 - General de Ley Personas Morales')",
            },
            {
                content: "the receipt states it is not a tax document",
                trigger: ".vlux-receipt-legend:contains('Este ticket no es un comprobante fiscal')",
            },
            Chrome.endTour(),
        ].flat(),
});
