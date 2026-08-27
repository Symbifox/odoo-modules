/** @odoo-module **/

import { Component, onMounted, onWillDestroy, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

// Personal inbox: scoped to the current user's OWN emails (user_id). Without
// this leaf, accounts in the "tous les courriels" admin group (record rule
// (1=1)) would have their badge count every user's unhandled inbox, not their
// own.
//
// ⚠️ This is a hand-written copy of `bf.email._inbox_domain()`, which is the
// single source of truth on the server side. It cannot import it — the badge
// counts before any action is opened, and a round-trip just to learn the
// domain would double the cost of every refresh. A test in
// bf_email_management asserts that this file still carries every leaf of the
// Python domain, so the copy cannot drift unnoticed.
//
// Third leaf (`imap_folder = false`) = the server copy's whereabouts are
// unknown, which happens when a restore fails because the folder was renamed
// or emptied in the webmail. Without it the row leaves "Traités" and lands in
// no working list at all.
function inboxDomain() {
    return [
        ["user_id", "=", user.userId],
        ["is_handled", "=", false],
        "|",
        "|",
        ["imap_in_inbox", "=", true],
        ["source", "in", ["chatter", "gateway"]],
        ["imap_folder", "=", false],
    ];
}

export class BfEmailSystray extends Component {
    static template = "bf_email_systray.Systray";
    static props = [];

    setup() {
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.busService = useService("bus_service");
        this.state = useState({ count: 0 });
        this._pollInterval = null;
        this._debounce = null;
        this._unsubscribe = null;

        onMounted(async () => {
            try {
                await this._refresh();
                // Le serveur pousse un tick dès qu'une ligne bouge. Le sondage
                // reste en filet — il rattrape un websocket tombé, et il est
                // maintenant assez rare pour ne rien coûter.
                this._pollInterval = setInterval(() => this._refresh(), 300_000);
                this._onBusTick = () => this._refreshSoon();
                this.busService.subscribe("bf_email/changed", this._onBusTick);
                this.busService.start();
            } catch (e) {
                console.error("bf_email_systray: mount error", e);
            }
        });

        onWillDestroy(() => {
            if (this._pollInterval) {
                clearInterval(this._pollInterval);
            }
            if (this._onBusTick) {
                this.busService.unsubscribe("bf_email/changed", this._onBusTick);
            }
            if (this._debounce) {
                clearTimeout(this._debounce);
            }
        });
    }

    /**
     * Une passe d'ingestion appelle create() par message : cinquante courriels
     * livrés ensemble produisent cinquante ticks. On n'en compte qu'un.
     */
    _refreshSoon() {
        if (this._debounce) {
            clearTimeout(this._debounce);
        }
        this._debounce = setTimeout(() => {
            this._debounce = null;
            this._refresh();
        }, 400);
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
