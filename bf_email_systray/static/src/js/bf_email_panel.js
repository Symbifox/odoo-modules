/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { useBus, useService } from "@web/core/utils/hooks";
import { BfEmailInbox } from "@bf_email_management/js/bf_email_inbox";

// Taille retenue par navigateur et par personne, au-dessus du defaut de la base.
const STORAGE_KEY = "bf_email_systray_panel";
const MIN_PCT = 40;
const MAX_PCT = 100;
// Hauteur de la barre de navigation d'Odoo : le panneau se pose dessous.
const NAVBAR_PX = 46;
const GAP_PX = 8;

function clampPct(value, fallback) {
    const n = Math.round(Number(value));
    if (!Number.isFinite(n)) {
        return fallback;
    }
    return Math.min(MAX_PCT, Math.max(MIN_PCT, n));
}

/**
 * La boite de reception en panneau embarque, ancre au coin superieur droit,
 * sous le bouton qui l'ouvre.
 *
 * Monte par le service `overlay` en sequence 40, sous celle des boites de
 * dialogue (50) : le conteneur trie par sequence et tous ses elements
 * partagent le meme z-index, donc les assistants que la boite ouvre
 * elle-meme (re-routage, composeur, planification) passent forcement
 * PAR-DESSUS le panneau.
 *
 * Le contenu est `BfEmailInbox`, le composant que sert deja l'action cliente
 * `bf_email_management.action_bf_email_inbox_owl` : rien n'est duplique, le
 * panneau n'est qu'un cadre.
 */
export class BfEmailPanel extends Component {
    static template = "bf_email_systray.Panel";
    static components = { BfEmailInbox };
    static props = {
        close: Function,
        defaultWidthPct: Number,
        defaultHeightPct: Number,
    };

    setup() {
        this.action = useService("action");
        this.state = useState(this._storedSize());
        this.drag = null;

        // Echap ferme le panneau, mais seulement quand aucune dialogue n'est
        // ouverte : le service de raccourcis ignore les enregistrements hors
        // de l'element actif de l'interface, et une dialogue prend cette
        // place tant qu'elle vit. Echap ferme donc la dialogue d'abord.
        useHotkey("escape", () => this.props.close());

        // La boite navigue : « Ouvrir la fiche rattachee », « Ouvrir dans le
        // chatter » et la creation d'une tache rendent une action
        // `target: "current"`. Odoo ferme alors toutes les DIALOGUES, mais le
        // panneau vit dans le service `overlay` : sans ce qui suit il
        // resterait pose sur une page qui a change dessous.
        //
        // `ACTION_MANAGER:UPDATE` ne part que sur ce chemin-la : la branche
        // `target: "new"` retourne avant de l'emettre. Une dialogue ne ferme
        // donc pas le panneau, une vraie navigation oui.
        useBus(this.env.bus, "ACTION_MANAGER:UPDATE", () => this.props.close());
    }

    _storedSize() {
        const size = {
            widthPct: clampPct(this.props.defaultWidthPct, 85),
            heightPct: clampPct(this.props.defaultHeightPct, 85),
        };
        try {
            const saved = JSON.parse(browser.localStorage.getItem(STORAGE_KEY) || "{}");
            if (saved.widthPct) {
                size.widthPct = clampPct(saved.widthPct, size.widthPct);
            }
            if (saved.heightPct) {
                size.heightPct = clampPct(saved.heightPct, size.heightPct);
            }
        } catch {
            // preference illisible : on garde celle de la configuration
        }
        return size;
    }

    _persistSize() {
        try {
            browser.localStorage.setItem(
                STORAGE_KEY,
                JSON.stringify({
                    widthPct: this.state.widthPct,
                    heightPct: this.state.heightPct,
                })
            );
        } catch {
            // navigation privee, quota plein : la taille vaut pour la session
        }
    }

    /**
     * Les `min()` gardent le panneau dans la fenetre meme a 100 % : la barre
     * de navigation et la marge sont retranchees dans le calcul, pas dans
     * l'etat, pour que la preference reste exprimee en pourcentage.
     */
    get panelStyle() {
        const w = `min(${this.state.widthPct}vw, calc(100vw - ${2 * GAP_PX}px))`;
        const h = `min(${this.state.heightPct}vh, calc(100vh - ${NAVBAR_PX + GAP_PX}px))`;
        return `top:${NAVBAR_PX}px;right:${GAP_PX}px;width:${w};height:${h};`;
    }

    get sizeLabel() {
        return `${this.state.widthPct} × ${this.state.heightPct} %`;
    }

    get isDefaultSize() {
        return (
            this.state.widthPct === clampPct(this.props.defaultWidthPct, 85) &&
            this.state.heightPct === clampPct(this.props.defaultHeightPct, 85)
        );
    }

    /**
     * Sortie vers la pleine page quand le panneau devient trop petit pour ce
     * qu'on fait. `doAction` emet `ACTION_MANAGER:UPDATE`, donc l'ecouteur
     * ci-dessus ferme deja le panneau ; l'appel explicite reste pour que la
     * fermeture ne depende pas d'un evenement.
     */
    openFullPage() {
        this.action.doAction("bf_email_management.action_bf_email_inbox_owl");
        this.props.close();
    }

    resetSize() {
        this.state.widthPct = clampPct(this.props.defaultWidthPct, 85);
        this.state.heightPct = clampPct(this.props.defaultHeightPct, 85);
        try {
            browser.localStorage.removeItem(STORAGE_KEY);
        } catch {
            // sans stockage, la remise a zero ne vaut que pour cette ouverture
        }
    }

    // ----------------------------------------------------------------
    // Poignee du coin inferieur GAUCHE : le panneau est ancre en haut a
    // droite, il grandit donc vers la gauche et vers le bas.
    // ----------------------------------------------------------------
    startResize(ev) {
        const panel = ev.currentTarget.closest(".o_bf_email_panel");
        if (!panel) {
            return;
        }
        ev.preventDefault();
        const rect = panel.getBoundingClientRect();
        this.drag = { x: ev.clientX, y: ev.clientY, w: rect.width, h: rect.height };
        // La capture garde les evenements meme si le pointeur sort du
        // panneau, et evite d'ecouter la fenetre entiere.
        ev.currentTarget.setPointerCapture(ev.pointerId);
    }

    onResizeMove(ev) {
        if (!this.drag) {
            return;
        }
        const width = this.drag.w + (this.drag.x - ev.clientX);
        const height = this.drag.h + (ev.clientY - this.drag.y);
        this.state.widthPct = clampPct(
            (width / window.innerWidth) * 100, this.state.widthPct);
        this.state.heightPct = clampPct(
            (height / window.innerHeight) * 100, this.state.heightPct);
    }

    stopResize(ev) {
        if (!this.drag) {
            return;
        }
        this.drag = null;
        if (ev.currentTarget.hasPointerCapture?.(ev.pointerId)) {
            ev.currentTarget.releasePointerCapture(ev.pointerId);
        }
        this._persistSize();
    }
}
