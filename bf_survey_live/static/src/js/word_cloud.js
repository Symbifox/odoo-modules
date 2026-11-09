/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { rpc } from "@web/core/network/rpc";

// La spirale d'Archimède : on part du centre et on s'éloigne jusqu'à trouver une
// place libre. C'est ce que font les vrais nuages de mots, et c'est pour ça que
// les gros mots finissent au milieu : ils passent les premiers.
const PAS_ANGLE = 0.22;          // radians par essai
const PAS_RAYON = 3.4;           // pixels par radian
const APLATISSEMENT = 1.7;       // la salle est large, pas haute : ellipse
const ESSAIS_MAX = 2600;
const MARGE = 5;                 // pixels entre deux mots

const RAFRAICHISSEMENT = 2000;

publicWidget.registry.BfLiveWordCloud = publicWidget.Widget.extend({
    init(parent, options) {
        this._super(...arguments);
        this.surveyAccessToken = options.surveyAccessToken;
        // Un mot garde SON élément d'un rafraîchissement à l'autre : c'est la
        // seule façon que le navigateur ait de l'animer vers sa nouvelle place
        // au lieu de le faire sauter.
        this.elements = new Map();
    },

    start() {
        return this._super(...arguments).then(() => {
            this.el.classList.add("o_bf_live_cloud_ready");
            this._dessiner(this._motsInitiaux());
            this.minuterie = setInterval(this._rafraichir.bind(this), RAFRAICHISSEMENT);
        });
    },

    destroy() {
        if (this.minuterie) {
            clearInterval(this.minuterie);
            this.minuterie = null;
        }
        this._super(...arguments);
    },

    //--------------------------------------------------------------------------
    // Privé
    //--------------------------------------------------------------------------

    _motsInitiaux() {
        // Le premier affichage porte déjà son nuage : sans ça, revenir sur une
        // question montre un cadre vide pendant deux secondes.
        try {
            return JSON.parse(this.el.dataset.bfWords || "[]");
        } catch {
            return [];
        }
    },

    async _rafraichir() {
        let reponse;
        try {
            reponse = await rpc(`/bf_survey_live/word_cloud/${this.surveyAccessToken}`);
        } catch {
            // La séance s'est fermée ou le réseau a cédé : on arrête de demander.
            clearInterval(this.minuterie);
            this.minuterie = null;
            return;
        }
        if (reponse && reponse.words) {
            this._dessiner(reponse.words);
        }
    },

    _dessiner(mots) {
        const largeur = this.el.clientWidth;
        const hauteur = this.el.clientHeight;
        if (!largeur || !hauteur) {
            return;
        }
        const vus = new Set(mots.map((mot) => mot.text));
        for (const [texte, element] of this.elements) {
            if (!vus.has(texte)) {
                element.remove();
                this.elements.delete(texte);
            }
        }
        // La taille de référence suit la hauteur du cadre : le même nuage se lit
        // sur un portable de développement et sur un projecteur.
        const base = Math.max(13, Math.min(38, hauteur * 0.055));
        const poses = [];
        for (const mot of mots) {
            const element = this._element(mot, base);
            const boite = this._placer(element, largeur, hauteur, poses);
            if (boite) {
                poses.push(boite);
                element.classList.remove("o_bf_live_word_skipped");
            } else {
                // Plus de place : on tait le mot plutôt que de l'empiler sur un
                // autre. Un nuage illisible ne dit rien du tout.
                element.classList.add("o_bf_live_word_skipped");
            }
        }
    },

    _element(mot, base) {
        let element = this.elements.get(mot.text);
        if (!element) {
            element = document.createElement("span");
            element.className = "o_bf_live_word";
            element.textContent = mot.text;
            this.el.appendChild(element);
            this.elements.set(mot.text, element);
        }
        element.style.fontSize = `${(mot.size * base).toFixed(1)}px`;
        // Les mots rares s'effacent un peu : la taille dit le compte, l'encre
        // aide l'oeil à trouver les grands.
        const part = Math.max(0, Math.min(1, (mot.size - 1) / 2.4));
        element.style.opacity = (0.55 + 0.45 * part).toFixed(2);
        element.dataset.bfCount = mot.count;
        element.dataset.bfSize = mot.size;
        return element;
    },

    _placer(element, largeur, hauteur, poses) {
        let l = element.offsetWidth;
        let h = element.offsetHeight;
        // ⚠️ Un mot plus large que le cadre se RÉTRÉCIT, il ne se perd pas.
        // Mesuré : « collaboration » à 82 px faisait 530 px de long et
        // disparaissait en silence du nuage, alors que la salle l'avait écrit.
        let garde = 0;
        while ((l > largeur || h > hauteur) && garde < 8) {
            const taille = parseFloat(element.style.fontSize) || 16;
            element.style.fontSize = `${(taille * 0.85).toFixed(1)}px`;
            l = element.offsetWidth;
            h = element.offsetHeight;
            garde += 1;
        }
        if (!l || !h || l > largeur || h > hauteur) {
            return false;
        }
        const cx = largeur / 2;
        const cy = hauteur / 2;
        for (let essai = 0; essai < ESSAIS_MAX; essai++) {
            const angle = essai * PAS_ANGLE;
            const rayon = PAS_RAYON * angle;
            const x = cx + Math.cos(angle) * rayon * APLATISSEMENT - l / 2;
            const y = cy + Math.sin(angle) * rayon - h / 2;
            if (x < 0 || y < 0 || x + l > largeur || y + h > hauteur) {
                continue;
            }
            const boite = { x, y, l, h };
            if (!poses.some((autre) => this._cogne(boite, autre))) {
                element.style.left = `${x.toFixed(1)}px`;
                element.style.top = `${y.toFixed(1)}px`;
                return boite;
            }
        }
        return false;
    },

    _cogne(une, autre) {
        return (
            une.x < autre.x + autre.l + MARGE &&
            une.x + une.l + MARGE > autre.x &&
            une.y < autre.y + autre.h + MARGE &&
            une.y + une.h + MARGE > autre.y
        );
    },
});

export default publicWidget.registry.BfLiveWordCloud;
