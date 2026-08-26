/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

const COOKIE = "color_scheme";
const STORAGE = "braille_color_scheme";
const VALID = new Set(["dark", "light"]);

function readCookie(name) {
    const prefix = `${name}=`;
    for (const part of document.cookie.split(";")) {
        const item = part.trim();
        if (item.startsWith(prefix)) {
            return decodeURIComponent(item.slice(prefix.length));
        }
    }
    return null;
}

function writeCookie(name, value) {
    const secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
        `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
}

function applyMode(mode) {
    document.documentElement.dataset.brailleTheme = mode;
    document.documentElement.dataset.bsTheme = mode;
    document.documentElement.classList.add("bi-platform");
    document.documentElement.style.colorScheme = mode;
}

const service = {
    dependencies: ["notification"],

    start(env, { notification }) {
        let mode = readCookie(COOKIE);
        const stored = localStorage.getItem(STORAGE);

        if (!VALID.has(mode) && VALID.has(stored)) {
            mode = stored;
            writeCookie(COOKIE, mode);
            location.reload();
        }

        if (!VALID.has(mode)) {
            mode = "dark";
            writeCookie(COOKIE, mode);
            localStorage.setItem(STORAGE, mode);
            location.reload();
        }

        applyMode(mode);

        function setMode(nextMode) {
            if (!VALID.has(nextMode) || nextMode === mode) {
                return;
            }

            mode = nextMode;
            writeCookie(COOKIE, mode);
            localStorage.setItem(STORAGE, mode);
            applyMode(mode);

            notification.add(
                mode === "dark"
                    ? _t("Tema oscuro Braille activado")
                    : _t("Tema claro Braille activado"),
                { type: "success" }
            );

            window.setTimeout(() => location.reload(), 100);
        }

        return {
            getMode: () => mode,
            isDark: () => mode === "dark",
            setMode,
            toggle: () => setMode(mode === "dark" ? "light" : "dark"),
        };
    },
};

registry.category("services").add("braille_theme", service);

registry.category("user_menuitems").add(
    "braille_theme_toggle",
    (env) => ({
        type: "switch",
        id: "braille_theme_toggle",
        description: _t("Tema oscuro Braille"),
        isChecked: env.services.braille_theme.isDark(),
        callback: () => env.services.braille_theme.toggle(),
        sequence: 45,
    }),
    { force: true }
);
