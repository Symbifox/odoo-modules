/** @odoo-module **/

import { Component, onMounted, onWillDestroy, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { BfWebmailDialog } from "./bf_webmail_dialog";

export class BfWebmailSystray extends Component {
    static template = "bf_webmail.Systray";
    static props = [];

    setup() {
        this.dialogService = useService("dialog");
        this.state = useState({
            unread: 0,
            configured: false,
            webmailUrl: "",
        });
        this._pollInterval = null;

        onMounted(async () => {
            try {
                await this._loadConfig();
                await this._checkUnread();
                this._pollInterval = setInterval(() => this._checkUnread(), 120_000);
            } catch (e) {
                console.error("bf_webmail: mount error", e);
            }
        });

        onWillDestroy(() => {
            if (this._pollInterval) {
                clearInterval(this._pollInterval);
            }
        });
    }

    async _loadConfig() {
        try {
            const config = await rpc("/bf_webmail/config");
            this.state.webmailUrl = config.webmail_url || "";
        } catch (e) {
            console.error("bf_webmail: config load failed", e);
        }
    }

    async _checkUnread() {
        try {
            const result = await rpc("/bf_webmail/unread");
            this.state.unread = result.unread || 0;
            this.state.configured = result.configured || false;
        } catch (e) {
            console.error("bf_webmail: unread check failed", e);
        }
    }

    get hasUnread() {
        return this.state.unread > 0;
    }

    get unreadLabel() {
        return this.state.unread > 99 ? "99+" : String(this.state.unread);
    }

    onOpenWebmail() {
        if (!this.state.webmailUrl) return;
        this.dialogService.add(BfWebmailDialog, {
            url: this.state.webmailUrl,
        });
    }
}

export const systrayWebmailItem = {
    Component: BfWebmailSystray,
};

registry.category("systray").add("BfWebmailMenu", systrayWebmailItem, { sequence: 5 });
