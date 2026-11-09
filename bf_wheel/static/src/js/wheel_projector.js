/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const RAYON = 100;          // unités du dessin, pas des pixels
const RAYON_TEXTE = 0.63;   // où l'étiquette se pose sur le rayon
const TOURS = 6;            // tours complets avant de s'arrêter
export const DUREE = 4500;  // durée de la rotation, en millisecondes

const ENCRE_FONCEE = "#2d3031";
const ENCRE_CLAIRE = "#ffffff";

/** Luminance relative d'une couleur, selon la formule de la WCAG. */
function luminance(hex) {
    const canaux = [1, 3, 5].map((i) => {
        const v = parseInt(hex.slice(i, i + 2), 16) / 255;
        return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2];
}

/**
 * L'encre d'une étiquette, en dernier recours.
 *
 * Le serveur envoie l'encre avec chaque candidat, et c'est la sienne qui vaut.
 * Ce repli n'existe que pour qu'une étiquette ne reste JAMAIS sans encre : un
 * `fill` vide retombe au noir, illisible sur une part foncée.
 */
export function encre(couleur) {
    if (!couleur || !/^#[0-9a-f]{6}$/i.test(couleur)) {
        return ENCRE_CLAIRE;
    }
    const l = luminance(couleur);
    const contrasteClair = 1.05 / (l + 0.05);
    const contrasteFonce = (l + 0.05) / (luminance(ENCRE_FONCEE) + 0.05);
    return contrasteClair >= contrasteFonce ? ENCRE_CLAIRE : ENCRE_FONCEE;
}

/** Un point du cercle, l'angle comptant depuis midi, dans le sens des aiguilles. */
function point(angle, rayon) {
    const rad = ((angle - 90) * Math.PI) / 180;
    return [rayon * Math.cos(rad), rayon * Math.sin(rad)];
}

/**
 * Les parts de la roue, chacune proportionnelle à son poids.
 *
 * Un seul candidat ne se dessine pas en « part » : un arc de 360 degrés a le
 * même point de départ et d'arrivée, et le chemin SVG se referme sur rien. Ce
 * cas-là rend un disque plein.
 */
export function parts(candidats) {
    const total = candidats.reduce((somme, c) => somme + (c.weight || 1), 0) || 1;
    let debut = 0;
    return candidats.map((candidat, index) => {
        const angle = (360 * (candidat.weight || 1)) / total;
        const fin = debut + angle;
        const milieu = debut + angle / 2;
        const [x1, y1] = point(debut, RAYON);
        const [x2, y2] = point(fin, RAYON);
        const grand = angle > 180 ? 1 : 0;
        const chemin = candidats.length === 1
            ? `M 0 ${-RAYON} A ${RAYON} ${RAYON} 0 1 1 0 ${RAYON} A ${RAYON} ${RAYON} 0 1 1 0 ${-RAYON} Z`
            : `M 0 0 L ${x1.toFixed(3)} ${y1.toFixed(3)} `
              + `A ${RAYON} ${RAYON} 0 ${grand} 1 ${x2.toFixed(3)} ${y2.toFixed(3)} Z`;
        debut = fin;
        return {
            index,
            label: candidat.label,
            weight: candidat.weight || 1,
            color: candidat.color || "#29abe2",
            textColor: candidat.text_color || encre(candidat.color),
            chemin,
            milieu,
            part: angle,
            // Une étiquette posée le long du rayon se lit du centre vers le bord.
            // La rotation du texte s'ajoute à celle de la part : à droite de l'axe
            // il faut -90, à gauche +90, sinon le mot sort à l'envers. (Mesuré :
            // les quatre étiquettes se lisaient tête en bas avec la règle inverse.)
            sensTexte: milieu > 180 ? 90 : -90,
        };
    });
}

export class WheelProjector extends Component {
    static template = "bf_wheel.Projector";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.wheelId = this._wheelIdFromAction();
        this.state = useState({
            name: "",
            candidates: [],
            canSpin: false,
            claimEnabled: false,
            removeDrawn: false,
            angle: 0,
            spinning: false,
            winner: null,     // { label, index, spinId }
            claimName: "",
            claimContact: "",
            claimDone: false,
            error: "",
        });
        onWillStart(() => this.charger());
    }

    _wheelIdFromAction() {
        const action = this.props.action || {};
        const params = action.params || {};
        return params.wheel_id || (action.context && action.context.active_id) || false;
    }

    get parts() {
        return parts(this.state.candidates);
    }

    get rayonTexte() {
        return -RAYON * RAYON_TEXTE;
    }

    async charger() {
        if (!this.wheelId) {
            this.state.error = _t("Aucune roue à projeter.");
            return;
        }
        const data = await this.orm.call("bf.wheel", "get_display_data", [[this.wheelId]]);
        this.state.name = data.name;
        this.state.candidates = data.candidates;
        this.state.canSpin = data.can_spin;
        this.state.claimEnabled = data.claim_enabled;
        this.state.removeDrawn = data.remove_drawn;
    }

    /**
     * Fait tourner. Le serveur tire ; l'écran ne fait que rejoindre son résultat.
     *
     * La liste renvoyée par le tirage REMPLACE celle affichée : entre l'ouverture
     * de l'écran et le bouton, un candidat a pu arriver ou partir, et la roue qu'on
     * regarde doit être celle d'où le résultat est sorti.
     */
    async spin() {
        if (this.state.spinning) {
            return;
        }
        this.state.spinning = true;
        this.state.winner = null;
        this.state.claimDone = false;
        this.state.claimName = "";
        this.state.claimContact = "";
        let resultat;
        try {
            resultat = await this.orm.call("bf.wheel", "action_spin", [[this.wheelId]]);
        } catch (erreur) {
            this.state.spinning = false;
            throw erreur;
        }
        this.state.candidates = resultat.candidates;
        const gagnante = this.parts[resultat.winner_index];
        // La part gagnante doit venir sous l'aiguille, en haut. On ajoute des tours
        // complets à l'angle courant pour que la roue reparte toujours vers l'avant.
        const cible = 360 - gagnante.milieu;
        const base = this.state.angle - (this.state.angle % 360);
        this.state.angle = base + 360 * TOURS + cible;
        setTimeout(() => {
            this.state.spinning = false;
            this.state.winner = {
                label: resultat.winner_label,
                index: resultat.winner_index,
                spinId: resultat.spin_id,
                claimEnabled: resultat.claim_enabled,
            };
        }, DUREE);
    }

    get dureeStyle() {
        return `transition-duration: ${this.state.spinning ? DUREE : 0}ms`;
    }

    async noterPersonne() {
        const nom = (this.state.claimName || "").trim();
        if (!nom) {
            this.notification.add(_t("Il faut au moins un nom."), { type: "warning" });
            return;
        }
        await this.orm.call("bf.wheel.spin", "action_claim", [
            [this.state.winner.spinId], nom, this.state.claimContact || "",
        ]);
        this.state.claimDone = true;
        this.notification.add(_t("Noté au registre."), { type: "success" });
    }

    async fermer() {
        await this.charger();
        this.state.winner = null;
        this.state.angle = this.state.angle % 360;
    }

    ouvrirRoue() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "bf.wheel",
            res_id: this.wheelId,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("bf_wheel.projector", WheelProjector);
