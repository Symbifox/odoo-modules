import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

/**
 * Offer "Install <app>" per application, for the applications a tenant chose.
 *
 * Odoo builds the whole scoped-app machinery generically (`/scoped_app` serves
 * any module as its own installable PWA, with its own scope, name and icon)
 * and then gates the user-menu entry on a hardcoded list of three actionPaths:
 * barcode, field-service, shop-floor. Its own comment says the feature works
 * for all apps and the list can grow. This replaces that list with a tenant
 * setting, `bluefox_branding.scoped_app_modules`, delivered in the session.
 * Empty means every application, which is what a fresh tenant gets.
 *
 * Three details Odoo's version can afford to skip and this one cannot:
 *
 * - **The module is read from the xmlid, not from the icon.** Odoo's own item
 *   derives it from `webIcon`, which names whichever module ships the image.
 *   That is not the same module on this fleet: 21 of 54 root menus draw their
 *   tile from a shared icon module, so `webIcon` would scope Project to
 *   `symbifox_icons`. The menu's xmlid names the module that owns the menu.
 * - **Its three apps all define an action `path`.** Ours mostly do not, so
 *   `actionPath` is false and the scoped URL would read "scoped_app/undefined".
 *   The fallback is the one the rest of the web client already uses for the
 *   same problem, in navbar.js and menu_helpers.js: `action-<actionID>`.
 * - An app we cannot resolve to a module falls back to installing the whole
 *   backend, which is Odoo's behaviour.
 */
function appModuleOf(app) {
    if (app.xmlid && app.xmlid.includes(".")) {
        return app.xmlid.split(".")[0];
    }
    if (app.webIcon && app.webIcon.includes(",")) {
        return app.webIcon.split(",")[0];
    }
    return "";
}

function installBrandedPWAItem(env) {
    const item = {
        type: "item",
        id: "install_pwa",
        description: _t("Install App"),
        callback: () => env.services.pwa.show(),
        show: () => env.services.pwa.isAvailable,
        sequence: 65,
    };
    const currentApp = env.services.menu.getCurrentApp();
    if (!currentApp) {
        return item;
    }
    const appId = appModuleOf(currentApp);
    const target =
        currentApp.actionPath || (currentApp.actionID && `action-${currentApp.actionID}`);
    if (!appId || !target) {
        return item;
    }
    const allowed = session.scoped_app_modules || [];
    if (allowed.length && !allowed.includes(appId)) {
        return item;
    }
    item.description = _t("Install %s", currentApp.name);
    item.callback = () => {
        browser.open(
            `/scoped_app?app_id=${encodeURIComponent(appId)}&path=${encodeURIComponent(
                "scoped_app/" + target
            )}&app_name=${encodeURIComponent(currentApp.name)}`
        );
    };
    // Nothing to install from inside an already-installed scoped app.
    item.show = () => !env.services.pwa.isScopedApp;
    return item;
}

registry
    .category("user_menuitems")
    .add("install_pwa", installBrandedPWAItem, { force: true });
