import { registry } from "@web/core/registry";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as OfflineUtil from "@point_of_sale/../tests/generic_helpers/offline_util";
import { scan_barcode, negateStep, refresh } from "@point_of_sale/../tests/generic_helpers/utils";

const KNOWN_BARCODE = "7509990000011";
const UNKNOWN_OFFLINE_BARCODE = "7509990000059";

// The store loses its internet after opening the register: the cashier keeps
// selling with the (USB) scanner even after reloading the page offline, an
// unknown code gets a clear message instead of a broken form, and every sale
// reaches the server exactly once when the connection comes back.
registry.category("web_tour.tours").add("VluxOfflineDayTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            OfflineUtil.setOfflineMode(),
            // The page is reloaded without internet; Odoo warns once and the
            // register keeps working from what it had loaded.
            refresh(),
            Dialog.confirm("Continue with limited functionality"),

            scan_barcode(KNOWN_BARCODE),
            scan_barcode(KNOWN_BARCODE),
            ProductScreen.selectedOrderlineHas("Agua Tour 1L", 2),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),

            scan_barcode(UNKNOWN_OFFLINE_BARCODE),
            {
                content: "offline, an unknown code explains itself instead of opening a form that cannot save",
                trigger: `.o_notification:contains("Sin conexión"):contains("${UNKNOWN_OFFLINE_BARCODE}")`,
            },
            negateStep({
                content: "no quick-create form offline",
                trigger: ".vlux-catalog-dialog",
            }),

            scan_barcode(KNOWN_BARCODE),
            ProductScreen.selectedOrderlineHas("Agua Tour 1L", 1),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),

            OfflineUtil.setOnlineMode(),
            Chrome.isSyncStatusConnected(),
        ].flat(),
});
