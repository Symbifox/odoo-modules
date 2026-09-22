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
 *
 * Et une règle d'usage, apprise en rencontre : **les raccourcis ne
 * prennent rien à Odoo.** Alt+S y sauvegarde la fiche et Alt+P ouvre la fiche
 * précédente ; les réclamer faisait avancer le chronomètre à chaque réflexe de
 * sauvegarde.
 */

import { Component, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { user } from "@web/core/user";
import { browser } from "@web/core/browser/browser";
import { entree, poserCurseur } from "@bf_meeting_timer/js/meeting_timer_store";

const MODELE = "meeting.agenda";

// Les gestes qui ouvrent un sujet : après eux, on amène la personne au sujet
// dans les notes en direct.
const GESTES_QUI_OUVRENT = new Set([
    "action_timer_start",
    "action_timer_split",
    "action_timer_skip",
    "action_timer_goto",
]);

/** Le texte d'un titre, comparable d'un nom de sujet saisi à l'autre. */
function normaliser(texte) {
    return (texte || "").replace(/\s+/g, " ").trim().toLowerCase();
}

export class MeetingTimerPanel extends Component {
    static template = "bf_meeting_timer.Panel";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.racine = useRef("racine");
        this.cleReduit = `bf_meeting_timer.reduit.${user.userId}`;
        this.state = useState({
            payload: null,
            busy: false,
            tick: 0,
            // Préférence d'affichage, pas une donnée : elle vit dans le
            // navigateur, jamais en base, jamais par le formulaire.
            reduit: browser.localStorage.getItem(this.cleReduit) === "1",
        });
        this.etatFiche = this.props.record.data.state;
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
            // Le bouton « Terminer » de l'en-tête arrête aussi le chronomètre,
            // côté serveur : quand l'état de la fiche change, on relit.
            const etat = this.props.record.data.state;
            if (etat !== this.etatFiche) {
                this.etatFiche = etat;
                this.charger();
            }
        }, 1000);
        onWillUnmount(() => clearInterval(this.battement));

        useHotkey("alt+shift+s", () => this.geste("action_timer_split"), { bypassEditableProtection: true });
        useHotkey("alt+shift+p", () => {
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
        if (this.resId) {
            entree(this.resId).payload = payload;
        }
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

    async geste(methode, args = [], { saut = true } = {}) {
        if (!this.resId || this.state.busy) {
            return;
        }
        this.state.busy = true;
        const avant = this.state.payload && this.state.payload.current_topic_id;
        try {
            const payload = await this.orm.call(MODELE, methode, [[this.resId], ...args]);
            this.poser(payload);
        } finally {
            this.state.busy = false;
        }
        const apres = this.state.payload && this.state.payload.current_topic_id;
        if (saut && GESTES_QUI_OUVRENT.has(methode) && apres && apres !== avant) {
            await this.allerAuxNotes();
        }
    }

    /**
     * Terminer au chronomètre termine aussi la rencontre : le serveur
     * passe l'ordre du jour à « Terminé ». La fiche doit donc être relue, et
     * sauvegardée d'abord, sinon la relecture emporterait les notes pas encore
     * enregistrées.
     */
    async terminer() {
        const record = this.props.record;
        if ((await record.isDirty()) && !(await record.save())) {
            return;
        }
        await this.geste("action_timer_stop");
        await record.load();
        this.etatFiche = record.data.state;
    }

    /**
     * « + Varia » : le serveur crée le sujet au besoin et l'ouvre. La
     * fiche est sauvegardée avant et relue après, sinon le nouveau sujet
     * n'existe pas pour les notes, et la relecture emporterait ce qui est tapé.
     */
    async varia() {
        const record = this.props.record;
        if ((await record.isDirty()) && !(await record.save())) {
            return;
        }
        await this.geste("action_timer_varia", [], { saut: false });
        await record.load();
        await this.allerAuxNotes();
    }

    basculerReduit() {
        this.state.reduit = !this.state.reduit;
        browser.localStorage.setItem(this.cleReduit, this.state.reduit ? "1" : "0");
    }

    // ------------------------------------------------------------------
    // Aller au sujet dans les notes en direct
    // ------------------------------------------------------------------
    /** Le champ des notes de l'ordre du jour, pas ceux de la liste des sujets. */
    trouverLesNotes(formulaire) {
        return [...formulaire.querySelectorAll('.o_field_widget[name="live_notes_html"]')].find(
            (champ) => !champ.closest(".o_field_x2many, .o_list_renderer")
        );
    }

    /**
     * Amener la personne à la fin du sujet ouvert, là où elle ajoute ses notes :
     * `action_start_meeting` pré-remplit les notes d'un titre par sujet, suivi
     * du contexte d'origine et d'un paragraphe vide.
     *
     * Ne fait rien, sans bruit, si le titre a été renommé ou supprimé : le saut
     * est une aide, pas une condition. Et n'écrit rien : la fiche reste propre.
     */
    async allerAuxNotes() {
        const ligne = this.ligneCourante;
        const racine = this.racine.el;
        const formulaire = racine && racine.closest(".o_form_view");
        if (!ligne || !formulaire) {
            return;
        }
        if (this.state.payload.notes_layout !== "flow") {
            return this.allerAuxNotesParSujet(formulaire, ligne);
        }
        let champ = this.trouverLesNotes(formulaire);
        if (!champ) {
            // Odoo ne construit une page d'onglet qu'au clic.
            const onglet = formulaire.querySelector('.o_notebook .nav-link[name="live_notes"]');
            if (!onglet) {
                return;
            }
            onglet.click();
            for (let essai = 0; essai < 20 && !champ; essai++) {
                await new Promise((resolve) => setTimeout(resolve, 50));
                champ = this.trouverLesNotes(formulaire);
            }
        }
        const editable = champ && champ.querySelector(".odoo-editor-editable");
        if (!editable) {
            return;
        }
        const nom = normaliser(ligne.name);
        const titres = [...editable.querySelectorAll("h1, h2, h3")];
        const titre = titres.find((t) => normaliser(t.textContent) === nom);
        if (!titre) {
            return;
        }
        // La fin de la section : le dernier bloc avant le titre suivant.
        let bloc = titre;
        let suivant = titre.nextElementSibling;
        while (suivant && !/^H[1-3]$/.test(suivant.tagName)) {
            bloc = suivant;
            suivant = suivant.nextElementSibling;
        }
        await poserCurseur(editable, bloc);
    }

    /**
     * Notes par sujet : ouvrir l'onglet au besoin, puis demander aux notes
     * d'afficher le sujet et d'y poser le curseur. Ce sont elles qui le font :
     * l'éditeur est à elles.
     */
    async allerAuxNotesParSujet(formulaire, ligne) {
        const trouver = () => formulaire.querySelector(".o_bf_meeting_timer_notes");
        if (!trouver()) {
            const onglet = formulaire.querySelector('.o_notebook .nav-link[name="live_notes"]');
            if (!onglet) {
                return;
            }
            onglet.click();
            for (let essai = 0; essai < 20 && !trouver(); essai++) {
                await new Promise((resolve) => setTimeout(resolve, 50));
            }
        }
        const e = entree(this.resId);
        e.sautVers = ligne.id;
        e.saut++;
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

    /** Le sujet ouvert a-t-il mangé tout son alloué? */
    depasse(ligne) {
        return Boolean(
            ligne && ligne.planned_minutes && this.secondesDuSujet(ligne) > ligne.planned_minutes * 60
        );
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
