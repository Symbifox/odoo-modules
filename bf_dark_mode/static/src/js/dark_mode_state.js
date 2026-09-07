/** @odoo-module **/
import { reactive } from "@odoo/owl";

/**
 * Single source of truth for the JS that has to REACT to the theme.
 *
 * The stylesheet keys off the `bf_dark_mode` class on `<body>`, which is
 * enough for anything CSS can reach. It is not enough for content living in a
 * shadow root — the chatter renders every email body in one, and a shadow tree
 * ignores the page stylesheet entirely. Those places need to be told, in JS,
 * when the theme flips, hence this shared reactive flag: the systray toggle
 * writes it, the chatter patch subscribes to it.
 */
export const bfDarkState = reactive({
    enabled: Boolean(document.body?.classList.contains("bf_dark_mode")),
});

export function setBfDarkEnabled(enabled) {
    bfDarkState.enabled = Boolean(enabled);
}
