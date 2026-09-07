/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, useState, onWillUpdateProps } from "@odoo/owl";
import { cookie } from "@web/core/browser/cookie";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { setBfDarkEnabled } from "./dark_mode_state";

class BFDarkModeSystray extends Component {
    static props = {};

    _applyTheme() {
        const dark = this.state.color_scheme === "dark";
        document.body.classList.toggle("bf_dark_mode", dark);
        // Tell the JS that cannot see a class — the chatter renders email
        // bodies in a shadow root, out of reach of the stylesheet.
        setBfDarkEnabled(dark);
    }

    _onClick() {
        this.state.color_scheme =
            this.state.color_scheme === "light" ? "dark" : "light";
        const enabled = this.state.color_scheme === "dark";
        // Instant client-side toggle stays in the per-browser cookie...
        cookie.set("bf_color_scheme", this.state.color_scheme);
        this._applyTheme();
        // ...and is persisted to the user so the choice follows them across
        // devices. Fire-and-forget: a failed write must not break the toggle.
        this.orm
            .write("res.users", [user.userId], {
                bf_dark_mode_enabled: enabled,
            })
            .catch(() => {});
    }

    setup() {
        this.state = useState({ color_scheme: "light" });
        this.orm = useService("orm");
        super.setup();
        // Source of truth on load: the per-user preference shipped in the
        // session (follows the user across browsers/devices and can be
        // admin-defaulted). Fall back to the local cookie, then to "light".
        let scheme;
        if (session.bf_dark_mode_enabled !== undefined) {
            scheme = session.bf_dark_mode_enabled ? "dark" : "light";
        } else {
            scheme = cookie.get("bf_color_scheme") || "light";
        }
        this.state.color_scheme = scheme;
        cookie.set("bf_color_scheme", scheme);
        this._applyTheme();
        onWillUpdateProps(() => this._applyTheme());
    }
}
BFDarkModeSystray.template = "bf_dark_mode.SystrayItem";

export const systrayItem = {
    Component: BFDarkModeSystray,
    isDisplayed: () => true,
};

registry
    .category("systray")
    .add("BFDarkModeSystrayItem", systrayItem, { sequence: 1 });
