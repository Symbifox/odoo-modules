/** @odoo-module **/

/**
 * L'état de l'échéancier, et rien d'autre.
 *
 * Ce fichier ne dessine pas et ne calcule aucune coordonnée : la géométrie est
 * celle du serveur, la même que le PDF et le SVG. Il garde en mémoire ce qui a
 * déjà été demandé pour qu'un changement d'échelle ne rappelle pas le serveur
 * deux fois pour le même état.
 */

import { reactive } from "@odoo/owl";

export const ECHELLES = [
    { key: "day", label: "Jour" },
    { key: "week", label: "Semaine" },
    { key: "month", label: "Mois" },
];

// Le tracé est calibré pour l'impression : à 1:1 il est illisible à l'écran.
// Le zoom n'étire que la boîte du SVG, le repère ne bouge pas, donc rien n'est
// recalculé et rien ne se pixellise. Il ne fait PAS partie de la clé de cache :
// changer de zoom ne redemande rien au serveur.
export const ZOOMS = [1, 1.25, 1.5, 2, 2.5].map(
    (v) => ({ value: v, label: `${Math.round(v * 100)} %` })
);
export const ZOOM_DEFAUT = 1.5;
const CLE_ZOOM = "bf_gantt.zoom";

function zoomRetenu() {
    try {
        const brut = parseFloat(window.localStorage.getItem(CLE_ZOOM));
        return Number.isFinite(brut) && brut >= 0.6 && brut <= 3 ? brut : ZOOM_DEFAUT;
    } catch {
        // Navigation privée, stockage refusé : le défaut fait le travail.
        return ZOOM_DEFAUT;
    }
}

export class GanttStore {
    constructor(orm, notification) {
        this.orm = orm;
        this.notification = notification;
        this.cache = new Map();
        this.state = reactive({
            loading: true,
            portefeuille: { projects: [], plans: [], groupings: [] },
            kind: "project",
            resId: null,
            grouping: "stage",
            echelle: "week",
            zoom: zoomRetenu(),
            geometrie: null,
            erreur: null,
            survol: null,
        });
    }

    get cle() {
        const s = this.state;
        return `${s.kind}:${s.resId}:${s.grouping}:${s.echelle}`;
    }

    async chargerPortefeuille() {
        try {
            this.state.portefeuille = await this.orm.call(
                "bf.gantt.source", "get_portefeuille", []
            );
        } catch (e) {
            console.error("Échéancier : portefeuille illisible", e);
            this.state.portefeuille = { projects: [], plans: [], groupings: [] };
        }
    }

    /**
     * Charge la géométrie de l'état courant. `force` ignore le cache, ce qui est
     * exactement ce que fait le bouton Actualiser : sans lui, il ne ferait rien
     * de visible et mentirait à l'usager.
     */
    async charger({ force = false } = {}) {
        const s = this.state;
        if (!s.resId) {
            s.geometrie = null;
            s.loading = false;
            return;
        }
        const cle = this.cle;
        if (!force && this.cache.has(cle)) {
            s.geometrie = this.cache.get(cle);
            s.erreur = null;
            s.loading = false;
            return;
        }
        s.loading = true;
        s.erreur = null;
        try {
            const geometrie = await this.orm.call(
                "bf.gantt.source",
                "get_geometrie",
                [s.kind, s.resId],
                { grouping: s.grouping, echelle: s.echelle }
            );
            this.cache.set(cle, geometrie);
            s.geometrie = geometrie;
        } catch (e) {
            console.error("Échéancier : chargement refusé", e);
            s.geometrie = null;
            s.erreur = e?.data?.message || e?.message || "Chargement impossible.";
        } finally {
            s.loading = false;
        }
    }

    /** Un changement de cible vide le cache : les clés d'un autre projet ne
     *  servent à rien et garder tout en mémoire finit par peser. */
    async choisir(kind, resId) {
        this.cache.clear();
        this.state.kind = kind;
        this.state.resId = resId;
        if (kind === "plan" && this.state.grouping === "stage") {
            // Un plan autonome n'a pas d'étape de projet : ses couloirs sont
            // ses propres libellés. On y retombe au lieu d'afficher un vide.
            this.state.grouping = "lane";
        }
        await this.charger();
    }

    async setGrouping(grouping) {
        this.state.grouping = grouping;
        await this.charger();
    }

    async setEchelle(echelle) {
        this.state.echelle = echelle;
        await this.charger();
    }

    setZoom(zoom) {
        this.state.zoom = zoom;
        try {
            window.localStorage.setItem(CLE_ZOOM, String(zoom));
        } catch {
            // Rien à faire : le zoom vaut pour cette page, et c'est déjà utile.
        }
    }

    detail(ref) {
        return this.state.geometrie?.details?.[ref] || null;
    }
}
