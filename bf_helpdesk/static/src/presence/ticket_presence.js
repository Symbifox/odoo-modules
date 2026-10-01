/** @odoo-module **/
// Présence en direct sur la fiche d'un billet : signale ma
// présence au serveur et affiche les collègues qui ont la même fiche ouverte.

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

export class BfTicketPresence extends Component {
    static template = "bf_helpdesk.TicketPresence";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ others: [] });
        this.timer = null;
        this.ticketId = null;
        this.onVisibility = () => {
            if (document.visibilityState === "visible") {
                this.ping();
            }
        };
        // Rechargement ou fermeture de l'onglet : le composant n'est pas
        // démonté, on signale le départ par une balise (sendBeacon survit à
        // la fermeture de la page).
        this.onPageHide = () => this.leaveBeacon(this.ticketId);
        onMounted(() => {
            document.addEventListener("visibilitychange", this.onVisibility);
            window.addEventListener("pagehide", this.onPageHide);
            this.ping();
        });
        onWillUnmount(() => {
            document.removeEventListener("visibilitychange", this.onVisibility);
            window.removeEventListener("pagehide", this.onPageHide);
            clearTimeout(this.timer);
            this.leave(this.ticketId);
        });
    }

    get resId() {
        return this.props.record.resId;
    }

    async ping() {
        clearTimeout(this.timer);
        const resId = this.resId;
        if (resId !== this.ticketId) {
            // Le pager a changé de billet : quitter l'ancien.
            this.leave(this.ticketId);
            this.ticketId = resId;
            this.state.others = [];
        }
        let interval = 25;
        if (resId && document.visibilityState === "visible") {
            try {
                const res = await this.orm.silent.call(
                    "helpdesk.ticket", "bf_presence_ping", [[resId]],
                );
                this.state.others = res.others;
                interval = res.interval;
            } catch {
                this.state.others = [];
            }
        }
        this.timer = setTimeout(() => this.ping(), interval * 1000);
    }

    leave(resId) {
        if (resId) {
            this.orm.silent.call("helpdesk.ticket", "bf_presence_leave", [[resId]]).catch(() => {});
        }
    }

    leaveBeacon(resId) {
        if (!resId || !navigator.sendBeacon) {
            return;
        }
        const payload = JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: {
                model: "helpdesk.ticket",
                method: "bf_presence_leave",
                args: [[resId]],
                kwargs: {},
            },
        });
        navigator.sendBeacon(
            "/web/dataset/call_kw/helpdesk.ticket/bf_presence_leave",
            new Blob([payload], { type: "application/json" }),
        );
    }

    get message() {
        const names = this.state.others.map((o) => o.name);
        if (!names.length) {
            return "";
        }
        const who = names.length === 1
            ? names[0]
            : `${names.slice(0, -1).join(", ")} et ${names.at(-1)}`;
        return names.length === 1
            ? `${who} consulte aussi ce billet.`
            : `${who} consultent aussi ce billet.`;
    }
}

registry.category("view_widgets").add("bf_ticket_presence", {
    component: BfTicketPresence,
});
