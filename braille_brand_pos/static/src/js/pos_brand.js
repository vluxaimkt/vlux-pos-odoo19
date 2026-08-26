(() => {
    "use strict";

    const BRAND = "Braille International";
    const MARK = "/braille_brand_theme/static/src/img/braille_mark.png";

    function normalize() {
        document.documentElement.classList.add("bi-pos-platform");
        const title = (document.title || "")
            .replace(/\bOdoo\b/gi, "Braille Platform")
            .trim();
        document.title =
            !title || title === "Braille Platform"
                ? `${BRAND} | Punto de Venta`
                : title.includes(BRAND)
                    ? title
                    : `${title} | ${BRAND}`;

        for (const image of document.querySelectorAll(
            "img[src*='odoo_logo'], img[src*='odoo-logo'], img[src*='odoo-icon']"
        )) {
            image.src = MARK;
        }

        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_TEXT
        );
        let node;
        while ((node = walker.nextNode())) {
            if (["SCRIPT", "STYLE", "CODE", "PRE"].includes(
                node.parentElement?.tagName
            )) {
                continue;
            }
            node.nodeValue = (node.nodeValue || "")
                .replace(/\bOdooBot\b/gi, "Asistente Braille")
                .replace(/\bOdoo\b/gi, "Braille Platform");
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", normalize, { once: true });
    } else {
        normalize();
    }

    new MutationObserver(normalize).observe(document.documentElement, {
        childList: true,
        subtree: true,
    });
})();
