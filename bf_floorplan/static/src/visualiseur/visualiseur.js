/** @odoo-module **/
/**
 * Éditeur de plan d'étage — dessine le plan en SVG, depuis les
 * enregistrements, et le repose dedans.
 *
 * Même règle que l'éditeur de cartographie : rien n'est calculé ici. Un
 * glisser-déposer envoie un coin en centimètres du plan, et c'est le serveur
 * qui le recale sur la grille et le garde dans le plan. Après chaque
 * écriture, le serveur renvoie le plan complet et le composant le remplace.
 *
 * Le gel se lit AVANT d'offrir une poignée. Sans bibliothèque tierce.
 */
import { Component, onMounted, onWillStart, useExternalListener, useState, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

/** Le refus lisible du serveur voyage dans `data.message`. */
function messageDe(e) {
    return e.data?.message || e.message || String(e);
}

const INK = "#2D3031";
const GRIS = "#73787A";
const BLEU = "#29ABE1";
const AMBRE = "#D69921";
const ROUGE = "#C0392B";
const VERT = "#1B8A4B";
const BLEU_DOUX = "#EAF7FD";

const COULEUR_LIEN = { reseau: GRIS, fibre: AMBRE, electrique: ROUGE, autre: INK };
const TEINTE = { alerte: ROUGE, attention: AMBRE, ok: VERT };
const FOND_ELEMENT = { serveur: "#ECEFF1", baie: "#DDE3E6", borne: BLEU_DOUX,
                       commutateur: "#FFFFFF", prise: "#FFFFFF" };
const RONDS = ["borne", "telephone", "camera"];

/** Un contexte de canevas pour mesurer un libellé, créé une fois. */
function couper() {}
couper._ctx = typeof document !== "undefined"
    ? document.createElement("canvas").getContext("2d") : null;

export class VisualiseurPlan extends Component {
    static template = "bf_floorplan.Visualiseur";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.state = useState({
            donnees: null, erreur: null, zoom: 0.5, plein: false,
            outil: "consulter", sorte: "element", genre: "poste",
            genreLien: "reseau", depuis: null, fantome: null, occupe: false,
            recherche: "",
        });
        // Échap : lâcher ce qu'on tient, sortir du plein écran, vider la
        // recherche — dans cet ordre, une chose à la fois
        useExternalListener(window, "keydown", (ev) => this.surTouche(ev));
        this.svgRef = useRef("svg");
        this.toileRef = useRef("toile");
        this.glisse = null;
        this.deplacement = null;
        this.redimension = null;
        onWillStart(() => this.charger());
        // 100 % d'un plan de vingt mètres ne tient dans aucune colonne : on
        // part ajusté à la largeur, et le zoom reste à portée de main.
        onMounted(() => this.ajuster());
    }

    get resId() {
        return this.props.record ? this.props.record.resId : this.props.resId;
    }

    /** Ce qu'il faut surligner quand on arrive depuis une fiche : un élément
     *  ou une zone. Les deux clés voyagent dans le contexte de l'action, et
     *  repartent telles quelles vers le serveur à chaque appel. */
    get contexteSurligne() {
        const ctx = this.props.record?.context || {};
        const out = {};
        for (const cle of ["bf_floorplan_surligne", "bf_floorplan_surligne_zone"]) {
            if (ctx[cle]) {
                out[cle] = ctx[cle];
            }
        }
        return Object.keys(out).length ? { context: out } : {};
    }

    async charger() {
        const id = this.resId;
        if (!id) {
            this.state.erreur = _t("Enregistrez d'abord, le plan suivra.");
            return;
        }
        try {
            this.state.donnees = await this.orm.call("bf.floorplan", "rendu", [id], this.contexteSurligne);
        } catch (e) {
            this.state.erreur = messageDe(e);
        }
    }

    surTouche(ev) {
        if (ev.key !== "Escape") {
            return;
        }
        if (this.deplacement || this.redimension || this.state.depuis || this.state.fantome) {
            this.annulerGlisse();
            this.state.depuis = null;
        } else if (this.state.plein) {
            this.plein_ecran();
        } else if (this.state.recherche) {
            this.state.recherche = "";
        }
    }

    // --- recherche --------------------------------------------------------------

    /** « Trouver quelqu'un ou quelque chose » : l'étiquette, l'occupant, la
     *  nature, la zone ou la cible d'un élément ; le nom ou le code d'une zone. */
    correspond(f) {
        const q = this.state.recherche.trim().toLowerCase();
        if (!q) {
            return false;
        }
        const champs = f.sorte === "zone"
            ? [f.nom, f.code, f.genre_nom]
            : [f.nom, f.occupant, f.info, f.zone, f.cible && f.cible.nom];
        return champs.some((c) => c && String(c).toLowerCase().includes(q));
    }

    get nbTrouves() {
        return this.zones.concat(this.elements).filter((f) => this.correspond(f)).length;
    }

    // --- cadre ----------------------------------------------------------------

    ajuster() {
        const d = this.state.donnees;
        const toile = this.toileRef.el;
        if (!d || !toile || !toile.clientWidth) {
            return;
        }
        this.state.zoom = Math.max(0.05, (toile.clientWidth - 16) / d.largeur);
    }

    zoomer(pas) {
        this.state.zoom = Math.min(4, Math.max(0.05, this.state.zoom * (1 + pas)));
    }

    plein_ecran() {
        this.state.plein = !this.state.plein;
        setTimeout(() => this.ajuster(), 0);
    }

    surMolette(ev) {
        if (!ev.ctrlKey) {
            return;
        }
        ev.preventDefault();
        this.zoomer(ev.deltaY < 0 ? 0.12 : -0.12);
    }

    debutGlisse(ev) {
        if (ev.button !== 0 || ev.target.dataset.rid || this.deplacement || this.redimension) {
            return;
        }
        const toile = this.toileRef.el;
        this.glisse = { x: ev.clientX, y: ev.clientY,
                        gx: toile.scrollLeft, gy: toile.scrollTop };
        toile.classList.add("o_bf_floorplan_glisse");
    }

    surGlisse(ev) {
        if (this.deplacement) {
            this.surDeplacement(ev);
            return;
        }
        if (this.redimension) {
            this.surRedimension(ev);
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
        } else if (this.redimension) {
            this.finRedimension(ev);
        }
        this.glisse = null;
        if (this.toileRef.el) {
            this.toileRef.el.classList.remove("o_bf_floorplan_glisse");
        }
    }

    annulerGlisse() {
        this.deplacement = null;
        this.redimension = null;
        this.state.fantome = null;
        this.finGlisse();
    }

    // --- édition --------------------------------------------------------------

    get modifiable() {
        return !!(this.state.donnees && this.state.donnees.modifiable);
    }

    get outils() {
        return [
            { code: "consulter", nom: _t("Consulter"),
              aide: _t("Cliquer une forme ouvre ce qu'elle représente.") },
            { code: "deplacer", nom: _t("Déplacer"),
              aide: _t("Glisser une forme ; la poignée en bas à droite la redimensionne.") },
            { code: "poser", nom: _t("Poser"),
              aide: _t("Cliquer sur le plan pour y poser la forme choisie.") },
            { code: "tourner", nom: _t("Tourner"),
              aide: _t("Cliquer un élément le tourne d'un quart de tour.") },
            { code: "lier", nom: _t("Lier"),
              aide: _t("Cliquer le premier élément, puis le second.") },
            { code: "retirer", nom: _t("Retirer"),
              aide: _t("Cliquer une forme ou un lien pour l'enlever du plan.") },
        ];
    }

    get aideOutil() {
        const o = this.outils.find((x) => x.code === this.state.outil);
        return o ? o.aide : "";
    }

    get palette() {
        const d = this.state.donnees;
        if (!d) {
            return [];
        }
        return this.state.sorte === "zone" ? d.palette.zones : d.palette.elements;
    }

    choisirOutil(code) {
        this.state.outil = code;
        this.state.depuis = null;
        this.state.fantome = null;
    }

    choisirSorte(ev) {
        this.state.sorte = ev.target.value;
        this.state.genre = this.state.sorte === "zone" ? "bureau" : "poste";
    }

    /** Un événement de souris, en centimètres du plan. */
    _modele(ev) {
        const r = this.svgRef.el.getBoundingClientRect();
        return { x: (ev.clientX - r.left) / this.state.zoom,
                 y: (ev.clientY - r.top) / this.state.zoom };
    }

    _caler(v) {
        const pas = this.state.donnees.pas || 25;
        return Math.round(v / pas) * pas;
    }

    async _ecrire(methode, args) {
        if (this.state.occupe) {
            return false;
        }
        this.state.occupe = true;
        try {
            this.state.donnees = await this.orm.call(
                "bf.floorplan", methode, [this.resId, ...args], this.contexteSurligne);
            return true;
        } catch (e) {
            this.notification.add(messageDe(e), { type: "warning", sticky: false });
            return false;
        } finally {
            this.state.occupe = false;
            this.state.fantome = null;
            this.state.depuis = null;
        }
    }

    debutDeplacement(f, ev) {
        if (this.state.outil !== "deplacer" || !this.modifiable || ev.button !== 0) {
            return;
        }
        ev.stopPropagation();
        const p = this._modele(ev);
        this.deplacement = { sorte: f.sorte, id: f.id, x: f.x, y: f.y, w: f.w, h: f.h,
                             ox: p.x, oy: p.y, bouge: false };
    }

    surDeplacement(ev) {
        const d = this.deplacement;
        if (!d) {
            return;
        }
        const p = this._modele(ev);
        const dx = this._caler(p.x - d.ox);
        const dy = this._caler(p.y - d.oy);
        if (dx || dy) {
            d.bouge = true;
        }
        this.state.fantome = { x: d.x + dx, y: d.y + dy, w: d.w, h: d.h };
    }

    async finDeplacement(ev) {
        const d = this.deplacement;
        this.deplacement = null;
        if (!d || !d.bouge) {
            this.state.fantome = null;
            return;
        }
        const p = this._modele(ev);
        await this._ecrire("deplacer", [d.sorte, d.id, d.x + (p.x - d.ox), d.y + (p.y - d.oy)]);
    }

    debutRedimension(f, ev) {
        if (this.state.outil !== "deplacer" || !this.modifiable || ev.button !== 0) {
            return;
        }
        ev.stopPropagation();
        const p = this._modele(ev);
        this.redimension = { sorte: f.sorte, id: f.id, x: f.x, y: f.y, w: f.w, h: f.h,
                             ox: p.x, oy: p.y, bouge: false };
    }

    surRedimension(ev) {
        const r = this.redimension;
        if (!r) {
            return;
        }
        const p = this._modele(ev);
        const pas = this.state.donnees.pas || 25;
        const w = Math.max(pas, this._caler(r.w + (p.x - r.ox)));
        const h = Math.max(pas, this._caler(r.h + (p.y - r.oy)));
        if (w !== r.w || h !== r.h) {
            r.bouge = true;
        }
        this.state.fantome = { x: r.x, y: r.y, w, h };
    }

    async finRedimension(ev) {
        const r = this.redimension;
        this.redimension = null;
        if (!r || !r.bouge) {
            this.state.fantome = null;
            return;
        }
        const p = this._modele(ev);
        await this._ecrire("redimensionner",
                           [r.sorte, r.id, r.w + (p.x - r.ox), r.h + (p.y - r.oy)]);
    }

    /** Un clic sur une forme : ce qu'il fait dépend de l'outil en main. */
    async surClicForme(f, ev) {
        if (this.state.outil === "consulter" || !this.modifiable) {
            this.ouvrir(f);
            return;
        }
        ev.stopPropagation();
        if (this.state.outil === "tourner") {
            if (f.sorte === "element") {
                await this._ecrire("tourner", [f.id]);
            }
        } else if (this.state.outil === "lier") {
            if (f.sorte !== "element") {
                return;
            }
            if (!this.state.depuis) {
                this.state.depuis = f;
                return;
            }
            const depuis = this.state.depuis;
            if (depuis.id !== f.id) {
                await this._ecrire("lier", [depuis.id, f.id, this.state.genreLien]);
            }
            this.state.depuis = null;
        } else if (this.state.outil === "retirer") {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Retirer du plan"),
                body: _t("« %s » sera retiré du plan, avec ses liens. L'opération est"
                         + " inscrite au fil du plan.", f.nom),
                confirmLabel: _t("Retirer"),
                confirm: () => this._ecrire("retirer", [f.sorte, f.id]),
                cancel: () => {},
            });
        }
    }

    async surClicLien(li, ev) {
        if (this.state.outil !== "retirer" || !this.modifiable) {
            return;
        }
        ev.stopPropagation();
        await this._ecrire("delier", [li.id]);
    }

    /** Un clic sur le fond : c'est là qu'on pose une forme. */
    async surClicFond(ev) {
        if (this.state.outil !== "poser" || !this.modifiable || ev.target.dataset.rid) {
            return;
        }
        const p = this._modele(ev);
        const ok = await this._ecrire("poser", [this.state.sorte, this.state.genre, p.x, p.y]);
        const neuf = ok && this.state.donnees.neuf;
        if (!neuf) {
            return;
        }
        // la fiche s'ouvre tout de suite : une forme posée doit être nommée
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: neuf.sorte === "zone" ? "bf.floorplan.zone" : "bf.floorplan.element",
            res_id: neuf.id,
            views: [[false, "form"]],
            target: "new",
        }, { onClose: () => this.charger() });
    }

    /** Un clic ouvre ce que la forme représente : la cible si elle en a une,
     *  la fiche sinon. */
    ouvrir(f) {
        if (f.sorte === "element" && f.cible) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: f.cible.modele,
                res_id: f.cible.id,
                views: [[false, "form"]],
                target: "new",
            }, { onClose: () => this.charger() });
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: f.sorte === "zone" ? "bf.floorplan.zone" : "bf.floorplan.element",
            res_id: f.id,
            views: [[false, "form"]],
            target: "new",
        }, { onClose: () => this.charger() });
    }

    infobulle(f) {
        if (f.sorte === "zone") {
            const parts = [f.genre_nom];
            if (f.capacite) {
                parts.push(_t("%s/%s places", f.occupes, f.capacite));
            }
            if (f.elements) {
                parts.push(_t("%s élément(s)", f.elements));
            }
            return parts.join(" · ");
        }
        const parts = [f.info];
        if (f.cible) {
            parts.push(_t("Ouvrir : %s", f.cible.nom));
        }
        if (f.liens) {
            parts.push(_t("%s lien(s)", f.liens));
        }
        return parts.join(" · ");
    }

    /** Le plan s'emporte en SVG, fond compris : l'image est incorporée. */
    async telecharger() {
        const svg = this.svgRef.el;
        if (!svg) {
            return;
        }
        const copie = svg.cloneNode(true);
        copie.setAttribute("xmlns", "http://www.w3.org/2000/svg");
        copie.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
        const image = copie.querySelector("image");
        if (image) {
            try {
                const rep = await fetch(image.getAttribute("href"));
                const blob = await rep.blob();
                const dataUrl = await new Promise((res) => {
                    const fr = new FileReader();
                    fr.onload = () => res(fr.result);
                    fr.readAsDataURL(blob);
                });
                image.setAttribute("href", dataUrl);
            } catch {
                image.remove();
            }
        }
        const blob = new Blob(['<?xml version="1.0" encoding="UTF-8"?>', copie.outerHTML],
                              { type: "image/svg+xml" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${this.state.donnees.titre.replace(/[^\w -]+/g, "")}.svg`;
        a.click();
        URL.revokeObjectURL(url);
    }

    // --- tracé ----------------------------------------------------------------

    get zones() {
        const d = this.state.donnees;
        return d ? d.zones.map((z) => ({ ...z, sorte: "zone" })) : [];
    }

    get elements() {
        const d = this.state.donnees;
        return d ? d.elements.map((e) => ({ ...e, sorte: "element" })) : [];
    }

    get liens() {
        const d = this.state.donnees;
        return d ? d.liens : [];
    }

    couleurLien(li) {
        return COULEUR_LIEN[li.genre] || INK;
    }

    contour(e) {
        return TEINTE[e.teinte] || INK;
    }

    fond(e) {
        return FOND_ELEMENT[e.genre] || "#FFFFFF";
    }

    rayon(e) {
        return RONDS.includes(e.genre) ? Math.min(e.w, e.h) / 2 : 3;
    }

    transform(e) {
        return e.rot ? `rotate(${e.rot} ${e.x + e.w / 2} ${e.y + e.h / 2})` : undefined;
    }

    /** La taille d'une étiquette, lue sur la forme : lisible sur un poste,
     *  discrète sur une prise. */
    taille(e) {
        return Math.max(9, Math.min(16, Math.min(e.w, e.h) / 3.5));
    }

    /** L'étiquette tient dans la forme, ou se pose dessous. */
    etiquette(e) {
        const taille = this.taille(e);
        const largeur = couper._ctx ? this._mesurer(e.nom, taille) : e.nom.length * taille * 0.58;
        const dedans = e.h >= taille * 1.6 && largeur <= e.w - 6;
        return {
            taille, dedans,
            x: e.x + e.w / 2,
            y: dedans ? e.y + e.h / 2 + taille / 3 : e.y + e.h + taille,
            occupant: dedans && e.h >= taille * 3.2,
        };
    }

    _mesurer(texte, taille) {
        couper._ctx.font = `400 ${taille}px Lexend, sans-serif`;
        return couper._ctx.measureText(texte).width;
    }

    /** Une poignée de redimensionnement, dessinée en unités du plan mais
     *  toujours de la même taille à l'écran. */
    poignee(f) {
        const c = 10 / this.state.zoom;
        return { x: f.x + f.w - c, y: f.y + f.h - c, c };
    }
}

registry.category("view_widgets").add("bf_floorplan_visualiseur", {
    component: VisualiseurPlan,
});
