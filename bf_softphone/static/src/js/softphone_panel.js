/** @odoo-module **/
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class SoftphonePanel extends Component {
    static template = "bf_softphone.Panel";
    static props = { close: { type: Function, optional: true } };

    setup() {
        this.phone = useService("bf_softphone");
        this.action = useService("action");
        // ⚠️ useState(...) est OBLIGATOIRE ici. `phone.state` est un objet
        // `reactive()` du service : le lire directement donne les bonnes valeurs
        // au PREMIER rendu, mais n'ABONNE PAS le composant. Sans abonnement, le
        // passage « ça sonne » → « en communication » ne redessine rien : le
        // panneau restait sur l'écran d'appel entrant et le bouton Raccrocher
        // n'apparaissait jamais (signalé en ces termes : « je ne peux pas
        // raccrocher »). Les rendus qu'on observait venaient par accident d'un
        // AUTRE état réactif (this.ui / this.local) qui, lui, était abonné.
        this.state = useState(this.phone.state);
        // `dtmf` garde la trace des touches envoyées pendant l'appel : sans
        // retour visuel, on ne sait pas si le menu a reçu le 2 ou le 22.
        this.local = useState({
            dial: "", results: [], history: [], padOpen: false, dtmf: "",
        });
        this._searchTimer = null;

        onWillStart(async () => { await this.loadHistory(); });
    }

    // ---------- composition ----------
    press(k) {
        if (this.state.inCall) {
            // En communication, une touche n'écrit pas un numéro : elle répond
            // au menu d'en face (RFC 4733, le poste est en dtmf_mode=rfc4733).
            this.phone.sendDTMF(k);
            this.local.dtmf = (this.local.dtmf + k).slice(-16);
            return;
        }
        this.local.dial += k;
        this.onSearch();
    }

    togglePad() {
        this.local.padOpen = !this.local.padOpen;
        if (!this.local.padOpen) this.local.dtmf = "";
    }
    del() { this.local.dial = this.local.dial.slice(0, -1); }

    call() {
        const n = this.local.dial.trim();
        if (n) this.phone.callNumber(n);
    }
    callNumber(n) { this.phone.callNumber(n); }

    answer() { this.phone.answer(); }
    hangup() { this.phone.hangup(); }
    mute() { this.phone.toggleMute(); }

    // ---------- recherche de contacts (débounce) ----------
    onSearch() {
        const term = this.local.dial.trim();
        if (this._searchTimer) clearTimeout(this._searchTimer);
        if (term.length < 2 || /^[0-9*#+\s]+$/.test(term)) { this.local.results = []; return; }
        this._searchTimer = setTimeout(async () => {
            try { this.local.results = await this.phone.searchContacts(term); }
            catch (e) { this.local.results = []; }
        }, 250);
    }
    pickContact(c) {
        this.local.results = [];
        this.phone.callNumber(c.number);
    }

    // ---------- historique ----------
    async loadHistory() {
        try {
            const orm = this.env.services.orm;
            const rows = await orm.searchRead(
                "call.archive.call",
                [["import_batch_id", "!=", false]],
                ["contact_name", "call_type", "date", "duration", "thread_id"],
                { limit: 12, order: "date desc" },
            );
            this.local.history = rows.map((r) => ({
                id: r.id,
                name: r.contact_name || (r.thread_id && r.thread_id[1]) || "Inconnu",
                type: r.call_type,
                date: r.date,
            }));
        } catch (e) { this.local.history = []; }
    }

    openPartner() {
        if (!this.state.partnerId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: this.state.partnerId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}
