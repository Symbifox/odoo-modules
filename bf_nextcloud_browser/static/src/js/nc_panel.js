/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { useService } from "@web/core/utils/hooks";
import { NcBrowserApp } from "@bf_nextcloud_browser/js/nc_browser";

// Preference de taille, par navigateur et par personne.
const STORAGE_KEY = "bf_nc_browser_panel";
// En deca de 40 % le volet des dossiers et la liste ne tiennent plus ensemble.
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
 * Le navigateur Nextcloud en panneau embarque, ancre au coin superieur droit,
 * sous la barre systeme d'ou on l'ouvre.
 *
 * Il est monte par le service `overlay` avec une sequence INFERIEURE a celle
 * des boites de dialogue (50) : le conteneur d'overlay trie par sequence et
 * tous ses elements partagent le meme z-index, donc c'est l'ordre du DOM qui
 * decide de l'empilement. A 40, l'apercu, le partage et le renommage
 * s'ouvrent forcement PAR-DESSUS le panneau, jamais dessous.
 *
 * Le contenu est le composant autonome `NcBrowserApp`, celui-la meme que sert
 * l'action client : rien n'est duplique, le panneau n'est qu'un cadre.
 */
export class NcBrowserPanel extends Component {
    static template = "bf_nextcloud_browser.NcBrowserPanel";
    static components = { NcBrowserApp };
    static props = {
        close: Function,
        defaultWidthPct: Number,
        defaultHeightPct: Number,
    };

    setup() {
        this.action = useService("action");
        this.state = useState(this._storedSize());
        this.drag = null;
        // Echap ferme le panneau — mais seulement quand aucune boite de
        // dialogue n'est ouverte : le service de raccourcis ignore les
        // enregistrements hors de l'element actif de l'interface, et une
        // dialogue prend cette place tant qu'elle vit.
        useHotkey("escape", () => this.props.close());
    }

    _storedSize() {
        const size = {
            widthPct: clampPct(this.props.defaultWidthPct, 80),
            heightPct: clampPct(this.props.defaultHeightPct, 80),
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
     * Les `min()` gardent le panneau dans la fenetre meme a 100 % : la barre de
     * navigation et la marge sont retranchees dans le calcul, pas dans l'etat,
     * pour que la preference reste exprimee en pourcentage de la fenetre.
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
            this.state.widthPct === clampPct(this.props.defaultWidthPct, 80) &&
            this.state.heightPct === clampPct(this.props.defaultHeightPct, 80)
        );
    }

    /**
     * Sortie vers la pleine page, quand le panneau devient trop petit pour ce
     * qu'on fait. Sans ca il fallait le fermer et repasser par le menu.
     *
     * Le dossier courant suit : le navigateur memorise sa position dans
     * `localStorage` a chaque listing, et l'action autonome la relit au
     * montage. On ferme apres avoir demande l'action, pas avant.
     */
    openFullPage() {
        this.action.doAction("bf_nextcloud_browser.action_nc_browser_app");
        this.props.close();
    }

    resetSize() {
        this.state.widthPct = clampPct(this.props.defaultWidthPct, 80);
        this.state.heightPct = clampPct(this.props.defaultHeightPct, 80);
        try {
            browser.localStorage.removeItem(STORAGE_KEY);
        } catch {
            // sans stockage, la remise a zero ne vaut que pour cette ouverture
        }
    }

    // ----------------------------------------------------------------
    // Redimensionnement par la poignee du coin inferieur GAUCHE : le panneau
    // est ancre en haut a droite, il grandit donc vers la gauche et vers le bas.
    // ----------------------------------------------------------------
    startResize(ev) {
        const panel = ev.currentTarget.closest(".o_nc_panel");
        if (!panel) {
            return;
        }
        ev.preventDefault();
        const rect = panel.getBoundingClientRect();
        this.drag = { x: ev.clientX, y: ev.clientY, w: rect.width, h: rect.height };
        // La capture garde les evenements meme si le pointeur sort du panneau,
        // et evite d'avoir a ecouter la fenetre entiere.
        ev.currentTarget.setPointerCapture(ev.pointerId);
    }

    onResizeMove(ev) {
        if (!this.drag) {
            return;
        }
        const width = this.drag.w + (this.drag.x - ev.clientX);
        const height = this.drag.h + (ev.clientY - this.drag.y);
        this.state.widthPct = clampPct(
            (width / window.innerWidth) * 100,
            this.state.widthPct
        );
        this.state.heightPct = clampPct(
            (height / window.innerHeight) * 100,
            this.state.heightPct
        );
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
