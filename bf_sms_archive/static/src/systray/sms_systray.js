/** @odoo-module **/
import { Component, useState, onWillStart, onWillDestroy } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

class SmsSystray extends Component {
    static template = "bf_sms_archive.Systray";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.busService = useService("bus_service");
        this.state = useState({ total: 0, visible: false });
        this._busCb = () => this.refresh();

        // Rien n'est attendu ici. Le client web n'affiche rien tant que
        // tous ses composants n'ont pas fini leur onWillStart ; le droit puis le
        // compte des non-lus, en chaîne, retardaient tout l'écran.
        onWillStart(() => {
            this._start();
        });

        // onWillDestroy et non onWillUnmount : il passe aussi pour un composant
        // détruit avant d'avoir été monté, qui sinon resterait abonné au bus.
        onWillDestroy(() => {
            this._gone = true;
            this.busService.unsubscribe("sms.archive/new", this._busCb);
            this.busService.unsubscribe("sms.archive/read", this._busCb);
        });
    }

    async _start() {
        const visible = await user.hasGroup("bf_sms_archive.group_sms_user");
        if (this._gone) {
            return;
        }
        this.state.visible = visible;
        if (!visible) {
            return;
        }
        // Abonnement au canal partenaire assuré par le bus core ; on écoute juste le type.
        this.busService.subscribe("sms.archive/new", this._busCb);
        // …et le ping « lu » pour décrémenter le badge dès qu'on lit un message.
        this.busService.subscribe("sms.archive/read", this._busCb);
        await this.refresh();
    }

    async refresh() {
        try {
            const res = await this.orm.call("sms.archive.thread", "get_unread_summary", []);
            this.state.total = res.total || 0;
        } catch {
            // pas de droit / modèle indisponible : reste discret
        }
    }

    openMessenger() {
        this.action.doAction({
            type: "ir.actions.client",
            tag: "sms_archive_messenger",
            name: "Messagerie",
        });
    }
}

export const systrayItem = { Component: SmsSystray };
registry.category("systray").add("bf_sms_archive.Systray", systrayItem, { sequence: 25 });
