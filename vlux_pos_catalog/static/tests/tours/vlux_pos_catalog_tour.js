import { registry } from "@web/core/registry";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import { scan_barcode, negateStep } from "@point_of_sale/../tests/generic_helpers/utils";

const KNOWN_BARCODE = "7509990000011";
const UNKNOWN_BARCODE = "7509990000028";
const UNKNOWN_BARCODE_WITH_PHOTO = "7509990000035";
// Exists in the catalog but is not sold in the POS: the register does not know it.
const DUPLICATE_BARCODE = "7509990000042";

function openRegister() {
    return [Chrome.startPoS(), Dialog.confirm("Open Register")].flat();
}

function dialogIsOpenFor(barcode) {
    return {
        content: `quick create dialog shown for ${barcode}`,
        trigger: `.vlux-catalog-dialog .vlux-catalog-barcode:contains("${barcode}")`,
    };
}

function fillQuickForm(name, price) {
    return [
        {
            content: "type product name",
            trigger: ".vlux-catalog-dialog .vlux-catalog-input-name",
            run: `edit ${name}`,
        },
        {
            content: "type product price",
            trigger: ".vlux-catalog-dialog .vlux-catalog-input-price",
            run: `edit ${price}`,
        },
    ];
}

function attachGeneratedPhoto() {
    return {
        content: "attach a generated photo through the hidden file input",
        // The input is hidden (d-none); the user reaches it through the photo
        // button, the tour reaches it through the DOM.
        trigger: ".vlux-catalog-dialog .vlux-catalog-photo-button",
        run: async () => {
            const anchor = document.querySelector(".vlux-catalog-dialog .vlux-catalog-file-input");
            const canvas = document.createElement("canvas");
            canvas.width = 640;
            canvas.height = 480;
            const context = canvas.getContext("2d");
            context.fillStyle = "#2255aa";
            context.fillRect(0, 0, 640, 480);
            const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
            const transfer = new DataTransfer();
            transfer.items.add(new File([blob], "tour.png", { type: "image/png" }));
            anchor.files = transfer.files;
            anchor.dispatchEvent(new Event("change", { bubbles: true }));
        },
    };
}

function confirmQuickForm() {
    return {
        content: "save and add",
        trigger: ".vlux-catalog-dialog .vlux-catalog-confirm:not(:disabled)",
        run: "click",
    };
}

registry.category("web_tour.tours").add("VluxCatalogKnownBarcodeTour", {
    steps: () =>
        [
            openRegister(),
            scan_barcode(KNOWN_BARCODE),
            ProductScreen.selectedOrderlineHas("Agua Tour 1L", 1),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxCatalogRepeatedScanTour", {
    steps: () =>
        [
            openRegister(),
            scan_barcode(KNOWN_BARCODE),
            scan_barcode(KNOWN_BARCODE),
            scan_barcode(KNOWN_BARCODE),
            scan_barcode(KNOWN_BARCODE),
            scan_barcode(KNOWN_BARCODE),
            ProductScreen.selectedOrderlineHas("Agua Tour 1L", 5),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxCatalogUnknownBarcodeTour", {
    steps: () =>
        [
            openRegister(),
            scan_barcode(UNKNOWN_BARCODE),
            dialogIsOpenFor(UNKNOWN_BARCODE),
            fillQuickForm("Producto Nuevo Tour", "42.50"),
            confirmQuickForm(),
            negateStep(dialogIsOpenFor(UNKNOWN_BARCODE)),
            ProductScreen.selectedOrderlineHas("Producto Nuevo Tour", 1),
            // The product is now part of the session catalog: a second scan is a plain hit.
            scan_barcode(UNKNOWN_BARCODE),
            ProductScreen.selectedOrderlineHas("Producto Nuevo Tour", 2),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxCatalogUnknownBarcodeWithPhotoTour", {
    steps: () =>
        [
            openRegister(),
            scan_barcode(UNKNOWN_BARCODE_WITH_PHOTO),
            dialogIsOpenFor(UNKNOWN_BARCODE_WITH_PHOTO),
            fillQuickForm("Producto Con Foto", "9.99"),
            attachGeneratedPhoto(),
            {
                content: "preview is rendered from the downscaled image",
                trigger: ".vlux-catalog-dialog .vlux-catalog-photo-preview img",
            },
            confirmQuickForm(),
            ProductScreen.selectedOrderlineHas("Producto Con Foto", 1),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxCatalogDeniedTour", {
    steps: () =>
        [
            openRegister(),
            scan_barcode(UNKNOWN_BARCODE),
            {
                content: "standard unknown barcode notification, no quick create dialog",
                trigger: ".o_notification:contains('Unknown Barcode')",
            },
            negateStep(dialogIsOpenFor(UNKNOWN_BARCODE)),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxCatalogDuplicateBarcodeTour", {
    steps: () =>
        [
            openRegister(),
            scan_barcode(DUPLICATE_BARCODE),
            dialogIsOpenFor(DUPLICATE_BARCODE),
            fillQuickForm("Duplicado Tour", "5.00"),
            confirmQuickForm(),
            {
                content: "the dialog stays open and names the product that already owns the barcode",
                trigger: ".vlux-catalog-dialog:contains('Este codigo ya pertenece a un producto')",
            },
            {
                content: "the existing product is offered instead of creating a second one",
                trigger: ".vlux-catalog-dialog .vlux-catalog-use-existing",
                run: "click",
            },
            negateStep(dialogIsOpenFor(DUPLICATE_BARCODE)),
            ProductScreen.selectedOrderlineHas("Refresco Duplicado", 1),
            Chrome.endTour(),
        ].flat(),
});
