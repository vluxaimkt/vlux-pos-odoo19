(() => {
    "use strict";

    const BRAND = "Braille International";
    const PLATFORM = "Braille Platform";
    const MARK = "/braille_brand_theme/static/src/img/braille_mark.png";
    const ATTRIBUTES = [
        "placeholder",
        "title",
        "aria-label",
        "alt",
        "data-tooltip",
        "data-bs-original-title",
    ];
    const SKIP_TAGS = new Set([
        "SCRIPT",
        "STYLE",
        "NOSCRIPT",
        "CODE",
        "PRE",
        "TEXTAREA",
    ]);

    const replacementRules = [
        [/\bOdooBot\b/gi, "Asistente Braille"],
        [/\bMy Company\b/gi, BRAND],
        [/www\.odoo\.com/gi, "brailleinternational.com"],
        [/\bOdoo\.com\b/gi, BRAND],
        [/\bOdoo\b/gi, PLATFORM],
    ];

    function replaceBrand(value) {
        if (!value || typeof value !== "string") {
            return value;
        }
        let result = value;
        for (const [pattern, replacement] of replacementRules) {
            result = result.replace(pattern, replacement);
        }
        return result;
    }

    function normalizeTitle() {
        const raw = replaceBrand(document.title || "").trim();
        let title = raw;

        if (!title || title === PLATFORM || title === BRAND) {
            title = BRAND;
        } else if (!title.includes(BRAND)) {
            title = `${title} | ${BRAND}`;
        }

        if (document.title !== title) {
            document.title = title;
        }
    }

    function sanitizeTextNode(node) {
        const parent = node.parentElement;
        if (!parent || SKIP_TAGS.has(parent.tagName)) {
            return;
        }

        const nextValue = replaceBrand(node.nodeValue);
        if (nextValue !== node.nodeValue) {
            node.nodeValue = nextValue;
        }
    }

    function sanitizeElement(element) {
        if (!(element instanceof Element) || SKIP_TAGS.has(element.tagName)) {
            return;
        }

        for (const attribute of ATTRIBUTES) {
            if (!element.hasAttribute(attribute)) {
                continue;
            }
            const value = element.getAttribute(attribute);
            const nextValue = replaceBrand(value);
            if (nextValue !== value) {
                element.setAttribute(attribute, nextValue);
            }
        }

        if (element.tagName === "A") {
            const href = element.getAttribute("href") || "";
            if (/^https?:\/\/(?:www\.)?odoo\.com/i.test(href)) {
                element.setAttribute("href", "https://brailleinternational.com/");
            }
        }

        if (element.tagName === "IMG") {
            const src = element.getAttribute("src") || "";
            if (/\/(?:odoo_logo|odoo-logo|odoo-icon)/i.test(src)) {
                element.setAttribute("src", MARK);
            }
        }
    }

    function processTree(root) {
        if (!root) {
            return;
        }

        if (root.nodeType === Node.TEXT_NODE) {
            sanitizeTextNode(root);
            return;
        }

        if (!(root instanceof Element) && root !== document) {
            return;
        }

        if (root instanceof Element) {
            sanitizeElement(root);
        }

        const walker = document.createTreeWalker(
            root,
            NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT
        );

        let node;
        let count = 0;
        while ((node = walker.nextNode()) && count < 8000) {
            if (node.nodeType === Node.TEXT_NODE) {
                sanitizeTextNode(node);
            } else {
                sanitizeElement(node);
            }
            count += 1;
        }
    }

    function isDark() {
        return (
            document.documentElement.dataset.brailleTheme === "dark" ||
            document.cookie.split(";").some(
                (item) => item.trim() === "color_scheme=dark"
            )
        );
    }

    const lightColors = new Set([
        "rgb(255, 255, 255)",
        "rgb(248, 249, 250)",
        "rgb(247, 248, 249)",
        "rgb(245, 246, 247)",
        "rgb(244, 245, 246)",
        "rgb(243, 244, 245)",
        "rgb(242, 242, 242)",
    ]);

    const candidateSelector = [
        ".bg-white",
        ".bg-light",
        ".bg-100",
        ".o_search_panel",
        ".o_calendar_sidebar",
        ".o_calendar_header",
        ".o_calendar_renderer",
        ".o_mail_discuss",
        "[class*='o-mail-Discuss']",
        "[class*='o-mail-ChannelMember']",
        "[class*='o-mail-Thread']",
        "[class*='o-mail-Composer']",
        "[class*='Sidebar']",
        "[class*='sidebar']",
        "[class*='Renderer']",
        "[class*='renderer']",
        "[class*='Panel']",
        "[class*='panel']",
        "[style*='background']",
    ].join(",");

    function auditSurface(element) {
        if (!(element instanceof HTMLElement)) {
            return;
        }

        const rect = element.getBoundingClientRect();
        const explicitUtility =
            element.classList.contains("bg-white") ||
            element.classList.contains("bg-light") ||
            element.classList.contains("bg-100");

        if (!explicitUtility && rect.width * rect.height < 6000) {
            return;
        }

        const style = getComputedStyle(element);
        if (lightColors.has(style.backgroundColor)) {
            element.classList.add("bi-auto-surface");
        }

        if (
            element.hasAttribute("style") &&
            /(?:color\s*:\s*(?:#000(?:000)?|black|rgb\(\s*0\s*,\s*0\s*,\s*0\s*\)))/i.test(
                element.getAttribute("style")
            )
        ) {
            element.classList.add("bi-auto-text");
        }
    }

    function runVisualAudit(root = document) {
        if (!isDark()) {
            return;
        }

        const container =
            root instanceof Element || root === document ? root : document;

        if (container instanceof HTMLElement) {
            auditSurface(container);
        }

        const candidates = container.querySelectorAll
            ? container.querySelectorAll(candidateSelector)
            : [];

        let count = 0;
        for (const element of candidates) {
            auditSurface(element);
            count += 1;
            if (count >= 1800) {
                break;
            }
        }
    }

    let auditTimer = null;
    function scheduleAudit(root) {
        window.clearTimeout(auditTimer);
        auditTimer = window.setTimeout(() => {
            const callback = () => runVisualAudit(root || document);
            if ("requestIdleCallback" in window) {
                window.requestIdleCallback(callback, { timeout: 500 });
            } else {
                callback();
            }
        }, 180);
    }

    function start() {
        document.documentElement.classList.add("bi-platform");
        document.documentElement.dataset.bsTheme =
            isDark() ? "dark" : "light";

        normalizeTitle();
        processTree(document);
        scheduleAudit(document);

        const titleElement = document.querySelector("title");
        if (titleElement) {
            new MutationObserver(normalizeTitle).observe(titleElement, {
                childList: true,
                characterData: true,
                subtree: true,
            });
        }

        const observer = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                if (mutation.type === "characterData") {
                    sanitizeTextNode(mutation.target);
                    continue;
                }

                for (const node of mutation.addedNodes) {
                    processTree(node);
                }
            }
            normalizeTitle();
            scheduleAudit(document);
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true,
            characterData: true,
        });

        window.addEventListener("hashchange", () => {
            normalizeTitle();
            scheduleAudit(document);
        });
        window.addEventListener("popstate", () => {
            normalizeTitle();
            scheduleAudit(document);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
})();
