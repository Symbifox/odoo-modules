/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

/**
 * Systray launcher: a toggleable button (shown only to group_nc_browser_user)
 * that opens the full Nextcloud web app in a centered ~75%-of-screen popup
 * window. An embedded iframe is impossible — Nextcloud sends
 * X-Frame-Options: SAMEORIGIN / frame-ancestors 'self' — so we open a real
 * window instead.
 */
export class NcLauncherSystray extends Component {
    static template = "bf_nextcloud_browser.NcLauncherSystray";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.state = useState({ show: false, url: "" });
        onWillStart(async () => {
            if (!(await user.hasGroup("bf_nextcloud_browser.group_nc_browser_user"))) {
                return;
            }
            try {
                const url = await this.orm.call("bf.nc.browser", "get_app_url", []);
                if (url) {
                    this.state.url = url;
                    this.state.show = true;
                }
            } catch {
                // No config / no access: stay hidden.
            }
        });
    }

    open() {
        const w = Math.round(screen.availWidth * 0.75);
        const h = Math.round(screen.availHeight * 0.75);
        const left = Math.round((screen.availWidth - w) / 2);
        const top = Math.round((screen.availHeight - h) / 2);
        const win = window.open(
            this.state.url,
            "bf_nc_app",
            `width=${w},height=${h},left=${left},top=${top},resizable=yes,scrollbars=yes`
        );
        if (win) {
            win.opener = null; // sever the opener reference (defence in depth)
        }
    }
}

export const ncLauncherSystrayItem = { Component: NcLauncherSystray };
registry
    .category("systray")
    .add("bf_nextcloud_browser.launcher", ncLauncherSystrayItem, { sequence: 30 });
