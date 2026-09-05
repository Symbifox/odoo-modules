/** @odoo-module **/
import { _t } from "@web/core/l10n/translation";
import { CommandPalette } from "@web/core/commands/command_palette";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";

// Namespace registered by universal_search_provider.js.
export const STAR_NAMESPACE = "*";

// Template of the footer command_service puts on the MAIN palette (Ctrl+K,
// the editor's Ctrl+K, openMainPalette() with no footer). A caller that
// brings its own footer (the user menu's "Shortcuts" entry) asked for the
// native command list on purpose and is left alone.
const MAIN_PALETTE_FOOTER = "web.DefaultFooter";

/** True when the server said this user wants Ctrl+K to open the "*" search. */
export function ctrlKOpensUniversalSearch() {
    return Boolean(session.bf_universal_search_ctrl_k_star);
}

/**
 * Return the config the palette should really open with: the same config,
 * pre-filled with the "*" namespace, when nothing else was asked for.
 * Exported so the decision can be tested without a palette.
 *
 * @param {import("@web/core/commands/command_palette").CommandPaletteConfig} config
 */
export function withUniversalSearchDefault(config) {
    if (!config || config.searchValue) {
        // The caller already chose a namespace and/or a query (systray button,
        // "/" menu shortcut, a nested palette returned by a command…).
        return config;
    }
    const providers = config.providers || [];
    if (!providers.some((provider) => provider.namespace === STAR_NAMESPACE)) {
        // Not the main palette: a nested palette with its own providers.
        return config;
    }
    const footer = config.FooterComponent;
    if (footer && footer.template !== MAIN_PALETTE_FOOTER) {
        return config;
    }
    return { ...config, searchValue: STAR_NAMESPACE };
}

patch(CommandPalette.prototype, {
    async setCommandPaletteConfig(config) {
        if (ctrlKOpensUniversalSearch()) {
            config = withUniversalSearchDefault(config);
        }
        return super.setCommandPaletteConfig(config);
    },

    async setCommands(namespace, options = {}) {
        await super.setCommands(namespace, options);
        // An empty "*" palette is not "no result", it is "nothing typed yet":
        // say so, since the palette now opens there before any keystroke.
        if (
            namespace === STAR_NAMESPACE &&
            !this.state.commands.length &&
            (options.searchValue || "").trim().length < 2
        ) {
            this.state.emptyMessage = _t(
                "Tapez au moins deux caractères pour chercher partout."
            );
        }
    },
});
