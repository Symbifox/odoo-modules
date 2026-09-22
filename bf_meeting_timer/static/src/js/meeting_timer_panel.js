/** @odoo-module **/

/**
 * Le panneau du chronomètre, posé dans le formulaire de l'ordre du jour.
 *
 * Trois règles de construction, chacune payée ailleurs :
 *
 * 1. **Le panneau n'écrit jamais par le formulaire.** Les notes en direct vivent
 *    dans le même formulaire ; une écriture par l'enregistrement client salirait
 *    ce que la personne est en train de taper, et une sauvegarde de formulaire
 *    écraserait le chronomètre. Tout passe par des appels nommés au serveur, et
 *    l'état affiché vient de la réponse, pas du `record`.
 * 2. **L'horloge est celle du serveur.** Chaque réponse porte `server_now` ; on
 *    en tire un décalage qu'on applique à l'horloge du navigateur, qui ment et
 *    dont l'onglet s'endort.
 * 3. **Le battement s'arrête avec le composant.** Un intervalle qui survit à son
 *    composant tient une promesse morte, et c'est ainsi qu'on fige tous les
 *    chatters d'une base.
 */

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const MODELE = "meeting.agenda";

export class MeetingTimerPanel extends Component {
    static template = "bf_meeting_timer.Panel";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.state = useState({ payload: null, busy: false, tick: 0 });
        this.decalage = 0;          // horloge serveur moins horloge navigateur
        this.dernierEchange = 0;    // instant navigateur du dernier échange

        onWillStart(async () => {
            await this.charger();
        });

        this.battement = setInterval(() => {
            this.state.tick++;
            // Resynchronisation douce : un sujet ajouté depuis un autre onglet
            // n'existe pas pour le panneau tant qu'on ne relit pas.
            const p = this.state.payload;
            if (p && p.state === "running" && this.state.tick % 20 === 0) {
                this.charger();
            }
        }, 1000);
        onWillUnmount(() => clearInterval(this.battement));

        useHotkey("alt+s", () => this.geste("action_timer_split"), { bypassEditableProtection: true });
        useHotkey("alt+p", () => {
            const p = this.state.payload;
            if (!p) {
                return;
            }
            if (p.state === "running") {
                this.geste("action_timer_pause");
            } else if (p.state === "paused") {
                this.geste("action_timer_resume");
            }
        }, { bypassEditableProtection: true });
    }

    get resId() {
        return this.props.record.resId;
    }

    async charger() {
        if (!this.resId) {
            return;
        }
        const payload = await this.orm.call(MODELE, "timer_payload", [[this.resId]]);
        this.poser(payload);
    }

    poser(payload) {
        this.state.payload = payload;
        this.dernierEchange = Date.now();
        const serveur = deserializeDateTime(payload.server_now);
        this.decalage = serveur.toMillis() - this.dernierEchange;
    }

    /** Secondes écoulées depuis la réponse du serveur, si le chronomètre avance. */
    get derive() {
        const p = this.state.payload;
        if (!p || p.state !== "running") {
            return 0;
        }
        return Math.max(0, Math.round((Date.now() - this.dernierEchange) / 1000));
    }

    async geste(methode, args = []) {
        if (!this.resId || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const payload = await this.orm.call(MODELE, methode, [[this.resId], ...args]);
            this.poser(payload);
        } finally {
            this.state.busy = false;
        }
    }

    async demarrer() {
        if (this.props.record.isDirty) {
            await this.props.record.save();
        }
        await this.geste("action_timer_start");
    }

    async effacer() {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Effacer le chronomètre"),
            body: _t(
                "Le temps déjà mesuré sur chaque sujet sera perdu. Une rencontre ne se rejoue pas : ce geste sert à sortir d'un départ pressé par erreur."
            ),
            confirmLabel: _t("Effacer"),
            confirm: () => this.geste("action_timer_reset"),
            cancel: () => {},
        });
    }

    // ------------------------------------------------------------------
    // Affichage
    // ------------------------------------------------------------------
    get ecoule() {
        const p = this.state.payload;
        return p ? p.elapsed_seconds + this.derive : 0;
    }

    get ecart() {
        const p = this.state.payload;
        return p ? p.delta_seconds + this.derive : 0;
    }

    secondesDuSujet(ligne) {
        return ligne.is_current ? ligne.seconds + this.derive : ligne.seconds;
    }

    ecartDuSujet(ligne) {
        if (ligne.state === "pending" && !ligne.is_current) {
            return null;
        }
        return this.secondesDuSujet(ligne) - ligne.planned_minutes * 60;
    }

    /** « 12:34 », ou « 1:02:34 » au-delà de l'heure. */
    duree(secondes) {
        const s = Math.max(0, Math.round(secondes));
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const r = s % 60;
        const deux = (n) => String(n).padStart(2, "0");
        return h ? `${h}:${deux(m)}:${deux(r)}` : `${m}:${deux(r)}`;
    }

    /** « +4:12 » ou « -1:05 ». Le signe est l'information, pas la couleur seule. */
    signe(secondes) {
        if (secondes === null) {
            return "";
        }
        const s = Math.round(secondes);
        return (s >= 0 ? "+" : "-") + this.duree(Math.abs(s));
    }

    classeEcart(secondes) {
        if (secondes === null) {
            return "text-muted";
        }
        return secondes > 0 ? "text-danger fw-bold" : "text-success";
    }

    /** L'heure du jour, dans le fuseau de la personne. */
    heure(chaine) {
        if (!chaine) {
            return "";
        }
        return deserializeDateTime(chaine).toFormat("HH:mm");
    }

    /**
     * L'heure de fin projetée glisse avec le temps qui passe : le serveur l'a
     * calculée à `server_now`, on y ajoute la dérive.
     */
    get finProjetee() {
        const p = this.state.payload;
        if (!p || !p.projected_end) {
            return "";
        }
        const base = deserializeDateTime(p.projected_end);
        if (p.state !== "running") {
            return base.toFormat("HH:mm");
        }
        return base.plus({ seconds: this.derive }).toFormat("HH:mm");
    }

    /** Vrai quand la fin projetée dépasse la fin prévue. */
    get enRetard() {
        const p = this.state.payload;
        if (!p || !p.planned_end || !p.projected_end) {
            return false;
        }
        const prevue = deserializeDateTime(p.planned_end);
        const projetee = deserializeDateTime(p.projected_end).plus({ seconds: this.derive });
        return projetee > prevue;
    }

    get etiquetteEtat() {
        const p = this.state.payload;
        if (!p) {
            return "";
        }
        return {
            idle: _t("Non démarré"),
            running: _t("En cours"),
            paused: _t("En pause"),
            done: _t("Terminé"),
        }[p.state];
    }

    get lignes() {
        return this.state.payload ? this.state.payload.topics : [];
    }

    /** La ligne du sujet ouvert, celle dont le nom s'affiche sur le chronomètre. */
    get ligneCourante() {
        return this.lignes.find((l) => l.is_current) || null;
    }

    /** Un sujet qu'on peut ouvrir ou rouvrir d'un clic. */
    joignable(ligne) {
        const p = this.state.payload;
        if (!p || (p.state !== "running" && p.state !== "paused")) {
            return false;
        }
        return !ligne.is_current;
    }

    /**
     * Toute la ligne ramène au sujet, pas seulement le bouton : un lien
     * discret en fin de ligne ne se trouve pas.
     */
    ouvrirLigne(ligne) {
        if (!this.joignable(ligne)) {
            return;
        }
        return this.geste("action_timer_goto", [ligne.id]);
    }
}

export const meetingTimerPanel = {
    component: MeetingTimerPanel,
};

registry.category("view_widgets").add("bf_meeting_timer_panel", meetingTimerPanel);
