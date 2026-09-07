/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { Message } from "@mail/core/common/message";
import { useEffect, useState } from "@odoo/owl";
import { bfDarkState } from "./dark_mode_state";

/**
 * Make received emails readable in the chatter when the dark theme is on.
 *
 * Odoo 18 renders the body of every message whose type contains "email"
 * inside a SHADOW ROOT (`message.xml`, `t-ref="shadowBody"`), so that the
 * sender's HTML cannot leak into the backend styles. A shadow tree also
 * ignores the page stylesheet, which is why none of this module's SCSS
 * reaches it: an email that ships `color:#000` on its paragraphs — 71% of the
 * emails measured in the Blue Fox chatter — paints black text on the dark
 * surface and becomes unreadable.
 *
 * The core has the answer already: it injects a flattening stylesheet into
 * that shadow root, but only when `cookie.get("color_scheme") === "dark"` —
 * the Enterprise dark mode's own signal, which this module never sets (it
 * keeps its own `bf_color_scheme` cookie and a class on `<body>`, because
 * Odoo 18 CE ships the plumbing of a dark mode without its palette). So the
 * stylesheet is injected here instead, with the Blue Fox palette, and it is
 * added and removed live so the systray toggle stays instantaneous on a
 * chatter that is already on screen.
 */

const BF_SHADOW_STYLE_ID = "bf_dark_mode_shadow_style";

// Mirrors bf_dark_mode/static/src/scss/dark_mode.scss — keep in step.
const BF_SHADOW_CSS = `
    * {
        background-color: transparent !important;
        color: #d1d5d8 !important;
        border-color: #4a5153 !important;
    }
    a, a * {
        color: #54bfe8 !important;
    }
    a:hover, a *:hover {
        color: #7fd2ef !important;
    }
    .o-mail-Message-searchHighlight {
        background: #e99d00bf !important;
        color: #1a1d1e !important;
    }
`;

patch(Message.prototype, {
    setup() {
        super.setup();
        const darkState = useState(bfDarkState);
        useEffect(
            (enabled) => {
                // `this.shadowRoot` only exists on email-type messages, and
                // only from the core's own onMounted — which has run by the
                // time this effect does, since it was registered first.
                const root = this.shadowRoot;
                if (!root) {
                    return;
                }
                if (!enabled) {
                    root.getElementById?.(BF_SHADOW_STYLE_ID)?.remove();
                    return;
                }
                if (root.getElementById?.(BF_SHADOW_STYLE_ID)) {
                    return;
                }
                const style = document.createElement("style");
                style.id = BF_SHADOW_STYLE_ID;
                style.textContent = BF_SHADOW_CSS;
                root.appendChild(style);
            },
            () => [darkState.enabled]
        );
    },
});
