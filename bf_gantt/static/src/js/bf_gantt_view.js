/** @odoo-module **/

/**
 * L'échéancier dans le back-office.
 *
 * Le composant ne calcule aucune coordonnée : il trace ce que `bf.gantt.source`
 * a positionné, exactement comme le PDF et le SVG. Un rectangle mal placé ici
 * serait mal placé partout, ce qui est le comportement voulu : un seul endroit
 * à corriger.
 */

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { GanttStore, ECHELLES, ZOOMS } from "./bf_gantt_model";

export class BfGanttView extends Component {
    static template = "bf_gantt.Vue";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.echelles = ECHELLES;
        this.zooms = ZOOMS;
        this.store = new GanttStore(this.orm, this.notification);
        this.state = useState(this.store.state);

        const params = this.props.action?.params || {};
        const ctx = this.props.action?.context || {};

        onWillStart(async () => {
            await this.store.chargerPortefeuille();
            const kind = params.kind || ctx.default_bf_gantt_kind || "project";
            let resId = params.res_id || ctx.default_bf_gantt_id || null;
            if (!resId) {
                const liste = kind === "plan"
                    ? this.state.portefeuille.plans
                    : this.state.portefeuille.projects;
                resId = liste.length ? liste[0].id : null;
            }
            if (params.grouping) {
                this.state.grouping = params.grouping;
            }
            if (resId) {
                await this.store.choisir(kind, resId);
            } else {
                this.state.loading = false;
            }
        });
    }

    // ------------------------------------------------------------------ vues

    get geo() {
        return this.state.geometrie;
    }

    get groupings() {
        const tout = this.state.portefeuille.groupings || {};
        return tout[this.state.kind] || [];
    }

    get cibles() {
        return this.state.kind === "plan"
            ? this.state.portefeuille.plans
            : this.state.portefeuille.projects;
    }

    /** La boîte du SVG. Le `viewBox` reste la géométrie : c'est du vectoriel,
     *  donc net à n'importe quel facteur. */
    get boite() {
        const g = this.geo;
        if (!g) {
            return { w: 0, h: 0 };
        }
        return { w: g.largeur * this.state.zoom, h: g.hauteur * this.state.zoom };
    }

    get vide() {
        return !this.state.loading && (!this.geo || !this.geo.lignes.length);
    }

    /** Le chemin SVG d'une flèche, construit une fois par rendu. */
    cheminFleche(fleche) {
        return fleche.points
            .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
            .join(" ");
    }

    pointeFleche(fleche) {
        const [x, y] = fleche.pointe;
        return `M${x.toFixed(1)},${y.toFixed(1)} L${(x - 4).toFixed(1)},${(y - 2.2).toFixed(1)} L${(x - 4).toFixed(1)},${(y + 2.2).toFixed(1)} Z`;
    }

    diamant(ligne) {
        const d = ligne.diamant;
        return `M${d.cx},${d.cy - d.r} L${d.cx + d.r},${d.cy} L${d.cx},${d.cy + d.r} L${d.cx - d.r},${d.cy} Z`;
    }

    // --------------------------------------------------------------- actions

    async onChangerCible(ev) {
        const id = parseInt(ev.target.value, 10);
        if (!Number.isNaN(id)) {
            await this.store.choisir(this.state.kind, id);
        }
    }

    async onChangerGenre(kind) {
        const liste = kind === "plan"
            ? this.state.portefeuille.plans
            : this.state.portefeuille.projects;
        if (!liste.length) {
            this.notification.add(
                kind === "plan"
                    ? _t("Aucun échéancier autonome pour l'instant.")
                    : _t("Aucun projet ouvert."),
                { type: "info" }
            );
            return;
        }
        this.state.kind = kind;
        this.state.grouping = kind === "plan" ? "lane" : "stage";
        await this.store.choisir(kind, liste[0].id);
    }

    async onChangerGrouping(ev) {
        await this.store.setGrouping(ev.target.value);
    }

    async onChangerEchelle(key) {
        await this.store.setEchelle(key);
    }

    onChangerZoom(ev) {
        this.store.setZoom(parseFloat(ev.target.value));
    }

    /** Ctrl + molette sur le dessin : le geste que tout le monde essaie. */
    onMolette(ev) {
        if (!ev.ctrlKey && !ev.metaKey) {
            return;
        }
        ev.preventDefault();
        const pas = ev.deltaY < 0 ? 0.25 : -0.25;
        const cible = Math.round((this.state.zoom + pas) * 100) / 100;
        this.store.setZoom(Math.max(0.6, Math.min(3, cible)));
    }

    async onActualiser() {
        this.store.cache.clear();
        await this.store.charger({ force: true });
    }

    async onOuvrir(ref) {
        const action = await this.orm.call(
            "bf.gantt.source", "action_ouvrir_tache", [ref]
        );
        if (action) {
            this.action.doAction(action);
        }
    }

    onSurvol(ligne, ev) {
        const detail = this.store.detail(ligne.ref);
        this.state.survol = {
            ligne,
            detail,
            x: ev.clientX,
            y: ev.clientY,
        };
    }

    onQuitter() {
        this.state.survol = null;
    }

    async onExporter(format) {
        const s = this.state;
        if (!s.resId) {
            return;
        }
        this.notification.add(_t("Préparation du fichier…"), { type: "info" });
        try {
            const res = await this.orm.call(
                "bf.gantt.export",
                "telecharger",
                [s.kind, s.resId, format],
                // Le zoom suit le PNG et le SVG, pas le PDF : une page
                // d'impression agrandie ne dit plus la vérité sur ses dimensions.
                { echelle: s.echelle, grouping: s.grouping, zoom: s.zoom }
            );
            // Le contenu revient en base64 : on le rend au navigateur sans
            // passer par une route de plus, donc sans deuxième contrôle d'accès
            // à écrire et à maintenir.
            const octets = Uint8Array.from(atob(res.content), (c) => c.charCodeAt(0));
            const blob = new Blob([octets], { type: res.mimetype });
            const url = URL.createObjectURL(blob);
            const lien = document.createElement("a");
            lien.href = url;
            lien.download = res.name;
            document.body.appendChild(lien);
            lien.click();
            document.body.removeChild(lien);
            URL.revokeObjectURL(url);
        } catch (e) {
            console.error("Échéancier : export refusé", e);
            this.notification.add(_t("Le fichier n'a pas pu être produit."), {
                type: "danger",
                title: _t("Export"),
            });
        }
    }

    async onJoindre(format) {
        const s = this.state;
        if (!s.resId) {
            return;
        }
        try {
            const res = await this.orm.call(
                "bf.gantt.export",
                "joindre",
                [s.kind, s.resId, format],
                { echelle: s.echelle, grouping: s.grouping }
            );
            this.notification.add(
                _t("Déposé au dossier : %s", res.name),
                { type: "success" }
            );
        } catch (e) {
            console.error("Échéancier : dépôt refusé", e);
            this.notification.add(_t("Le fichier n'a pas pu être déposé."), {
                type: "danger",
            });
        }
    }
}

registry.category("actions").add("bf_gantt", BfGanttView);
