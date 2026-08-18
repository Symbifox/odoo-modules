/** @odoo-module **/

import { Component, onMounted, onWillDestroy, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

// Personal inbox: scoped to the current user's OWN emails (user_id). Without
// this leaf, accounts in the "tous les courriels" admin group (record rule
// (1=1)) would have their badge count every user's unhandled inbox, not their
// own. Kept in sync with the bf_email_action domain and the filter_inbox filter.
function inboxDomain() {
    return [
        ["user_id", "=", user.userId],
        ["is_handled", "=", false],
        "|",
        ["imap_in_inbox", "=", true],
        ["source", "in", ["chatter", "gateway"]],
    ];
}

export class BfEmailSystray extends Component {
    static template = "bf_email_systray.Systray";
    static props = [];

    setup() {
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.state = useState({ count: 0 });
        this._pollInterval = null;

        onMounted(async () => {
            try {
                await this._refresh();
                this._pollInterval = setInterval(() => this._refresh(), 120_000);
            } catch (e) {
                console.error("bf_email_systray: mount error", e);
            }
        });

        onWillDestroy(() => {
            if (this._pollInterval) {
                clearInterval(this._pollInterval);
            }
        });
    }

    async _refresh() {
        try {
            const count = await this.orm.searchCount("bf.email", inboxDomain());
            this.state.count = count || 0;
        } catch (e) {
            console.error("bf_email_systray: count failed", e);
        }
    }

    get hasCount() {
        return this.state.count > 0;
    }

    get countLabel() {
        return this.state.count > 99 ? "99+" : String(this.state.count);
    }

    onOpen() {
        // La boîte de réception est une action cliente OWL depuis
        // bf_email_management 18.0.9.0.0 ; `bf_email_action` est devenu la
        // variante « (liste) », conservée pour les filtres et l'export. Le
        // bouton de la barre doit mener à ce que le menu ouvre.
        this.actionService.doAction(
            "bf_email_management.action_bf_email_inbox_owl"
        );
    }
}

export const systrayItem = { Component: BfEmailSystray };

registry.category("systray").add("BfEmailSystray", systrayItem, { sequence: 4 });
