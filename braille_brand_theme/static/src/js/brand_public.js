(() => {
    "use strict";

    const BRAND = "Braille International";
    const MARK = "/braille_brand_theme/static/src/img/braille_mark.png";

    function replaceVisibleBrand(value) {
        return (value || "")
            .replace(/\bOdooBot\b/gi, "Asistente Braille")
            .replace(/\bMy Company\b/gi, BRAND)
            .replace(/www\.odoo\.com/gi, "brailleinternational.com")
            .replace(/\bOdoo\.com\b/gi, BRAND)
            .replace(/\bOdoo\b/gi, "Braille Platform");
    }

    function normalize(root = document) {
        document.documentElement.classList.add("bi-platform");

        const title = replaceVisibleBrand(document.title).trim();
        document.title =
            !title || title === "Braille Platform"
                ? BRAND
                : title.includes(BRAND)
                    ? title
                    : `${title} | ${BRAND}`;

        const elements = root.querySelectorAll
            ? root.querySelectorAll(
                "a, img, [title], [placeholder], [aria-label], [alt]"
            )
            : [];

        for (const element of elements) {
            for (const attribute of [
                "title",
                "placeholder",
                "aria-label",
                "alt",
            ]) {
                if (element.hasAttribute(attribute)) {
                    element.setAttribute(
                        attribute,
                        replaceVisibleBrand(element.getAttribute(attribute))
                    );
                }
            }

            if (
                element.tagName === "A" &&
                /^https?:\/\/(?:www\.)?odoo\.com/i.test(
                    element.getAttribute("href") || ""
                )
            ) {
                element.setAttribute(
                    "href",
                    "https://brailleinternational.com/"
                );
            }

            if (
                element.tagName === "IMG" &&
                /\/(?:odoo_logo|odoo-logo|odoo-icon)/i.test(
                    element.getAttribute("src") || ""
                )
            ) {
                element.setAttribute("src", MARK);
            }
        }

        const walker = document.createTreeWalker(
            root,
            NodeFilter.SHOW_TEXT
        );
        let node;
        while ((node = walker.nextNode())) {
            const parent = node.parentElement;
            if (
                !parent ||
                ["SCRIPT", "STYLE", "CODE", "PRE", "TEXTAREA"].includes(
                    parent.tagName
                )
            ) {
                continue;
            }

            const nextValue = replaceVisibleBrand(node.nodeValue);
            if (nextValue !== node.nodeValue) {
                node.nodeValue = nextValue;
            }
        }
    }

    function start() {
        normalize(document);
        new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                for (const node of mutation.addedNodes) {
                    if (node instanceof Element) {
                        normalize(node);
                    }
                }
            }
        }).observe(document.body, { childList: true, subtree: true });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
})();
