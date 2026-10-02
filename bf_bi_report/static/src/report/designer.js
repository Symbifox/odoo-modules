/** @odoo-module */
// Volets du concepteur : Visualisations, Champs du visuel (puits), Champs (mesures et
// dimensions). Glisser un champ dans un puits, ou cliquer dessus pour l'ajouter au visuel
// sélectionné. Toutes les modifications passent par les rappels du rapport, qui enregistre.

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

// Les icônes sont dessinées dans le gabarit (bf_bi_report.TypeIcon) : aucun balisage injecté.
export const VISUAL_TYPES = [
    ["kpi", _t("Tile")],
    ["column", _t("Column chart")],
    ["bar", _t("Bar chart")],
    ["line", _t("Line chart")],
    ["donut", _t("Donut")],
    ["table", _t("Data table")],
    ["gauge", _t("Gauge")],
    ["waterfall", _t("Waterfall")],
    ["heatmap", _t("Heat map")],
];
export const RICH_LIMIT = 15;
export const HEAT_COLUMNS = [
    ["month", _t("Month")],
    ["quarter", _t("Quarter")],
];
export const DIMENSIONS = [
    ["month", _t("Month")],
    ["quarter", _t("Quarter")],
    ["year", _t("Year")],
    ["customer", _t("Customer")],
];
export const NEEDS_DIMENSION = new Set(["column", "bar", "line", "donut", "table", "waterfall", "heatmap"]);
export const TIME_DIMENSIONS = new Set(["month", "quarter", "year"]);

export function isComplete(v) {
    if (!v.measures || !v.measures.length) {
        return false;
    }
    if (NEEDS_DIMENSION.has(v.type) && !v.dimension) {
        return false;
    }
    if (v.type === "heatmap" && v.dimension !== "customer") {
        return false;
    }
    return !(v.type === "line" && v.dimension === "customer");
}

export function missingHint(v) {
    if (!v.measures || !v.measures.length) {
        return _t("Drag a named measure into Values.");
    }
    if (v.type === "line" && v.dimension === "customer") {
        return _t("A line follows time: choose Month, Quarter or Year as Axis.");
    }
    if (v.type === "heatmap") {
        return _t("Drag Customer into Rows.");
    }
    return _t("Drag a dimension into Axis.");
}

export class DesignPanes extends Component {
    static template = "bf_bi_report.DesignPanes";
    static props = ["visual", "fields", "page", "canAdd", "choices", "onChange", "onAdd", "onDelete", "onRenamePage", "onPageSettings",
                    "theme", "themeChoices", "onTheme"];

    setup() {
        this.types = VISUAL_TYPES;
        this.dimensions = DIMENSIONS;
        this.heatColumns = HEAT_COLUMNS;
        this.over = useState({ well: null });
    }

    get groups() {
        const groups = new Map();
        for (const f of this.props.fields) {
            if (!groups.has(f.group)) {
                groups.set(f.group, []);
            }
            groups.get(f.group).push(f);
        }
        return [...groups.entries()].map(([name, items]) => ({ name, items }));
    }
    dimensionLabel(key) {
        const d = DIMENSIONS.find((x) => x[0] === key);
        return d ? d[1] : key;
    }
    typeLabel(key) {
        const t = VISUAL_TYPES.find((x) => x[0] === key);
        return t ? t[1] : key;
    }
    removeLabel(name) {
        return _t("Remove %s", name);
    }
    get fullPageTitle() {
        return _t("A page holds at most 24 visuals.");
    }
    fieldOf(code) {
        return this.props.fields.find((f) => f.code === code) || { code, name: code, unit: "number" };
    }
    get multi() {
        return this.props.visual && this.props.visual.type === "table";
    }
    get showAxis() {
        return this.props.visual && NEEDS_DIMENSION.has(this.props.visual.type);
    }
    get showTarget() {
        return this.props.visual && ["kpi", "table", "gauge"].includes(this.props.visual.type);
    }
    get showMax() {
        return this.props.visual && this.props.visual.type === "gauge";
    }
    get isHeatmap() {
        return this.props.visual && this.props.visual.type === "heatmap";
    }
    get isRich() {
        return this.props.visual && ["waterfall", "heatmap"].includes(this.props.visual.type);
    }
    get limitMax() {
        return this.isRich ? RICH_LIMIT : 50;
    }
    get showLimit() {
        return this.props.visual && this.props.visual.dimension === "customer" && !["kpi", "gauge"].includes(this.props.visual.type);
    }
    get additiveWarning() {
        const v = this.props.visual;
        return Boolean(v && v.type === "waterfall" && v.measures.some((c) => this.fieldOf(c).additive === false));
    }
    get maxValue() {
        const m = this.props.visual.max;
        if (!m) {
            return "";
        }
        return this.targetIsPercent ? Math.round(m * 1000) / 10 : m;
    }
    get targetIsPercent() {
        const v = this.props.visual;
        const first = v && v.measures && v.measures.find((c) => this.fieldOf(c).unit === "percent");
        return Boolean(first);
    }
    get targetValue() {
        const t = this.props.visual.target;
        if (t === undefined || t === null) {
            return "";
        }
        return this.targetIsPercent ? Math.round(t * 1000) / 10 : t;
    }
    get partnerWarning() {
        const v = this.props.visual;
        if (!v || v.dimension !== "customer") {
            return false;
        }
        return v.measures.some((c) => !this.fieldOf(c).has_partner);
    }
    get dateWarning() {
        const v = this.props.visual;
        if (!v || !TIME_DIMENSIONS.has(v.dimension)) {
            return false;
        }
        return v.measures.some((c) => !this.fieldOf(c).has_date);
    }

    // -------------------------------------------------- gestes
    patch(values) {
        this.props.onChange(values);
    }
    setType(type) {
        const v = this.props.visual;
        const values = { type };
        if (type !== "table" && v.measures.length > 1) {
            values.measures = v.measures.slice(0, 1);
        }
        if (type === "line" && (!v.dimension || v.dimension === "customer")) {
            values.dimension = "month";
        }
        if (["kpi", "gauge"].includes(type)) {
            values.dimension = null;
        }
        if (["gauge", "waterfall", "heatmap"].includes(type) && v.measures.length > 1) {
            values.measures = v.measures.slice(0, 1);
        }
        if (type === "heatmap") {
            values.dimension = "customer";
            values.columns = v.columns || "month";
        }
        if (type === "waterfall" && !v.dimension) {
            values.dimension = "customer";
        }
        if (["waterfall", "heatmap"].includes(type) && (v.limit || 10) > RICH_LIMIT) {
            values.limit = RICH_LIMIT;
        }
        this.patch(values);
    }
    addField(kind, key) {
        const v = this.props.visual;
        if (!v) {
            return;
        }
        // Un dépôt venu d'ailleurs (texte glissé d'une autre page) n'est pas un champ : ignoré.
        if (kind === "dim" ? !DIMENSIONS.some((d) => d[0] === key) : !this.props.fields.some((f) => f.code === key)) {
            return;
        }
        if (kind === "dim") {
            if (v.type === "heatmap" && key !== "customer") {
                return; // une carte thermique : les clients en lignes, le temps en colonnes
            }
            if (NEEDS_DIMENSION.has(v.type) && !(v.type === "line" && key === "customer")) {
                this.patch({ dimension: key });
            }
            return;
        }
        if (this.multi) {
            if (!v.measures.includes(key) && v.measures.length < 8) {
                this.patch({ measures: [...v.measures, key] });
            }
        } else {
            this.patch({ measures: [key] });
        }
    }
    removeMeasure(code) {
        this.patch({ measures: this.props.visual.measures.filter((c) => c !== code) });
    }
    onDragStart(ev, kind, key) {
        ev.dataTransfer.setData("text/plain", `${kind}:${key}`);
        ev.dataTransfer.effectAllowed = "copy";
    }
    onDragOver(ev, well) {
        ev.preventDefault();
        this.over.well = well;
    }
    onDragLeave() {
        this.over.well = null;
    }
    onDrop(ev, well) {
        ev.preventDefault();
        this.over.well = null;
        const [kind, key] = (ev.dataTransfer.getData("text/plain") || "").split(":");
        if ((well === "axis" && kind === "dim") || (well === "values" && kind === "measure")) {
            this.addField(kind, key);
        }
    }
    onTitle(ev) {
        this.patch({ title: ev.target.value.slice(0, 120) });
    }
    onTarget(ev) {
        const raw = ev.target.value.trim().replace(",", ".");
        if (!raw) {
            this.patch({ target: null });
            return;
        }
        const n = Number(raw);
        if (isFinite(n)) {
            this.patch({ target: this.targetIsPercent ? n / 100 : n });
        }
    }
    onLimit(ev) {
        const n = Math.max(1, Math.min(this.limitMax, parseInt(ev.target.value, 10) || 10));
        this.patch({ limit: n });
    }
    onMax(ev) {
        const raw = ev.target.value.trim().replace(",", ".");
        if (!raw) {
            this.patch({ max: null });
            return;
        }
        const n = Number(raw);
        if (isFinite(n) && n > 0) {
            this.patch({ max: this.targetIsPercent ? n / 100 : n });
        }
    }
    onColumns(ev) {
        this.patch({ columns: ev.target.value });
    }
    onPageName(ev) {
        this.props.onRenamePage(ev.target.value);
    }
    get presets() {
        return [["", _t("Follows the period slicer")], ["month", _t("Month")], ["quarter", _t("Quarter")],
                ["year", _t("This year")], ["last_year", _t("Last year")]];
    }
    onDrillToggle(ev) {
        this.props.onPageSettings({ drill: ev.target.checked ? "customer" : null });
    }
    onPreset(ev) {
        this.props.onPageSettings({ preset: ev.target.value || null });
    }
    isPageCustomer(id) {
        return (this.props.page.customers || []).includes(id);
    }
    togglePageCustomer(id) {
        const set = new Set(this.props.page.customers || []);
        set.has(id) ? set.delete(id) : set.add(id);
        this.props.onPageSettings({ customers: [...set].slice(0, 50) });
    }
}
