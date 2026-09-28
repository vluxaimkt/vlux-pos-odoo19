import { registry } from "@web/core/registry";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as PaymentScreen from "@point_of_sale/../tests/pos/tours/utils/payment_screen_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ReceiptScreen from "@point_of_sale/../tests/pos/tours/utils/receipt_screen_util";
import * as PosHr from "@pos_hr/../tests/tours/utils/pos_hr_helpers";
import * as PartnerList from "@point_of_sale/../tests/pos/tours/utils/partner_list_util";
import { negate } from "@point_of_sale/../tests/generic_helpers/utils";

/*
 * Sales on credit at the register, with employee login:
 * - a cashier does not see "Crédito";
 * - the encargado sells on credit to an authorised customer and the ticket
 *   reads VENTA A CRÉDITO with the previous and new balance;
 * - a customer without credit, or a sale above the limit, is refused with a
 *   plain explanation (nothing is sold).
 */
const PRODUCT = "Refresco Crédito";

function sellOneAndPay() {
    return [ProductScreen.clickDisplayedProduct(PRODUCT), ProductScreen.clickPayButton()].flat();
}

function refusedWith(text) {
    return [
        {
            content: `the register refuses: ${text}`,
            trigger: `.modal-body:contains("${text}")`,
        },
        Dialog.confirm(),
    ];
}

registry.category("web_tour.tours").add("VluxCreditRegisterTour", {
    steps: () =>
        [
            Chrome.clickBtn("Open Register"),
            PosHr.loginScreenIsShown(),
            PosHr.login("Pos Employee2", "1234"),
            Dialog.confirm("Open Register"),

            // A cashier: no Crédito on the payment screen.
            sellOneAndPay(),
            PaymentScreen.isShown(),
            {
                content: "a cashier does not see the Crédito payment method",
                trigger: negate(".paymentmethod:contains('Crédito')", ".payment-screen"),
            },
            PaymentScreen.clickBack(),

            // The encargado sells on credit to an authorised customer.
            PosHr.clickLockButton(),
            Chrome.clickBtn("Unlock Register"),
            PosHr.login("Test Manager 2", "5652"),
            ProductScreen.clickPayButton(),
            PaymentScreen.clickPartnerButton(),
            PaymentScreen.clickCustomer("Doña Lupe Crédito"),
            PaymentScreen.clickPaymentMethod("Crédito"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.receiptIsThere(),
            {
                content: "the ticket says it is a sale on credit",
                trigger: ".pos-receipt .vlux-receipt-sale:contains('VENTA A CRÉDITO')",
            },
            {
                content: "previous balance 0",
                trigger: ".pos-receipt .vlux-credit-prev:contains('0.00')",
            },
            {
                content: "new balance 18",
                trigger: ".pos-receipt .vlux-credit-new:contains('18.00')",
            },
            ReceiptScreen.clickNextOrder(),

            // A customer without credit is refused.
            sellOneAndPay(),
            PaymentScreen.clickPartnerButton(),
            PaymentScreen.clickCustomer("Cliente Sin Crédito"),
            PaymentScreen.clickPaymentMethod("Crédito"),
            PaymentScreen.clickValidate(),
            refusedWith("no tiene crédito autorizado"),

            // Doña Lupe owes 18, her limit is 30: another 18 goes over.
            PaymentScreen.clickPartnerButton(),
            PaymentScreen.clickCustomer("Doña Lupe Crédito"),
            PaymentScreen.clickValidate(),
            refusedWith("lo rebasa"),
            PaymentScreen.isShown(),
            Chrome.endTour(),
        ].flat(),
});

function searchCustomer(name) {
    return {
        content: `search customer "${name}"`,
        trigger: ".modal-dialog .input-group input",
        run: `edit ${name}`,
    };
}

/*
 * The encargado authorises a customer for credit from the register's customer
 * list and sells to them on credit right away; a cashier sees what the
 * customer owes but not the "Crédito…" option.
 */
registry.category("web_tour.tours").add("VluxCreditAuthorizeTour", {
    steps: () =>
        [
            Chrome.clickBtn("Open Register"),
            PosHr.loginScreenIsShown(),
            PosHr.login("Test Manager 2", "5652"),
            Dialog.confirm("Open Register"),

            ProductScreen.clickPartnerButton(),
            searchCustomer("Cliente Nuevo Crédito"),
            PartnerList.clickPartnerOptions("Cliente Nuevo Crédito"),
            PartnerList.clickDropDownItemText("Crédito…"),
            {
                content: "authorise credit",
                trigger: ".vlux-credit-dialog #vluxCreditAllowed",
                run: "click",
            },
            {
                content: "limit 100",
                trigger: ".vlux-credit-dialog #vluxCreditLimit",
                run: "edit 100",
            },
            {
                content: "save",
                trigger: ".vlux-credit-save",
                run: "click",
            },
            {
                content: "the dialog closed after saving",
                trigger: negate(".vlux-credit-dialog"),
            },
            PartnerList.clickPartner("Cliente Nuevo Crédito"),

            sellOneAndPay(),
            PaymentScreen.clickPaymentMethod("Crédito"),
            PaymentScreen.clickValidate(),
            ReceiptScreen.receiptIsThere(),
            {
                content: "sold on credit to the customer just authorised",
                trigger: ".pos-receipt .vlux-receipt-sale:contains('VENTA A CRÉDITO')",
            },
            ReceiptScreen.clickNextOrder(),

            // A cashier sees the debt, not the option.
            PosHr.clickLockButton(),
            Chrome.clickBtn("Unlock Register"),
            PosHr.login("Pos Employee2", "1234"),
            ProductScreen.clickPartnerButton(),
            searchCustomer("Cliente Nuevo Crédito"),
            {
                content: "the customer list shows what the customer owes",
                trigger: ".partner-info:contains('Cliente Nuevo Crédito') .vlux-partner-debt:contains('18.00')",
            },
            PartnerList.clickPartnerOptions("Cliente Nuevo Crédito"),
            PartnerList.checkDropDownItemText("All Orders"),
            {
                content: "a cashier cannot authorise credit",
                trigger: negate(".o-dropdown-item:contains('Crédito')"),
            },
            Chrome.endTour(),
        ].flat(),
});
