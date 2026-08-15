/** @odoo-module **/
/**
 * Éditeur de cartographie — dessine le niveau en SVG, depuis les
 * enregistrements, et le repose dedans.
 *
 * Il ne recalcule aucune géométrie : le serveur envoie exactement les
 * coordonnées qui partent dans le `.bpmn`, dans le `.drawio` et dans le PDF.
 * Quatre rendus, une seule géométrie — c'est ce qui garantit que ce qu'on voit
 * ici est ce qu'on ouvrira ailleurs.
 *
 * L'écriture suit la même règle : rien n'est calculé ici. Un glisser-déposer
 * envoie un centre en coordonnées du tracé, et c'est le serveur qui le
 * reconvertit en pas de grille — le modèle stocke une colonne et une rangée,
 * pas des pixels. Après chaque écriture, le serveur renvoie le tracé complet
 * et le composant le remplace : jamais de disposition devinée localement.
 *
 * Le gel se lit AVANT d'offrir une poignée. Laisser déplacer une forme pour
 * rendre une erreur au relâchement serait une promesse qu'on ne tient pas.
 *
 * Sans bibliothèque tierce : un éditeur BPMN embarqué (bpmn-js) impose un
 * filigrane visible jusque dans l'usage commercial, ce qui est une décision à
 * prendre à part.
 */
import { Component, onWillStart, useState, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

/** Le refus lisible du serveur voyage dans `data.message`.
 *
 * ⚠️ `e.message` est la chaîne d'enveloppe (« Odoo Server Error »), PAS un
 * objet : `makeErrorFromResponse` pose `error.message = message` et
 * `error.data = errorData`. Lire `e.message.data` rend donc toujours
 * `undefined`, et le repli affichait « RPC_ERROR: Odoo Server Error » à la
 * place de la phrase que le serveur avait pris soin de rédiger — le soin mis
 * côté serveur à refuser lisiblement ne servait à rien ici.
 */
function messageDe(e) {
    return e.data?.message || e.message || String(e);
}

const EV_R = 19;
const INK = "#2D3031";
const GRIS = "#73787A";
const BLEU = "#29ABE1";
const AMBRE = "#D69921";
const BLEU_DOUX = "#EAF7FD";
const AMBRE_DOUX = "#FEF6E6";
const POOL_HDR = "#ECEFF1";
const LANE_BG = "#F9FAFB";
const HAIR = "#B8BCBE";

// vert : validée des deux côtés ; ambre : un seul ; rouge : contestée
const VALIDATION = { validee: "#1B8A4B", partielle: "#D69921", conteste: "#C0392B" };

const EVENEMENTS = ["start", "msgStart", "end", "timerCatch", "msgCatch"];
const PASSERELLES = ["xor", "and", "or"];

/** Découpe un libellé pour qu'il tienne dans une largeur, en mesurant vraiment. */
function couper(texte, largeur, taille, gras) {
    const ctx = couper._ctx || (couper._ctx =
        document.createElement("canvas").getContext("2d"));
    ctx.font = `${gras ? 600 : 400} ${taille}px Lexend, sans-serif`;
    const lignes = [];
    let courante = "";
    for (const mot of (texte || "").split(/\s+/).filter(Boolean)) {
        const essai = courante ? `${courante} ${mot}` : mot;
        if (ctx.measureText(essai).width <= largeur || !courante) {
            courante = essai;
        } else {
            lignes.push(courante);
            courante = mot;
        }
    }
    if (courante) {
        lignes.push(courante);
    }
    return lignes;
}

export class VisualiseurCartographie extends Component {
    static template = "bf_process.Visualiseur";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        // 100 % par défaut : une carte réduite pour tenir dans la colonne est
        // illisible, et c'est le défilement qui doit céder, pas le texte.
        this.state = useState({
            donnees: null, erreur: null, zoom: 1, plein: false,
            outil: "consulter", genre: "task", depuis: null, fantome: null,
            occupe: false,
        });
        this.svgRef = useRef("svg");
        this.toileRef = useRef("toile");
        this.glisse = null;
        this.deplacement = null;
        onWillStart(() => this.charger());
    }

    get resId() {
        return this.props.record ? this.props.record.resId : this.props.resId;
    }

    get resModel() {
        return this.props.record ? this.props.record.resModel : "bf.process.diagram";
    }

    async charger(niveauId) {
        const id = this.resId;
        if (!id) {
            this.state.erreur = _t("Enregistrez d'abord, le tracé suivra.");
            return;
        }
        try {
            const args = this.resModel === "bf.process"
                ? [id, niveauId || false] : [id];
            this.state.donnees = await this.orm.call(this.resModel, "rendu", args);
        } catch (e) {
            this.state.erreur = messageDe(e);
        }
    }

    /** Sauter d'un niveau à l'autre sans quitter le tracé. */
    async changerNiveau(ev) {
        const id = parseInt(ev.target.value, 10);
        if (this.resModel === "bf.process") {
            await this.charger(id);
        } else {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "bf.process.diagram",
                res_id: id,
                views: [[false, "form"]],
                target: "current",
            });
        }
    }

    ajuster() {
        const d = this.state.donnees;
        const toile = this.toileRef.el;
        if (!d || !toile) {
            return;
        }
        this.state.zoom = (toile.clientWidth - 16) / d.largeur;
    }

    zoomer(pas) {
        this.state.zoom = Math.min(4, Math.max(0.2, this.state.zoom * (1 + pas)));
    }

    plein_ecran() {
        this.state.plein = !this.state.plein;
        // laisser la nouvelle taille s'appliquer avant de remesurer
        setTimeout(() => this.ajuster(), 0);
    }

    /** Molette : zoom avec Ctrl, défilement normal sinon. */
    surMolette(ev) {
        if (!ev.ctrlKey) {
            return;
        }
        ev.preventDefault();
        this.zoomer(ev.deltaY < 0 ? 0.12 : -0.12);
    }

    /** Glisser pour déplacer la carte, comme dans n'importe quel plan. */
    debutGlisse(ev) {
        if (ev.button !== 0 || ev.target.dataset.rid || this.deplacement) {
            return;
        }
        const toile = this.toileRef.el;
        this.glisse = { x: ev.clientX, y: ev.clientY,
                        gx: toile.scrollLeft, gy: toile.scrollTop };
        toile.classList.add("o_bf_process_glisse");
    }

    surGlisse(ev) {
        if (this.deplacement) {
            this.surDeplacement(ev);
            return;
        }
        if (!this.glisse) {
            return;
        }
        const toile = this.toileRef.el;
        toile.scrollLeft = this.glisse.gx - (ev.clientX - this.glisse.x);
        toile.scrollTop = this.glisse.gy - (ev.clientY - this.glisse.y);
    }

    finGlisse(ev) {
        if (this.deplacement) {
            this.finDeplacement(ev);
        }
        this.glisse = null;
        if (this.toileRef.el) {
            this.toileRef.el.classList.remove("o_bf_process_glisse");
        }
    }

    /** Sortir du cadre abandonne le déplacement plutôt que de l'entériner. */
    annulerGlisse() {
        this.deplacement = null;
        this.state.fantome = null;
        this.finGlisse();
    }

    // --- édition ------------------------------------------------------------

    /** Le tracé accepte-t-il d'être modifié ? Lu du serveur, pas déduit ici. */
    get modifiable() {
        return !!(this.state.donnees && this.state.donnees.modifiable);
    }

    get outils() {
        return [
            { code: "consulter", nom: _t("Consulter"),
              aide: _t("Ouvrir la fiche d'une étape ou sa page dépliée.") },
            { code: "deplacer", nom: _t("Déplacer"),
              aide: _t("Glisser une forme. Elle se cale sur la grille.") },
            { code: "poser", nom: _t("Poser"),
              aide: _t("Cliquer sur le fond pour ajouter la forme choisie.") },
            { code: "lier", nom: _t("Lier"),
              aide: _t("Cliquer l'origine, puis la cible.") },
            { code: "retirer", nom: _t("Retirer"),
              aide: _t("Cliquer une forme ou un lien pour l'enlever.") },
        ];
    }

    get aideOutil() {
        const o = this.outils.find((x) => x.code === this.state.outil);
        return o ? o.aide : "";
    }

    choisirOutil(code) {
        this.state.outil = code;
        this.state.depuis = null;
        this.state.fantome = null;
    }

    /** Un événement de souris, en coordonnées du tracé. */
    _modele(ev) {
        const r = this.svgRef.el.getBoundingClientRect();
        return { x: (ev.clientX - r.left) / this.state.zoom,
                 y: (ev.clientY - r.top) / this.state.zoom };
    }

    /**
     * Une écriture, et le tracé que le serveur renvoie en retour.
     *
     * Le composant ne rejoue pas l'opération de son côté : il remplace ce
     * qu'il affichait. Deux nœuds d'un même couloir se repositionnent l'un par
     * rapport à l'autre, et seule la géométrie du serveur sait comment.
     */
    async _ecrire(methode, args) {
        if (this.state.occupe) {
            return false;
        }
        this.state.occupe = true;
        try {
            this.state.donnees = await this.orm.call(
                "bf.process.diagram", methode,
                [this.state.donnees.niveau_id, ...args]);
            return true;
        } catch (e) {
            const message = messageDe(e);
            this.notification.add(message, { type: "warning", sticky: false });
            return false;
        } finally {
            this.state.occupe = false;
            this.state.fantome = null;
            this.state.depuis = null;
        }
    }

    /** Prise d'une forme : le déplacement commence. */
    debutDeplacement(n, ev) {
        if (this.state.outil !== "deplacer" || !this.modifiable || ev.button !== 0) {
            return;
        }
        ev.stopPropagation();
        const p = this._modele(ev);
        this.deplacement = {
            code: n.id, cx: n.x + n.w / 2, cy: n.y + n.h / 2,
            ox: p.x, oy: p.y, w: n.w, h: n.h, bouge: false,
        };
    }

    surDeplacement(ev) {
        const d = this.deplacement;
        if (!d) {
            return;
        }
        const p = this._modele(ev);
        const pas = this.state.donnees.pas || 0.05;
        const cale = (v, unite) => Math.round(v / (pas * unite)) * pas * unite;
        const dx = cale(p.x - d.ox, this.state.donnees.col_w);
        const dy = cale(p.y - d.oy, this.state.donnees.row_h);
        if (dx || dy) {
            d.bouge = true;
        }
        this.state.fantome = { x: d.cx + dx - d.w / 2, y: d.cy + dy - d.h / 2,
                               w: d.w, h: d.h };
    }

    async finDeplacement(ev) {
        const d = this.deplacement;
        this.deplacement = null;
        if (!d || !d.bouge) {
            this.state.fantome = null;
            return;
        }
        const p = this._modele(ev);
        await this._ecrire("deplacer_noeud",
                           [d.code, d.cx + (p.x - d.ox), d.cy + (p.y - d.oy)]);
    }

    /** Un clic sur une forme : ce qu'il fait dépend de l'outil en main. */
    async surClicNoeud(n, ev) {
        if (this.state.outil === "consulter" || !this.modifiable) {
            this.ouvrir(n);
            return;
        }
        ev.stopPropagation();
        if (this.state.outil === "lier") {
            if (!this.state.depuis) {
                this.state.depuis = n.id;
                return;
            }
            const depuis = this.state.depuis;
            if (depuis !== n.id) {
                await this._ecrire("creer_flux", [depuis, n.id]);
            }
            this.state.depuis = null;
        } else if (this.state.outil === "retirer") {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Retirer cette forme"),
                body: _t(
                    "« %s » et tout ce qui s'y raccorde seront retirés du tracé."
                    + " Cette opération est inscrite au fil du processus.",
                    n.nom || n.id),
                confirmLabel: _t("Retirer"),
                confirm: () => this._ecrire("supprimer_noeud", [n.id]),
                cancel: () => {},
            });
        }
    }

    /** Un clic sur un lien, pour le couper. */
    async surClicFlux(f, ev) {
        if (this.state.outil !== "retirer" || !this.modifiable) {
            return;
        }
        ev.stopPropagation();
        await this._ecrire("supprimer_flux", [f.src, f.tgt]);
    }

    /** Un clic sur le fond : c'est là qu'on pose une forme. */
    async surClicFond(ev) {
        if (this.state.outil !== "poser" || !this.modifiable
            || ev.target.dataset.rid) {
            return;
        }
        const p = this._modele(ev);
        const avant = new Set(this.state.donnees.noeuds.map((n) => n.id));
        const ok = await this._ecrire("creer_noeud",
                                      [this.state.genre, p.x, p.y]);
        if (!ok) {
            return;
        }
        // la fiche s'ouvre tout de suite : une forme posée doit être nommée,
        // et c'est là que vivent son libellé, son ton et son registre
        const neuf = this.state.donnees.noeuds.find((n) => !avant.has(n.id));
        if (neuf && neuf.rid) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "bf.process.node",
                res_id: neuf.rid,
                views: [[false, "form"]],
                target: "new",
            }, { onClose: () => this.charger(this.state.donnees.niveau_id) });
        }
    }

    /** Un clic sur une forme ouvre ce qu'elle représente. */
    ouvrir(n) {
        if (n.enfant) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "bf.process.diagram",
                res_id: n.enfant,
                views: [[false, "form"]],
                target: "current",
            });
        } else if (n.rid) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "bf.process.node",
                res_id: n.rid,
                views: [[false, "form"]],
                target: "new",
            });
        }
    }

    /** Le tracé s'emporte en SVG — un format ouvert, lisible partout. */
    telecharger() {
        const svg = this.svgRef.el;
        if (!svg) {
            return;
        }
        const copie = svg.cloneNode(true);
        copie.setAttribute("xmlns", "http://www.w3.org/2000/svg");
        const blob = new Blob(
            ['<?xml version="1.0" encoding="UTF-8"?>', copie.outerHTML],
            { type: "image/svg+xml" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${this.state.donnees.titre.replace(/[^\w -]+/g, "")}.svg`;
        a.click();
        URL.revokeObjectURL(url);
    }

    /** Tous les éléments à dessiner, à plat, dans l'ordre d'empilement. */
    get elements() {
        const d = this.state.donnees;
        if (!d) {
            return [];
        }
        const out = [];
        for (const e of d.externes) {
            out.push(...this.cadre(e, d.entete_pool, POOL_HDR, 1.1));
        }
        out.push(...this.cadre(d.pool, d.entete_pool, POOL_HDR, 1.2));
        d.couloirs.forEach((c, i) => {
            out.push({ t: "rect", x: c.x, y: c.y, w: d.entete_couloir, h: c.h,
                       fill: LANE_BG, stroke: INK, sw: 0.9 });
            out.push({ t: "vtexte", x: c.x + d.entete_couloir - 8,
                       y: c.y + c.h / 2, s: c.nom, taille: 8.8, gras: true,
                       max: c.h - 14 });
            if (i < d.couloirs.length - 1) {
                out.push({ t: "ligne", pts: [[c.x, c.y + c.h],
                                             [c.x + c.w, c.y + c.h]],
                           stroke: HAIR, sw: 0.9 });
            }
        });
        for (const f of d.flux) {
            out.push(...this.arete(f, f.association));
        }
        for (const m of d.messages) {
            out.push(...this.arete(m, false, true));
        }
        for (const n of d.noeuds) {
            out.push(...this.noeud(n));
        }
        for (const n of d.noeuds) {
            if (n.genre === "note") {
                continue;
            }
            if (n.fil) {
                out.push({ t: "cercle", cx: n.x + n.w - 5, cy: n.y + 5, r: 4,
                           fill: BLEU, stroke: "#FFFFFF", sw: 1 });
            }
            const teinte = VALIDATION[n.validation];
            if (teinte) {
                out.push({ t: "cercle", cx: n.x + 5, cy: n.y + 5, r: 4,
                           fill: teinte, stroke: "#FFFFFF", sw: 1 });
            }
        }
        // les liens ne s'attrapent qu'au moment de les couper : une bande large
        // et invisible par-dessus le trait, plutôt qu'un trait d'un point
        if (this.state.outil === "retirer" && this.modifiable) {
            for (const f of d.flux) {
                out.push({ t: "lien", pts: f.points, f });
            }
        }
        // en consultation, une annotation n'a pas de zone : son texte doit
        // rester sélectionnable. En édition il faut pouvoir l'attraper.
        const edite = this.state.outil !== "consulter" && this.modifiable;
        for (const n of d.noeuds) {
            if (n.genre === "note" && !edite) {
                continue;
            }
            out.push({ t: "zone", x: n.x, y: n.y, w: n.w, h: n.h, n,
                       pris: this.state.depuis === n.id });
        }
        if (this.state.fantome) {
            out.push({ t: "fantome", ...this.state.fantome });
        }
        return out;
    }

    cadre(p, entete, teinte, sw) {
        return [
            { t: "rect", x: p.x, y: p.y, w: p.w, h: p.h,
              fill: "#FFFFFF", stroke: INK, sw },
            { t: "rect", x: p.x, y: p.y, w: entete, h: p.h,
              fill: teinte, stroke: INK, sw },
            { t: "vtexte", x: p.x + entete - 8, y: p.y + p.h / 2, s: p.nom,
              taille: 9.4, gras: true, max: p.h - 12 },
        ];
    }

    arete(f, association, message) {
        const pts = f.points;
        const out = [{
            t: "ligne", pts, stroke: association ? GRIS : INK,
            sw: association ? 0.9 : 1.05,
            tirets: association ? "1 3" : (message ? "4 3" : null),
        }];
        const [ax, ay] = pts[pts.length - 2];
        const [bx, by] = pts[pts.length - 1];
        if (!association) {
            const ang = Math.atan2(by - ay, bx - ax);
            const s = 8;
            out.push({
                t: "poly",
                pts: [[bx, by],
                      [bx - s * Math.cos(ang - 0.42), by - s * Math.sin(ang - 0.42)],
                      [bx - s * Math.cos(ang + 0.42), by - s * Math.sin(ang + 0.42)]],
                fill: message ? "#FFFFFF" : INK, stroke: INK, sw: 0.9,
            });
        }
        if (f.etiquette) {
            let seg = [pts[0], pts[1]];
            let meilleur = -1;
            for (let i = 0; i < pts.length - 1; i++) {
                const l = Math.abs(pts[i][0] - pts[i + 1][0])
                        + Math.abs(pts[i][1] - pts[i + 1][1]);
                if (l > meilleur) {
                    meilleur = l;
                    seg = [pts[i], pts[i + 1]];
                }
            }
            const t = message ? 0.5 : 0.5;
            const mx = seg[0][0] + (seg[1][0] - seg[0][0]) * t;
            const my = seg[0][1] + (seg[1][1] - seg[0][1]) * t;
            out.push({
                t: "bloc", x: mx + f.decalage[0], y: my + f.decalage[1] - 9,
                s: f.etiquette, taille: 8.8, couleur: GRIS,
                max: f.largeur_etiquette, ancre: message ? "start" : "middle",
            });
        }
        return out;
    }

    noeud(n) {
        const cx = n.x + n.w / 2;
        const cy = n.y + n.h / 2;
        const out = [];
        if (EVENEMENTS.includes(n.genre)) {
            out.push({ t: "cercle", cx, cy, r: EV_R, fill: "#FFFFFF",
                       stroke: INK, sw: n.genre === "end" ? 2.6 : 1.1 });
            if (n.genre === "timerCatch" || n.genre === "msgCatch") {
                out.push({ t: "cercle", cx, cy, r: EV_R - 3.4, fill: "none",
                           stroke: INK, sw: 1 });
            }
            if (n.genre === "msgStart" || n.genre === "msgCatch") {
                out.push(...this.enveloppe(cx, cy, 15, false));
            }
            if (n.genre === "timerCatch") {
                out.push({ t: "cercle", cx, cy, r: 10, fill: "#FFFFFF",
                           stroke: INK, sw: 0.9 });
                out.push({ t: "ligne", pts: [[cx, cy], [cx, cy - 6.2]],
                           stroke: INK, sw: 0.9 });
                out.push({ t: "ligne", pts: [[cx, cy], [cx + 4.8, cy + 2]],
                           stroke: INK, sw: 0.9 });
            }
            out.push({ t: "bloc", x: cx, y: n.y + n.h + 22, s: n.nom,
                       taille: 8.8, couleur: INK, max: 150,
                       gras: n.genre === "end", ancre: "middle" });
        } else if (PASSERELLES.includes(n.genre)) {
            out.push({ t: "poly", pts: [[cx, n.y], [n.x + n.w, cy],
                                        [cx, n.y + n.h], [n.x, cy]],
                       fill: "#FFFFFF", stroke: INK, sw: 1, ferme: true });
            if (n.genre === "and") {
                out.push({ t: "ligne", pts: [[cx - 11, cy], [cx + 11, cy]],
                           stroke: INK, sw: 1.7 });
                out.push({ t: "ligne", pts: [[cx, cy - 11], [cx, cy + 11]],
                           stroke: INK, sw: 1.7 });
            } else if (n.genre === "or") {
                out.push({ t: "cercle", cx, cy, r: 12, fill: "none",
                           stroke: INK, sw: 2 });
            } else {
                out.push({ t: "ligne", pts: [[cx - 8, cy - 8], [cx + 8, cy + 8]],
                           stroke: INK, sw: 1.7 });
                out.push({ t: "ligne", pts: [[cx - 8, cy + 8], [cx + 8, cy - 8]],
                           stroke: INK, sw: 1.7 });
            }
            if (n.nom) {
                out.push({ t: "bloc", x: cx, y: n.y - 18, s: n.nom, taille: 8.8,
                           couleur: GRIS, max: 150, ancre: "middle" });
            }
        } else if (n.genre === "note") {
            const couleur = n.ton === "risk" ? AMBRE : (n.ton === "ai" ? BLEU : GRIS);
            if (n.ton) {
                out.push({ t: "rect", x: n.x, y: n.y, w: n.w, h: n.h,
                           fill: n.ton === "risk" ? AMBRE_DOUX : BLEU_DOUX,
                           stroke: "none", sw: 0 });
            }
            out.push({ t: "ligne", stroke: couleur, sw: n.ton ? 1.4 : 1,
                       pts: [[n.x + 9, n.y], [n.x, n.y], [n.x, n.y + n.h],
                             [n.x + 9, n.y + n.h]] });
            out.push({ t: "bloc", x: n.x + 14, y: n.y + 13, s: n.nom,
                       taille: 8.6, couleur, max: n.w - 20, ancre: "start",
                       haut: true });
        } else if (n.genre === "store") {
            out.push({ t: "rect", x: n.x, y: n.y + 6, w: n.w, h: n.h - 10,
                       fill: "#FFFFFF", stroke: INK, sw: 0.9 });
            out.push({ t: "ligne", pts: [[n.x, n.y + 6], [n.x + n.w, n.y + 6]],
                       stroke: INK, sw: 0.9 });
            out.push({ t: "bloc", x: cx, y: n.y + n.h + 14, s: n.nom,
                       taille: 8.6, couleur: GRIS, max: 120, ancre: "middle" });
        } else {
            out.push({ t: "rect", x: n.x, y: n.y, w: n.w, h: n.h, r: 9,
                       fill: "#FFFFFF", stroke: INK, sw: 1 });
            if (n.genre === "send" || n.genre === "receive") {
                out.push(...this.enveloppe(n.x + 15, n.y + 13, 13,
                                           n.genre === "send"));
            } else if (n.genre === "user") {
                out.push({ t: "cercle", cx: n.x + 14, cy: n.y + 9.6, r: 3.2,
                           fill: "#FFFFFF", stroke: INK, sw: 0.8 });
                out.push({ t: "poly", ferme: true, fill: "#FFFFFF", stroke: INK,
                           sw: 0.8,
                           pts: [[n.x + 8.6, n.y + 19], [n.x + 9.8, n.y + 13.5],
                                 [n.x + 18.2, n.y + 13.5], [n.x + 19.4, n.y + 19]] });
            } else if (n.genre === "sub") {
                out.push({ t: "rect", x: cx - 7, y: n.y + n.h - 15, w: 14, h: 14,
                           fill: "#FFFFFF", stroke: INK, sw: 0.9 });
                out.push({ t: "ligne", pts: [[cx - 4, n.y + n.h - 8],
                                             [cx + 4, n.y + n.h - 8]],
                           stroke: INK, sw: 0.9 });
                out.push({ t: "ligne", pts: [[cx, n.y + n.h - 12],
                                             [cx, n.y + n.h - 4]],
                           stroke: INK, sw: 0.9 });
            }
            const pad = ["send", "receive", "user"].includes(n.genre) ? 5 : 0;
            out.push({ t: "bloc", x: cx, y: cy + pad - (n.genre === "sub" ? 3 : 0),
                       s: n.nom, taille: n.genre === "sub" ? 9.6 : 9.2,
                       couleur: INK, max: n.w - 20, gras: n.genre === "sub",
                       ancre: "middle", centre: true });
        }
        return out;
    }

    enveloppe(cx, cy, w, plein) {
        const h = w * 0.68;
        return [
            { t: "rect", x: cx - w / 2, y: cy - h / 2, w, h,
              fill: plein ? INK : "#FFFFFF", stroke: INK, sw: 0.8 },
            { t: "ligne", stroke: plein ? "#FFFFFF" : INK, sw: 0.8,
              pts: [[cx - w / 2, cy - h / 2], [cx, cy + h * 0.18],
                    [cx + w / 2, cy - h / 2]] },
        ];
    }

    /** Un bloc de texte devient ses lignes, prêtes à poser. */
    lignes(e) {
        const lignes = couper(e.s, e.max || 200, e.taille, e.gras);
        const inter = e.taille * 1.22;
        let y = e.y;
        if (e.centre) {
            y = e.y - (lignes.length * inter) / 2 + e.taille * 0.95;
        } else if (!e.haut) {
            y = e.y + e.taille * 0.9;
        } else {
            y = e.y + e.taille;
        }
        return lignes.map((s, i) => ({ s, y: y + i * inter }));
    }

    points(pts) {
        return pts.map((p) => p.join(",")).join(" ");
    }
}

registry.category("view_widgets").add("bf_process_visualiseur", {
    component: VisualiseurCartographie,
});
