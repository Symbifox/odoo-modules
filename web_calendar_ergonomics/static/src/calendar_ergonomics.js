/** @odoo-module **/
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
//
// Ergonomie de la vue calendrier, sur la vue GÉNÉRIQUE : l'Agenda
// et toutes les autres vues calendrier (tâches, rendez-vous, congés...).
//
// - ⟳ Actualiser, et rechargement quand on revient sur l'onglet ou la fenêtre :
//   rien n'avertit l'écran quand la synchro Nextcloud, Google ou Gen change un
//   événement.
// - Raccourcis : Alt+PageUp / Alt+PageDown (période précédente, suivante),
//   Alt+↑ / Alt+↓ (échelle plus large, plus fine), Alt+T (aujourd'hui),
//   Alt+R (actualiser). Pas Alt+← / Alt+→ (page précédente du navigateur), pas
//   Alt+chiffre (changement d'onglet sous Linux), pas Alt+N (bloc-notes).
// - Panneau latéral mémorisé d'un onglet à l'autre (Odoo le garde en
//   sessionStorage, le temps de l'onglet seulement).
// - Heures de travail ombrées (jour et semaine).
// - « Dupliquer » dans la bulle d'un événement.

import { browser } from "@web/core/browser/browser";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { serializeDateTime } from "@web/core/l10n/dates";
import { useBus, useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { CalendarController } from "@web/views/calendar/calendar_controller";
import { CalendarModel } from "@web/views/calendar/calendar_model";
import { CalendarCommonRenderer } from "@web/views/calendar/calendar_common/calendar_common_renderer";
import { CalendarCommonPopover } from "@web/views/calendar/calendar_common/calendar_common_popover";

import { onMounted, onPatched, useExternalListener, useState } from "@odoo/owl";

const CLE_PANNEAU = "web_calendar_ergonomics.showSideBar";
// Écart minimal entre deux rechargements automatiques : revenir sur la
// fenêtre déclenche souvent `focus` ET `visibilitychange`.
const ECART_RECHARGEMENT_MS = 3000;
const ECHELLES = ["day", "week", "month", "year"];
// Filet du ⟳ : au-delà, le bouton redevient actif même si aucun chargement
// n'a abouti (chargement doublé par un autre qui a échoué).
const FILET_CHARGEMENT_MS = 20000;
// Où « Dupliquer » ne peut qu'échouer (une activité naît de sa fiche, un congé
// de son circuit d'approbation) : le serveur le refuse aussi.
const MODELES_SANS_DUPLIQUER = ["mail.activity", "hr.leave"];

function lireLocal(cle) {
    try {
        return browser.localStorage.getItem(cle);
    } catch {
        return null;
    }
}

function ecrireLocal(cle, valeur) {
    try {
        browser.localStorage.setItem(cle, valeur);
    } catch {
        // Navigation privée, stockage bloqué : le panneau n'est pas mémorisé.
    }
}

// ---------------------------------------------------------------------------
// Contrôleur : Actualiser, retour sur l'onglet, raccourcis, panneau mémorisé
// ---------------------------------------------------------------------------

patch(CalendarController.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfRefresh = useState({ loading: false });
        this.bfLastAutoLoad = Date.now();
        // Le ⟳ se rallume quand la grille a reçu des données, d'où qu'elles
        // viennent : le modèle ne garde que le DERNIER chargement (KeepLast) et
        // ne résout jamais la promesse d'un chargement doublé (« Suivant »
        // juste après ⟳, retour sur la fenêtre). Attendre `model.load()`
        // laissait le bouton éteint à vie.
        useBus(this.model.bus, "update", () => this.bfEndReload());

        // Le panneau : la valeur de l'onglet si l'onglet en a une (Odoo), sinon
        // la dernière choisie, tous onglets confondus.
        if (!this.env.isSmall) {
            let session = null;
            try {
                session = browser.sessionStorage.getItem("calendar.showSideBar");
            } catch {
                session = null;
            }
            const memorise = lireLocal(CLE_PANNEAU);
            if (session == null && memorise != null) {
                this.state.showSideBar = memorise === "true";
            }
        }

        const auRetour = () => this.bfReloadOnReturn();
        useExternalListener(document, "visibilitychange", auRetour);
        useExternalListener(window, "focus", auRetour);

        // Odoo met le curseur dans la recherche à l'ouverture de la vue : sans
        // `bypassEditableProtection`, aucun de ces raccourcis ne marchait avant
        // un premier clic dans la grille. Les boutons à `data-hotkey`
        // (Alt+T, Alt+R) passent déjà outre, comme partout dans Odoo.
        const partout = { bypassEditableProtection: true };
        useHotkey("alt+pageup", () => this.setDate("previous"), partout);
        useHotkey("alt+pagedown", () => this.setDate("next"), partout);
        useHotkey("alt+arrowup", () => this.bfStepScale(1), partout);
        useHotkey("alt+arrowdown", () => this.bfStepScale(-1), partout);
    },

    toggleSideBar() {
        super.toggleSideBar(...arguments);
        ecrireLocal(CLE_PANNEAU, String(Boolean(this.state.showSideBar)));
    },

    bfReload() {
        if (this.bfRefresh.loading || this.model.useSampleModel) {
            return;
        }
        this.bfRefresh.loading = true;
        this.bfLastAutoLoad = Date.now();
        const tour = (this.bfReloadTurn = (this.bfReloadTurn || 0) + 1);
        browser.setTimeout(() => {
            if (this.bfReloadTurn === tour) {
                this.bfEndReload();
            }
        }, FILET_CHARGEMENT_MS);
        // Rien n'attend cette promesse : elle peut ne jamais se résoudre (voir
        // plus haut). Une erreur rallume le bouton et passe au gestionnaire
        // d'erreurs d'Odoo.
        this.model.load().catch((erreur) => {
            this.bfEndReload();
            throw erreur;
        });
    },

    bfEndReload() {
        this.bfReloadTurn = (this.bfReloadTurn || 0) + 1;
        this.bfLastAutoLoad = Date.now();
        if (this.bfRefresh.loading) {
            this.bfRefresh.loading = false;
        }
    },

    bfReloadOnReturn() {
        if (document.visibilityState !== "visible") {
            return;
        }
        if (Date.now() - this.bfLastAutoLoad < ECART_RECHARGEMENT_MS) {
            return;
        }
        this.bfLastAutoLoad = Date.now();
        this.bfReload();
    },

    bfStepScale(pas) {
        const offertes = ECHELLES.filter((s) => this.model.scales.includes(s));
        const ici = offertes.indexOf(this.model.scale);
        const cible = offertes[ici + pas];
        if (ici >= 0 && cible) {
            this.setScale(cible);
        }
    },
});

// ---------------------------------------------------------------------------
// Modèle : heures de travail de la plage affichée
// ---------------------------------------------------------------------------

patch(CalendarModel.prototype, {
    async updateData(data) {
        // Lancée avant les enregistrements, attendue après : pas de délai ajouté.
        const heures = this.bfFetchWorkHours();
        await super.updateData(...arguments);
        data.bfWorkHours = await heures;
    },

    async bfFetchWorkHours() {
        // L'Agenda a déjà l'ombrage d'Odoo quand hr_calendar est installé
        // (horaire commun des participants cochés) : on ne s'y substitue pas.
        // `undefined` = la grille n'est pas à nous, on n'y touche pas.
        if (this.data && "workingHours" in this.data) {
            return undefined;
        }
        if (this.useSampleModel || !["day", "week"].includes(this.meta.scale)) {
            return false;
        }
        try {
            const { start, end } = this.computeRange();
            return await this.orm.silent.call("web.calendar.ergonomics", "work_hours", [
                serializeDateTime(start),
                serializeDateTime(end),
                luxon.Settings.defaultZone.name,
            ]);
        } catch {
            // Module pas encore monté en base, réseau : la grille sans ombre.
            return false;
        }
    },
});

// ---------------------------------------------------------------------------
// Grille : ombrer ce qui tombe hors des heures de travail
// ---------------------------------------------------------------------------

patch(CalendarCommonRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        // FullCalendar ne relit pas ses options au rechargement (le crochet
        // d'Odoo ne fait que `refetchEvents`) : on repose l'horaire après un
        // rendu, seulement s'il a changé (un `setOption` redessine la grille).
        onMounted(() => {
            this.bfPosees = JSON.stringify(this.bfBusinessHours);
            this.bfMarquerLaGrille();
        });
        onPatched(() => {
            const heures = this.bfBusinessHours;
            const cle = JSON.stringify(heures);
            if (heures !== undefined && cle !== this.bfPosees) {
                this.fc.api?.setOption("businessHours", heures);
                this.bfPosees = cle;
            }
            this.bfMarquerLaGrille();
        });
    },

    /** La teinte de notre ombre (SCSS) vise cette classe : posée seulement quand
     * l'ombrage est le nôtre, jamais sur celui de hr_calendar (Agenda). */
    bfMarquerLaGrille() {
        this.fc.el?.classList.toggle("o_bf_work_hours", this.bfBusinessHours !== undefined);
    },

    get options() {
        const options = super.options;
        const heures = this.bfBusinessHours;
        return heures === undefined ? options : { ...options, businessHours: heures };
    },

    /** undefined : pas à nous (hr_calendar) ; false : rien à ombrer ; sinon les règles. */
    get bfBusinessHours() {
        const heures = this.props.model.data?.bfWorkHours;
        if (heures === undefined) {
            return undefined;
        }
        if (!Array.isArray(heures)) {
            return false;
        }
        // Liste vide = l'horaire ne travaille pas ici (jour férié, fin de
        // semaine) : tout est hors des heures. FullCalendar lit une liste vide
        // comme « pas d'horaire », d'où une règle sur un jour qui n'existe pas
        // (7), comme le fait hr_calendar.
        return heures.length ? heures : [{ daysOfWeek: [7], startTime: "00:00", endTime: "00:00" }];
    },
});

// ---------------------------------------------------------------------------
// Bulle : Dupliquer
// ---------------------------------------------------------------------------

patch(CalendarCommonPopover.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfAction = useService("action");
        this.bfOrm = useService("orm");
    },

    get bfRecordId() {
        const id = this.props.record.rawRecord?.id ?? this.props.record.id;
        return Number.isInteger(id) ? id : false;
    },

    get bfCanDuplicate() {
        return Boolean(
            this.bfRecordId &&
                !MODELES_SANS_DUPLIQUER.includes(this.props.model.resModel) &&
                this.props.model.canCreate &&
                this.isEventEditable &&
                !this.props.model.useSampleModel
        );
    },

    async onBfDuplicate() {
        const { resModel, formViewId } = this.props.model;
        const defauts = await this.bfOrm.call("web.calendar.ergonomics", "duplicate_defaults", [
            resModel,
            this.bfRecordId,
        ]);
        const context = {};
        for (const [nom, valeur] of Object.entries(defauts)) {
            context[`default_${nom}`] = valeur;
        }
        this.props.close();
        await this.bfAction.doAction({
            type: "ir.actions.act_window",
            res_model: resModel,
            views: [[formViewId || false, "form"]],
            target: "current",
            context,
        });
    },
});
