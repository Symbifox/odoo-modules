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
// - Surlignage des changements depuis le dernier rafraîchissement,
//   avec son compte à côté du ⟳ : en mémoire seulement, il meurt avec la page.

import { browser } from "@web/core/browser/browser";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { serializeDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { useBus, useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { CalendarController } from "@web/views/calendar/calendar_controller";
import { CalendarModel } from "@web/views/calendar/calendar_model";
import { CalendarCommonRenderer } from "@web/views/calendar/calendar_common/calendar_common_renderer";
import { CalendarCommonPopover } from "@web/views/calendar/calendar_common/calendar_common_popover";
import { CalendarYearRenderer } from "@web/views/calendar/calendar_year/calendar_year_renderer";
import { session } from "@web/session";
import { user } from "@web/core/user";

import { Component, onMounted, onPatched, reactive, useExternalListener, useState } from "@odoo/owl";

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
// Surlignage des changements depuis le dernier rafraîchissement
// ---------------------------------------------------------------------------

const CLASSE_MARQUE = "o_bf_event_changed";
const CLASSE_PULSE = "o_bf_event_pulse";
const FORMES = ["ring", "pulse", "none"];
const EFFACEMENTS = ["refresh", "stay", "click"];

// Préférences de l'usager, servies par la session (models/ir_http.py). Le menu
// du ⟳ et l'écran des préférences écrivent les mêmes deux champs de
// `res.users` ; cet objet est l'exemplaire que les vues lisent en vivant.
const prefs = reactive({
    style: FORMES.includes(session.bf_calendar_highlight) ? session.bf_calendar_highlight : "ring",
    clear: EFFACEMENTS.includes(session.bf_calendar_highlight_clear)
        ? session.bf_calendar_highlight_clear
        : "refresh",
});

/** Clé de la fenêtre affichée : modèle, échelle, bornes, domaine et filtres
 * STABLES cochés.
 *
 * 🔴 Pourquoi elle est indispensable, et pourquoi elle a failli disparaître :
 * `load()` pose `this.meta` (date, échelle, filtres) tout de suite, mais
 * n'affecte `this.data` qu'APRÈS son `await`, et `KeepLast` ne résout jamais un
 * chargement dépassé. Un changement de semaine encore en vol, puis un ⟳ :
 * `meta` est déjà sur la semaine neuve, la référence est restée sur l'ancienne,
 * et sans cette clé toute la grille serait marquée.
 *
 * 🔴 Pourquoi les filtres DYNAMIQUES n'y sont pas : le cœur les reconstruit à
 * partir des fiches chargées (`loadDynamicFilterSection`) et une valeur jamais
 * vue arrive cochée. Les compter, c'est voir la fenêtre changer parce qu'une
 * DONNÉE a changé — un événement neuf dans un agenda ou un projet encore absent
 * de l'écran n'était alors pas marqué du tout. */
function cleDeLaFenetre(modele, data) {
    const bornes = data.range
        ? `${data.range.start.valueOf()}-${data.range.end.valueOf()}`
        : "";
    const filtres = [];
    for (const [nom, section] of Object.entries(data.filterSections || {})) {
        const stables = (section.filters || []).filter((f) => f.type !== "dynamic");
        if (!stables.length) {
            continue;
        }
        filtres.push([
            nom,
            stables
                .filter((f) => f.active)
                .map((f) => String(f.value))
                .sort(),
        ]);
    }
    filtres.sort();
    return JSON.stringify([
        modele.meta.resModel,
        modele.meta.scale,
        bornes,
        modele.meta.domain || [],
        filtres,
    ]);
}

/** Empreinte courte d'une chaîne (cyrb53).
 *
 * Garder la chaîne entière coûterait, sur une base dont les rencontres
 * portent de longues descriptions HTML, des dizaines de méga-octets par
 * chargement (le `rawRecord` complet, deux jeux à la fois). Une collision ferait
 * rater une marque, jamais un faux positif, et elle est de l'ordre de 1 sur
 * 9 000 000 000 000 000. */
function empreinte(texte) {
    let h1 = 0xdeadbeef;
    let h2 = 0x41c6ce57;
    for (let i = 0; i < texte.length; i++) {
        const c = texte.charCodeAt(i);
        h1 = Math.imul(h1 ^ c, 2654435761);
        h2 = Math.imul(h2 ^ c, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return 4294967296 * (2097151 & h2) + (h1 >>> 0);
}

// Champs dont la valeur suivrait l'HORLOGE et non la donnée : « En retard »
// bascule à minuit sans que personne n'ait touché à la fiche, et le premier
// rafraîchissement après minuit marquerait tout ce qui porte une activité.
// Aucune vue calendrier connue ne déclare celui-ci : la garde est là pour
// l'arch qui l'ajouterait.
const CHAMPS_VOLATILES = new Set(["activity_state"]);

/** Tout ce que la vue lit de l'événement (`rawRecord`) : un titre, un invité,
 * un lieu ou une couleur qui changent comptent autant qu'un déplacement, et
 * rien n'est demandé au serveur pour le savoir. */
function signatureFiche(fiche) {
    const brut = fiche.rawRecord || {};
    return empreinte(
        JSON.stringify(
            Object.keys(brut)
                .filter((c) => !CHAMPS_VOLATILES.has(c))
                .sort()
                .map((c) => [c, brut[c]])
        )
    );
}

/** L'id de la FICHE derrière un événement de la grille.
 *
 * 🔴 L'Agenda (`AttendeeCalendarModel`) refait `data.records` APRÈS nous, une
 * entrée par participant coché, et indexe les copies sur des id NÉGATIFS
 * (`_recordId`) que le rendu donne à FullCalendar. Chercher la marque sous cet
 * id-là ne trouverait rien : un seul des deux blocs d'une rencontre partagée
 * serait surligné. */
function ficheDeLEvenement(modele, id) {
    const fiche = modele.data?.records?.[id];
    return String(fiche?.id ?? id);
}

/** Les classes à ajouter à un événement marqué, selon la forme choisie. */
function classesDeMarque(modele, id) {
    if (prefs.style === "none") {
        return [];
    }
    const marques = modele.data?.bfChanges;
    const vrai = ficheDeLEvenement(modele, id);
    if (!marques || !(vrai in marques)) {
        return [];
    }
    // La pulsation ne vaut que pour les marques de CE tour : sinon, en mode
    // « cumul », tout le paquet repulse à chaque redessin de la grille.
    const pulse = prefs.style === "pulse" && modele.data?.bfPulse?.[vrai];
    return pulse ? [CLASSE_MARQUE, CLASSE_PULSE] : [CLASSE_MARQUE];
}

/** Le compte des changements, sur le ⟳.
 *
 * 🔴 Un composant FEUILLE, et c'est tout le point : si le contrôleur lisait le
 * compte, en changer redessinerait son sous-arbre, donc la grille
 * (`refetchEvents` de FullCalendar refait les éléments) — et la bulle qui vient
 * de s'ouvrir se refermerait aussitôt dans le mode « au clic ». Ici, seul ce
 * `<span>` est redessiné. */
class CompteurChangements extends Component {
    // Gabarit dans le fichier XML, pas en `xml\`` ici : c'est de là que
    // l'extraction des traductions lit un titre.
    static template = "web_calendar_ergonomics.CompteurChangements";
    // `pastille` : le chiffre visible, dans le bouton. `annonce` : la région
    // vivante, hors du bouton et TOUJOURS rendue — une région insérée en même
    // temps que son texte n'est annoncée par aucun lecteur d'écran, et le
    // `aria-label` du bouton masque ce qu'il contient.
    static props = { etat: Object, mode: { type: String, optional: true } };

    setup() {
        this.etat = useState(this.props.etat);
        this.prefs = useState(prefs);
    }

    /** Sans pluriel : Odoo n'en gère pas dans `_t`, et « Changements : 1 » se
     * lit aussi bien que « 1 changement ». */
    get etiquette() {
        return _t("Changes since the last refresh: %s", this.etat.count);
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
        this.bfOrm = useService("orm");
        this.bfPrefs = useState(prefs);
        // 🔴 Portés par l'INSTANCE, et le gabarit les pose par `t-component` :
        // les sous-classes du contrôleur (`AttendeeCalendarController`, pour
        // l'Agenda) recopient `components` à leur définition, donc un composant
        // ajouté après coup à la classe mère n'y est jamais. Le cœur d'Odoo fait
        // pareil pour sa barre de recherche (`searchBarToggler.component`).
        this.bfDropdown = Dropdown;
        this.bfDropdownItem = DropdownItem;
        this.bfCompteur = CompteurChangements;
        // Le ⟳ se rallume quand la grille a reçu des données, d'où qu'elles
        // viennent : le modèle ne garde que le DERNIER chargement (KeepLast) et
        // ne résout jamais la promesse d'un chargement doublé (« Suivant »
        // juste après ⟳, retour sur la fenêtre). Attendre `model.load()`
        // laissait le bouton éteint à vie.
        useBus(this.model.bus, "update", () => this.bfEndReload());

        // Le panneau : la valeur de l'onglet si l'onglet en a une (Odoo), sinon
        // la dernière choisie, tous onglets confondus.
        if (!this.env.isSmall) {
            let onglet = null;
            try {
                onglet = browser.sessionStorage.getItem("calendar.showSideBar");
            } catch {
                onglet = null;
            }
            const memorise = lireLocal(CLE_PANNEAU);
            if (onglet == null && memorise != null) {
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
        // Seuls le ⟳ (Alt+R compris) et le retour sur l'onglet surlignent :
        // un enregistrement, un changement de date ou de filtre rechargent
        // sans marquer (mes propres gestes ne se surlignent pas).
        this.model.bfMarquerCeChargement = true;
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
            // Le drapeau serait resté armé pour le chargement suivant (un
            // changement de date), qui n'a pas à surligner.
            this.model.bfMarquerCeChargement = false;
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

    /** Écrit une préférence de surlignage : la grille suit tout de suite, et la
     * valeur part au serveur pour suivre l'usager d'un appareil à l'autre. */
    bfSetPref(quoi, valeur) {
        if (prefs[quoi] === valeur) {
            return;
        }
        prefs[quoi] = valeur;
        if (quoi === "style" && this.model.data) {
            // Les marques du dernier tour ne repulsent pas parce qu'on vient de
            // choisir la pulsation : elle ne vaut que pour un tour en cours.
            this.model.data.bfPulse = {};
        }
        const champ = quoi === "style" ? "bf_calendar_highlight" : "bf_calendar_highlight_clear";
        // Sans attendre : un refus du serveur ne doit pas casser le menu.
        this.bfOrm
            .write("res.users", [user.userId], { [champ]: valeur })
            .catch(() => {});
        // Pas de `model.notify()` ici : le contrôleur lit déjà `prefs` (useState),
        // donc muter la préférence redessine sa vue — grille comprise. Le bus du
        // modèle, lui, rallumerait le ⟳ au milieu d'un chargement en cours.
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
    setup() {
        super.setup(...arguments);
        // Compte des marques, lu par le bouton ⟳. Réactif et séparé des
        // données : il se met à jour sans redessiner la grille.
        this.bfEtat = reactive({ count: 0 });
    },

    async load() {
        await super.load(...arguments);
        // `this.data` n'est affecté qu'au chargement qui GAGNE (KeepLast) : le
        // compte se lit ici, jamais depuis `updateData`.
        this.bfEtat.count = this.data.bfChangeCount || 0;
    },

    async updateData(data) {
        const marquer = this.bfMarquerCeChargement;
        this.bfMarquerCeChargement = false;
        // `load()` fait `data = { ...this.data }` et n'affecte `this.data`
        // qu'après : ici, `this.data` est encore le jeu AFFICHÉ.
        const affiche = this.data;
        // Lancée avant les enregistrements, attendue après : pas de délai ajouté.
        const heures = this.bfFetchWorkHours();
        await super.updateData(...arguments);
        data.bfWorkHours = await heures;
        this.bfComparer(data, affiche, marquer);
    },

    /** Marque, sur le jeu `data`, ce qui a changé depuis le jeu affiché. Les
     * marques vivent sur `data` : un chargement dépassé par un autre n'est
     * jamais affecté à `this.data`, donc il ne marque rien. */
    bfComparer(data, affiche, marquer) {
        data.bfChanges = {};
        data.bfChangeCount = 0;
        data.bfPulse = {};
        if (prefs.style === "none" || this.useSampleModel) {
            // Rien à signer : on ne paiera pas une empreinte par fiche pour un
            // surlignage éteint.
            //
            // 🔴 `null`, et surtout pas `{}` : une référence VIDE se lit comme
            // une fenêtre sans événement, et le premier ⟳ après le rallumage
            // marquerait toute la grille. `null` dit « pas de référence », et
            // la branche qui marque s'abstient.
            data.bfRecordSigs = null;
            data.bfFenetre = null;
            return;
        }
        const signatures = {};
        for (const [id, fiche] of Object.entries(data.records)) {
            signatures[id] = signatureFiche(fiche);
        }
        data.bfRecordSigs = signatures;
        data.bfFenetre = cleDeLaFenetre(this, data);
        if (!marquer) {
            // La référence repart de ce qu'on vient de lire, sans rien marquer.
            //
            // Il n'y a pas d'autre garde à mettre ici : TOUT ce qui change la
            // fenêtre affichée passe par un chargement sans drapeau —
            // `setDate`, `setScale`, `createFilter`, `unlinkFilter`,
            // `updateFilters` et la recherche finissent tous par `load()` —,
            // donc la référence est déjà celle de la fenêtre en cours quand un
            // ⟳ arrive.
            return;
        }
        // Sans référence (surlignage rallumé), ou référence d'une AUTRE fenêtre
        // (un chargement de période encore en vol quand le ⟳ est parti) : on ne
        // marque pas, on se contente de reprendre la référence.
        if (!affiche.bfRecordSigs || affiche.bfFenetre !== data.bfFenetre) {
            return;
        }
        const avant = affiche.bfRecordSigs;
        // « Garder jusqu'au départ » : les marques des rafraîchissements
        // précédents restent, sauf celles d'événements qui ne sont plus là (le
        // compte ne promet jamais une marque invisible).
        const marques = prefs.clear === "stay" ? { ...(affiche.bfChanges || {}) } : {};
        for (const id of Object.keys(marques)) {
            if (!(id in signatures)) {
                delete marques[id];
            }
        }
        const duTour = {};
        for (const [id, signature] of Object.entries(signatures)) {
            if (!(id in avant)) {
                marques[id] = "new";
                duTour[id] = true;
            } else if (avant[id] !== signature) {
                marques[id] = "changed";
                duTour[id] = true;
            }
        }
        data.bfChanges = marques;
        data.bfChangeCount = Object.keys(marques).length;
        data.bfPulse = duTour;
    },

    /** « Au clic » : la marque d'un événement s'en va sans redessiner la grille
     * (les classes sont retirées des éléments déjà posés, la bulle qui vient de
     * s'ouvrir reste ouverte). */
    bfOublierMarque(id, racine) {
        const marques = this.data.bfChanges;
        const vrai = ficheDeLEvenement(this, id);
        if (!marques || !(vrai in marques)) {
            return;
        }
        delete marques[vrai];
        this.data.bfChangeCount = Object.keys(marques).length;
        this.bfEtat.count = this.data.bfChangeCount;
        // Comparé au `dataset` plutôt que visé par un sélecteur : l'id n'a pas
        // à être échappé, un événement rendu en plusieurs morceaux (sur deux
        // jours) perd sa marque partout, et les copies par participant de
        // l'Agenda aussi, qui portent chacune leur propre id d'affichage.
        for (const el of racine ? racine.querySelectorAll("[data-event-id]") : []) {
            if (ficheDeLEvenement(this, el.dataset.eventId) === vrai) {
                el.classList.remove(CLASSE_MARQUE, CLASSE_PULSE);
            }
        }
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
    eventClassNames() {
        return super.eventClassNames(...arguments).concat(
            classesDeMarque(this.props.model, arguments[0].event.id)
        );
    },

    onEventClick(info) {
        super.onEventClick(...arguments);
        if (prefs.clear === "click") {
            this.props.model.bfOublierMarque(info.event.id, this.fc.el);
        }
    },

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
// Rendu de l'année : la même marque. Pas de clic qui efface : l'année n'ouvre
// pas de bulle d'événement (seul le jour est cliquable).
// ---------------------------------------------------------------------------

patch(CalendarYearRenderer.prototype, {
    eventClassNames() {
        return super.eventClassNames(...arguments).concat(
            classesDeMarque(this.props.model, arguments[0].event.id)
        );
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
