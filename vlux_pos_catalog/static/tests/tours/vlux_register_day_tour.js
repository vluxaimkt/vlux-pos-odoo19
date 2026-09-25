import { registry } from "@web/core/registry";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as TicketScreen from "@point_of_sale/../tests/pos/tours/utils/ticket_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import { scan_barcode } from "@point_of_sale/../tests/generic_helpers/utils";

// The cashier's day with the VLUX patches loaded: sell in cash, refund part
// of the ticket from the order list, pay the refund back in cash and close
// the register counting the drawer. The Python side checks the money and the stock.
registry.category("web_tour.tours").add("VluxRegisterDayTour", {
    steps: () =>
        [
            Chrome.startPoS(),
            Dialog.confirm("Open Register"),
            scan_barcode("7509990000011"),
            scan_barcode("7509990000011"),
            ProductScreen.selectedOrderlineHas("Agua Tour 1L", 2),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),

            ...ProductScreen.clickRefund(),
            TicketScreen.selectOrder("001"),
            ProductScreen.clickNumpad("1"),
            TicketScreen.toRefundTextContains("To Refund: 1"),
            TicketScreen.confirmRefund(),
            PaymentScreen.isShown(),
            PaymentScreen.clickPaymentMethod("Cash"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.isShown(),
            ReceiptScreen.clickNextOrder(),

            // Float 0 + 2 x 12 sold - 1 x 12 refunded = 12 in the drawer.
            Chrome.clickMenuOption("Close Register"),
            ProductScreen.closeWithCashAmount("12"),
            ProductScreen.cashDifferenceIs("0.00"),
            {
                content: "close the register with the exact count",
                trigger: ".modal .modal-footer .btn:contains(close register)",
                run: "click",
                expectUnloadPage: true,
            },
        ].flat(),
});
