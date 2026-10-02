/** @odoo-module */
// Lecteur d'un rapport BI : segments en haut, visuels sur une grille de 12 colonnes, pages en
// onglets. Un clic sur une barre, une part ou une ligne filtre toute la page (filtrage croisé) ;
// le visuel cliqué garde ses données et met les autres marques en retrait, comme Power BI.
// Toutes les valeurs viennent du serveur (bf.bi.report.bf_query), calculées sous les droits de
// la personne qui regarde.

import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { DesignPanes, isComplete, missingHint } from "./designer";
import { buildTheme, inkOn, rampColor, themeStyle } from "./theme";

const SAVE_DELAY = 1500;
const MAX_VISUALS = 24;
const MAX_PAGES = 20;
const GRID_GAP = 12;
const ROW_HEIGHT = 76;

const PRESETS = [
    ["month", _t("Month")],
    ["quarter", _t("Quarter")],
    ["year", _t("This year")],
    ["last_year", _t("Last year")],
];
const RICH_LIMIT = 15;

function locale() {
    return (user.lang || "en_US").replace("_", "-");
}
function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
}
function withAlpha(color, alpha) {
    // « #2a78d6 » → même couleur, plus pâle : la marque en retrait d'un filtrage croisé.
    // En RGBA : le canevas de Chart.js ne comprend pas color-mix partout.
    const m = /^#([0-9a-f]{6})$/i.exec((color || "").trim());
    if (!m) {
        return color;
    }
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export function formatValue(value, unit, currency, compact = false) {
    if (value === null || value === undefined || !isFinite(value)) {
        return "—";
    }
    const loc = locale();
    const nf = (digits, v = value) =>
        new Intl.NumberFormat(loc, { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v);
    if (unit === "percent") {
        return nf(1, value * 100) + " %";
    }
    if (unit === "hours") {
        return (compact && Math.abs(value) >= 10000 ? nf(1, value / 1000) + " k" : nf(0)) + " h";
    }
    if (unit === "currency") {
        const a = Math.abs(value);
        const body = compact && a >= 1e6 ? nf(2, value / 1e6) + " M" : compact && a >= 1e4 ? nf(0, value / 1e3) + " k" : nf(compact || a >= 1000 ? 0 : 2);
        const symbol = (currency && currency.symbol) || "$";
        return currency && currency.position === "before" ? symbol + " " + body : body + " " + symbol;
    }
    return nf(Math.abs(value) >= 100 ? 0 : 1);
}

function monthLabel(key) {
    // « 2026-03 » → « mars 26 » / « Mar 26 », dans la langue de la personne.
    if (!/^\d{4}-\d{2}/.test(key)) {
        return key;
    }
    const d = new Date(Number(key.slice(0, 4)), Number(key.slice(5, 7)) - 1, 1);
    return new Intl.DateTimeFormat(locale(), { month: "short", year: "2-digit" }).format(d);
}

function delta(cur, prev, comparison) {
    if (cur === null || prev === null || cur === undefined || prev === undefined) {
        return null;
    }
    if (comparison === "difference") {
        return { value: cur - prev, points: true };
    }
    if (!prev) {
        return null;
    }
    return { value: ((cur - prev) / Math.abs(prev)) * 100, points: false };
}

function changeOf(result, meta) {
    if (!result || !result.previous) {
        return null;
    }
    const d = delta(result.total[0], result.previous[0], meta.comparison);
    if (!d) {
        return null;
    }
    const scale = d.points && meta.unit === "percent" ? 100 : 1;
    const v = d.value * scale;
    const nf = new Intl.NumberFormat(locale(), { maximumFractionDigits: 1, minimumFractionDigits: d.points ? 1 : 0 });
    return {
        cls: Math.abs(v) < 0.5 ? "flat" : v > 0 ? "up" : "down",
        text: (v > 0 ? "▲ " : v < 0 ? "▼ " : "") + nf.format(Math.abs(v)) + (d.points ? " " + _t("pts") : " %"),
    };
}

function niceMax(v) {
    // 1, 2, 2,5 ou 5 × 10^k : une borne de jauge qui se lit.
    if (!(v > 0)) {
        return 1;
    }
    const p = 10 ** Math.floor(Math.log10(v));
    return [1, 2, 2.5, 5, 10].map((k) => k * p).find((m) => m >= v);
}

// ---------------------------------------------------------------- tuile
export class KpiVisual extends Component {
    static template = "bf_bi_report.KpiVisual";
    static props = ["visual", "result", "currency"];

    get meta() {
        return (this.props.result && this.props.result.measures[0]) || {};
    }
    get value() {
        return this.props.result ? this.props.result.total[0] : null;
    }
    get display() {
        return formatValue(this.value, this.meta.unit, this.props.currency);
    }
    get change() {
        return changeOf(this.props.result, this.meta);
    }
    get target() {
        const t = this.props.visual.target;
        if (t === undefined || t === null || this.value === null || this.value === undefined) {
            return null;
        }
        const ratio = Math.max(0, Math.min(1, t ? this.value / t : 0));
        return {
            width: Math.round(ratio * 100) + "%",
            reached: this.value >= t,
            label: formatValue(t, this.meta.unit, this.props.currency),
        };
    }
    get spark() {
        const trend = (this.props.result && this.props.result.trend) || [];
        const pts = trend.map((t, n) => [n, t.value]).filter(([, v]) => v !== null && v !== undefined);
        if (pts.length < 2) {
            return null;
        }
        const w = 96, h = 32, vals = pts.map(([, v]) => v);
        const lo = Math.min(...vals), hi = Math.max(...vals), sp = hi - lo || 1;
        const xy = pts.map(([n, v]) => [2 + (n * (w - 4)) / (trend.length - 1), h - 4 - ((v - lo) / sp) * (h - 8)]);
        const last = xy[xy.length - 1];
        return { w, h, points: xy.map((p) => p.join(",")).join(" "), cx: last[0], cy: last[1] };
    }
}

// ---------------------------------------------------------------- graphiques (Chart.js)
export class ChartVisual extends Component {
    static template = "bf_bi_report.ChartVisual";
    static props = ["visual", "result", "currency", "colorOf", "highlight", "onSelect", "onContext", "theme"];

    setup() {
        this.canvasRef = useRef("canvas");
        this.onContextMenu = (ev) => {
            if (!this.chart) {
                return;
            }
            if (this.props.visual.dimension !== "customer") {
                return;
            }
            const els = this.chart.getElementsAtEventForMode(ev, "nearest", { intersect: true }, true);
            if (els.length && this.props.onContext(this.rows[els[0].index], ev)) {
                ev.preventDefault();
            }
        };
        this.chart = null;
        onMounted(() => {
            this.draw();
            this.canvasRef.el && this.canvasRef.el.addEventListener("contextmenu", this.onContextMenu);
        });
        onPatched(() => this.draw());
        onWillUnmount(() => {
            this.canvasRef.el && this.canvasRef.el.removeEventListener("contextmenu", this.onContextMenu);
            this.chart && this.chart.destroy();
        });
    }
    get rows() {
        return (this.props.result && this.props.result.rows) || [];
    }
    labelOf(row) {
        return this.props.visual.dimension === "month" ? monthLabel(row.label) : row.label;
    }
    draw() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
        const el = this.canvasRef.el;
        if (!el || !window.Chart || !this.rows.length) {
            return;
        }
        const v = this.props.visual;
        const meta = this.props.result.measures[0] || {};
        const type = { column: "bar", bar: "bar", line: "line", donut: "doughnut" }[v.type];
        const ink = cssVar("--bfr-ink-2", "#4a5466"), grid = cssVar("--bfr-line", "#e2e6ec");
        const accent = this.props.theme.colors[0];
        const colors = this.rows.map((row) => {
            const base = v.dimension === "customer" ? this.props.colorOf(row.key) : accent;
            const dim = this.props.highlight !== null && this.props.highlight !== undefined && this.props.highlight !== row.key;
            return dim ? withAlpha(base, 0.28) : base;
        });
        const values = this.rows.map((row) => row.values[0]);
        const fmt = (val) => formatValue(val, meta.unit, this.props.currency, true);
        const dataset = {
            data: values,
            backgroundColor: v.type === "line" ? withAlpha(accent, 0.12) : colors,
            borderColor: v.type === "line" ? accent : v.type === "donut" ? cssVar("--bfr-canvas", "#fff") : colors,
            borderWidth: v.type === "line" ? 2 : v.type === "donut" ? 2 : 0,
            borderRadius: v.type === "donut" || v.type === "line" ? 0 : 4,
            fill: v.type === "line",
            tension: 0.25,
            pointRadius: v.type === "line" ? 3 : 0,
            pointBackgroundColor: accent,
            maxBarThickness: 48,
        };
        // Un « callback: undefined » remplacerait le formateur de Chart.js par rien : l'axe des
        // catégories afficherait 0, 1, 2… On ne pose le formateur que sur l'axe des valeurs.
        const valueTicks = { color: ink, callback: (val) => fmt(val) };
        const categoryTicks = { color: ink };
        if (v.type === "bar") {
            categoryTicks.callback = function (val) {
                const label = this.getLabelForValue(val);
                return label.length > 22 ? label.slice(0, 21) + "…" : label;
            };
        }
        const scales = v.type === "donut" ? {} : {
            x: { grid: { display: v.type === "bar", color: grid }, ticks: v.type === "bar" ? valueTicks : categoryTicks, border: { color: grid } },
            y: { grid: { display: v.type !== "bar", color: grid }, ticks: v.type === "bar" ? categoryTicks : valueTicks, border: { display: false }, beginAtZero: true },
        };
        this.chart = new window.Chart(el, {
            type,
            data: { labels: this.rows.map((r) => this.labelOf(r)), datasets: [dataset] },
            options: {
                indexAxis: v.type === "bar" ? "y" : "x",
                maintainAspectRatio: false,
                animation: false,
                cutout: v.type === "donut" ? "62%" : undefined,
                scales,
                plugins: {
                    legend: { display: v.type === "donut", position: "bottom", labels: { color: ink, boxWidth: 10, boxHeight: 10 } },
                    tooltip: { callbacks: { label: (ctx) => ` ${meta.name}: ${formatValue(ctx.raw, meta.unit, this.props.currency)}` } },
                },
                onClick: (_evt, elements) => {
                    if (elements.length) {
                        this.props.onSelect(this.rows[elements[0].index]);
                    }
                },
                onHover: (evt, elements) => {
                    evt.native.target.style.cursor = elements.length ? "pointer" : "default";
                },
            },
        });
    }
}

// ---------------------------------------------------------------- jauge
export class GaugeVisual extends Component {
    static template = "bf_bi_report.GaugeVisual";
    static props = ["visual", "result", "currency", "theme"];

    get meta() {
        return (this.props.result && this.props.result.measures[0]) || {};
    }
    get value() {
        return this.props.result ? this.props.result.total[0] : null;
    }
    get max() {
        const v = this.props.visual;
        if (v.max) {
            return v.max;
        }
        return niceMax(Math.max(this.value || 0, v.target || 0) * 1.15);
    }
    point(f, r) {
        // f de 0 (gauche) à 1 (droite) sur le demi-cercle de centre (100, 100).
        const a = Math.PI * (1 - Math.max(0, Math.min(1, f)));
        return [100 + r * Math.cos(a), 100 - r * Math.sin(a)];
    }
    arc(f) {
        const [x0, y0] = this.point(0, 78);
        const [x1, y1] = this.point(f, 78);
        return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A 78 78 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
    }
    get geometry() {
        const value = this.value;
        const f = value === null || value === undefined ? 0 : value / this.max;
        const t = this.props.visual.target;
        const g = { track: this.arc(1), fill: f > 0 ? this.arc(Math.min(f, 1)) : null, over: f > 1, tick: null };
        if (t !== undefined && t !== null) {
            const [x0, y0] = this.point(t / this.max, 64);
            const [x1, y1] = this.point(t / this.max, 92);
            g.tick = { x0, y0, x1, y1, reached: value !== null && value >= t };
        }
        return g;
    }
    get display() {
        return formatValue(this.value, this.meta.unit, this.props.currency);
    }
    get maxLabel() {
        return formatValue(this.max, this.meta.unit, this.props.currency, true);
    }
    get zeroLabel() {
        return formatValue(0, this.meta.unit, this.props.currency, true);
    }
    get targetLabel() {
        const t = this.props.visual.target;
        return t === undefined || t === null ? null : formatValue(t, this.meta.unit, this.props.currency);
    }
    get change() {
        return changeOf(this.props.result, this.meta);
    }
    get ariaLabel() {
        const parts = [`${this.meta.name || ""}: ${this.display}`];
        if (this.targetLabel) {
            parts.push(_t("target %s", this.targetLabel));
        }
        return parts.join(", ");
    }
}

// ---------------------------------------------------------------- cascade (Chart.js, barres flottantes)
export class WaterfallVisual extends Component {
    static template = "bf_bi_report.WaterfallVisual";
    static props = ["visual", "result", "currency", "theme", "highlight", "onSelect", "onContext"];

    setup() {
        this.canvasRef = useRef("canvas");
        this.chart = null;
        this.onContextMenu = (ev) => {
            if (!this.chart || this.props.visual.dimension !== "customer") {
                return;
            }
            const els = this.chart.getElementsAtEventForMode(ev, "nearest", { intersect: true }, true);
            const row = els.length ? this.bars[els[0].index].row : null;
            if (row && this.props.onContext(row, ev)) {
                ev.preventDefault();
            }
        };
        onMounted(() => {
            this.draw();
            this.canvasRef.el && this.canvasRef.el.addEventListener("contextmenu", this.onContextMenu);
        });
        onPatched(() => this.draw());
        onWillUnmount(() => {
            this.canvasRef.el && this.canvasRef.el.removeEventListener("contextmenu", this.onContextMenu);
            this.chart && this.chart.destroy();
        });
    }
    get meta() {
        return (this.props.result && this.props.result.measures[0]) || {};
    }
    get bars() {
        // Par client : période d'avant, un écart par client, période courante.
        // Par mois : chaque mois s'ajoute au précédent, puis le total.
        const r = this.props.result;
        if (!r || !r.measures.length) {
            return [];
        }
        const bars = [];
        let run = 0;
        if (this.props.visual.dimension === "customer") {
            run = r.previous[0] || 0;
            bars.push({ label: _t("Previous period"), from: 0, to: run, kind: "total", row: null });
            for (const row of r.rows) {
                bars.push({ label: row.label, from: run, to: run + row.delta, kind: row.delta >= 0 ? "up" : "down", row, delta: row.delta });
                run += row.delta;
            }
            bars.push({ label: _t("This period"), from: 0, to: r.total[0] || 0, kind: "total", row: null });
        } else {
            for (const row of r.rows) {
                const v = row.values[0] || 0;
                const label = this.props.visual.dimension === "month" ? monthLabel(row.label) : row.label;
                bars.push({ label, from: run, to: run + v, kind: v >= 0 ? "up" : "down", row, delta: v });
                run += v;
            }
            bars.push({ label: _t("Total"), from: 0, to: run, kind: "total", row: null });
        }
        return bars;
    }
    get legend() {
        const t = this.props.theme;
        return [
            { label: _t("Increase"), color: t.up, sign: "+" },
            { label: _t("Decrease"), color: t.down, sign: "−" },
            { label: _t("Total"), color: t.total, sign: "" },
        ];
    }
    draw() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
        const el = this.canvasRef.el;
        const bars = this.bars;
        if (!el || !window.Chart || !bars.length) {
            return;
        }
        const t = this.props.theme;
        const meta = this.meta;
        const ink = cssVar("--bfr-ink-2", "#4a5466"), grid = cssVar("--bfr-line", "#e2e6ec");
        const fmt = (val, compact = true) => formatValue(val, meta.unit, this.props.currency, compact);
        const hl = this.props.highlight;
        const colors = bars.map((b) => {
            const base = b.kind === "total" ? t.total : b.kind === "up" ? t.up : t.down;
            return hl !== null && hl !== undefined && b.row && b.row.key !== hl ? withAlpha(base, 0.28) : base;
        });
        this.chart = new window.Chart(el, {
            type: "bar",
            data: {
                labels: bars.map((b) => b.label),
                datasets: [{ data: bars.map((b) => [b.from, b.to]), backgroundColor: colors, borderRadius: 3, maxBarThickness: 44, borderSkipped: false }],
            },
            options: {
                maintainAspectRatio: false,
                animation: false,
                scales: {
                    x: { grid: { display: false }, ticks: {
                        color: ink,
                        callback: function (val) {
                            const label = this.getLabelForValue(val);
                            return label.length > 16 ? label.slice(0, 15) + "…" : label;
                        },
                    }, border: { color: grid } },
                    y: { grid: { color: grid }, ticks: { color: ink, callback: (val) => fmt(val) }, border: { display: false } },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: (ctx) => {
                        const b = bars[ctx.dataIndex];
                        if (b.kind === "total") {
                            return ` ${fmt(b.to, false)}`;
                        }
                        const sign = b.delta > 0 ? "+" : b.delta < 0 ? "−" : "";
                        const lines = [` ${sign}${fmt(Math.abs(b.delta), false)}`];
                        if (b.row && b.row.previous) {
                            lines.push(` ${fmt(b.row.previous[0], false)} → ${fmt(b.row.values[0], false)}`);
                        }
                        return lines;
                    } } },
                },
                onClick: (_evt, elements) => {
                    const b = elements.length ? bars[elements[0].index] : null;
                    if (b && b.row) {
                        this.props.onSelect(b.row);
                    }
                },
                onHover: (evt, elements) => {
                    const b = elements.length ? bars[elements[0].index] : null;
                    evt.native.target.style.cursor = b && b.row && b.row.key !== "others" ? "pointer" : "default";
                },
            },
        });
    }
}

// ---------------------------------------------------------------- carte thermique
export class HeatmapVisual extends Component {
    static template = "bf_bi_report.HeatmapVisual";
    static props = ["visual", "result", "currency", "theme", "highlight", "onSelect", "onContext"];

    get meta() {
        return (this.props.result && this.props.result.measures[0]) || {};
    }
    get columns() {
        const cols = (this.props.result && this.props.result.columns) || [];
        // La granularité vient du serveur : en période d'un mois, des trimestres deviennent un mois.
        return cols.map((c) => ({ ...c, text: c.dim === "month" ? monthLabel(c.label) : c.label }));
    }
    get scale() {
        const vals = ((this.props.result && this.props.result.rows) || []).flatMap((r) => r.cells || []).filter((v) => v !== null && v !== undefined);
        const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
        return { lo, hi, span: hi - lo || 1 };
    }
    get rows() {
        const { lo, span } = this.scale;
        const hl = this.props.highlight;
        return ((this.props.result && this.props.result.rows) || []).map((row) => ({
            row,
            dim: hl !== null && hl !== undefined && row.key !== hl && !this.columns.some((c) => c.key === hl),
            cells: (row.cells || []).map((v, i) => {
                const empty = v === null || v === undefined;
                const bg = empty ? "transparent" : rampColor(this.props.theme, (v - lo) / span);
                const col = this.columns[i];
                return {
                    key: col ? col.key : i,
                    text: formatValue(v, this.meta.unit, this.props.currency, true),
                    style: `background:${bg};color:${empty ? "inherit" : inkOn(bg)}`,
                    dim: hl !== null && hl !== undefined && col && this.columns.some((c) => c.key === hl) && col.key !== hl,
                    title: `${row.label} · ${col ? col.text : ""} : ${formatValue(v, this.meta.unit, this.props.currency)}`,
                };
            }),
        }));
    }
    get legend() {
        const { lo, hi } = this.scale;
        const stops = [0, 0.25, 0.5, 0.75, 1].map((k) => `${rampColor(this.props.theme, k)} ${k * 100}%`).join(", ");
        return {
            style: `background: linear-gradient(90deg, ${stops})`,
            lo: formatValue(lo, this.meta.unit, this.props.currency, true),
            hi: formatValue(hi, this.meta.unit, this.props.currency, true),
        };
    }
    selectRow(item) {
        this.props.onSelect(item.row);
    }
    selectColumn(col) {
        this.props.onSelect({ key: col.key, start: col.start, label: col.label, dim: col.dim });
    }
    context(ev, item) {
        if (this.props.onContext(item.row, ev)) {
            ev.preventDefault();
        }
    }
}

// ---------------------------------------------------------------- tableau
export class TableVisual extends Component {
    static template = "bf_bi_report.TableVisual";
    static props = ["visual", "result", "currency", "colorOf", "highlight", "onSelect", "onContext", "canDrill", "onDrill"];

    get rows() {
        const r = this.props.result;
        if (!r) {
            return [];
        }
        const first = r.rows.map((row) => row.values[0] || 0);
        const max = Math.max(1, ...first.map(Math.abs));
        return r.rows.map((row) => ({
            row,
            label: this.props.visual.dimension === "month" ? monthLabel(row.label) : row.label,
            color: this.props.visual.dimension === "customer" ? this.props.colorOf(row.key) : null,
            dim: this.props.highlight !== null && this.props.highlight !== undefined && this.props.highlight !== row.key,
            bar: Math.round((Math.abs(row.values[0] || 0) / max) * 100),
            cells: row.values.map((val, i) => this.cell(val, r.measures[i] || {})),
        }));
    }
    cell(value, meta) {
        const text = formatValue(value, meta.unit, this.props.currency);
        const target = this.props.visual.target;
        if (meta.unit === "percent" && target !== undefined && value !== null && value !== undefined) {
            const level = value >= target ? "good" : value >= target * 0.75 ? "warn" : "bad";
            return { text, level, icon: level === "good" ? "▲" : level === "warn" ? "●" : "▼" };
        }
        return { text, level: null };
    }
    select(item) {
        this.props.onSelect(item.row);
    }
    context(ev, item) {
        if (this.props.visual.dimension === "customer" && this.props.onContext(item.row, ev)) {
            ev.preventDefault();
        }
    }
    drill(ev, item) {
        ev.stopPropagation();
        this.props.onDrill(item.row);
    }
    drillLabel(item) {
        return _t("Detail of %s", item.label);
    }
    get showDrill() {
        return this.props.canDrill && this.props.visual.dimension === "customer";
    }
}

// ---------------------------------------------------------------- le rapport
export class ReportAction extends Component {
    static template = "bf_bi_report.ReportAction";
    static components = { KpiVisual, ChartVisual, TableVisual, GaugeVisual, WaterfallVisual, HeatmapVisual, DesignPanes };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.presets = PRESETS;
        this.colorIndex = new Map(); // client → rang de couleur, fixé à la première rencontre
        this.themeCache = { key: null, theme: null };
        this.ticket = 0;
        this.choicesTicket = 0;
        this.visualTickets = {};
        this.saveTimer = null;
        this.inFlight = null; // la sauvegarde en cours : une seule à la fois, la suivante s'enchaîne
        this.pendingSave = false;
        this.epoch = 0; // change à chaque changement de page : une réponse d'une autre page est écartée
        this.requeryTimers = {};
        this.onBeforeUnload = (ev) => {
            if (["dirty", "saving"].includes(this.state.save.status)) {
                ev.preventDefault();
                ev.returnValue = "";
            }
        };
        window.addEventListener("beforeunload", this.onBeforeUnload);
        this.canvasRef = useRef("canvas");
        // Rappels stables : un rappel recréé à chaque rendu redessinerait chaque graphique.
        this.colorOfBound = (key) => this.colorOf(key);
        this.selectHandlers = {};
        this.state = useState({
            report: null, page: 0, preset: "year", customers: [], choices: [],
            selection: null, results: {}, loading: true, error: null,
            mode: "read", draft: null, selected: null, fields: [], revision: 0,
            save: { status: "saved", author: "", message: "" }, versions: null, renaming: null,
            drill: null, menu: null, partnerNames: {}, theme: null,
        });
        this.contextHandler = (row, ev) => this.openMenu(row, ev);
        this.lastPage = null; // la dernière page ordinaire vue : le Retour d'une extraction y ramène
        this.menuRef = useRef("menu");
        useEffect(
            (menu) => {
                const el = this.menuRef.el;
                if (!menu || !el) {
                    return;
                }
                // Mesuré après rendu : un nom long qui passe à la ligne ne déborde pas de l'écran.
                const r = el.getBoundingClientRect();
                el.style.left = `${Math.max(8, Math.min(menu.x, window.innerWidth - r.width - 8))}px`;
                el.style.top = `${Math.max(8, Math.min(menu.y, window.innerHeight - r.height - 8))}px`;
                const first = el.querySelector("button");
                first && first.focus();
            },
            () => [this.state.menu]
        );
        this.drillHandler = (row) => this.drillTo(row.key, row.label);
        const params = (this.props.action && this.props.action.params) || {};
        const ctx = (this.props.action && this.props.action.context) || {};
        this.reportId = params.report_id || ctx.active_id;
        this.openInDesign = Boolean(params.design);
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            if (!this.reportId) {
                this.state.error = _t("No report to open.");
                this.state.loading = false;
                return;
            }
            if (!(await this.loadReport())) {
                return;
            }
            await this.loadChoices();
            if (this.openInDesign && this.state.report.can_edit) {
                await this.enterDesign();
            } else {
                await this.refresh();
            }
        });
        onWillUnmount(() => {
            window.removeEventListener("beforeunload", this.onBeforeUnload);
            Object.values(this.requeryTimers).forEach(clearTimeout);
            this.flushSave(); // ne rien perdre en quittant : s'enchaîne après une sauvegarde en cours
        });
    }

    async loadReport() {
        try {
            this.state.report = await this.orm.call("bf.bi.report", "bf_get_report", [this.reportId]);
            this.state.revision = this.state.report.revision;
            this.loadPartnerNames();
            return true;
        } catch (error) {
            this.state.error = (error.data && error.data.message) || _t("This report cannot be opened.");
            this.state.loading = false;
            return false;
        }
    }

    get pages() {
        if (this.state.mode === "design" && this.state.draft) {
            return this.state.draft;
        }
        return (this.state.report && this.state.report.pages) || [];
    }
    get page() {
        return this.pages[this.state.page] || { visuals: [] };
    }
    get choiceMeasure() {
        // Les clients proposés (segment, page de détail, clients d'une page) viennent d'une mesure
        // déjà montrée par client : elle sait découper par client.
        for (const p of this.pages) {
            for (const v of p.visuals) {
                if (v.dimension === "customer" && v.measures && v.measures.length) {
                    return v.measures[0];
                }
            }
        }
        return this.firstMeasure;
    }
    get firstMeasure() {
        for (const p of this.pages) {
            for (const v of p.visuals) {
                if (v.measures && v.measures.length) {
                    return v.measures[0];
                }
            }
        }
        return null;
    }
    get canAddVisual() {
        return this.page.visuals.length < MAX_VISUALS;
    }
    get canAddPage() {
        return this.pages.length < MAX_PAGES;
    }
    get selectedVisual() {
        return this.page.visuals.find((v) => v.id === this.state.selected) || null;
    }
    get saveLabel() {
        return {
            saved: _t("Saved"), dirty: _t("Unsaved changes"), saving: _t("Saving…"),
            conflict: _t("Someone else saved this report"), error: _t("Not saved"),
        }[this.state.save.status];
    }
    get theme() {
        const report = this.state.report;
        const input = {
            name: this.state.mode === "design" && this.state.theme ? this.state.theme : (report && report.theme && report.theme.name) || "default",
            company_colors: (report && report.theme && report.theme.company_colors) || [],
        };
        const key = JSON.stringify(input);
        if (this.themeCache.key !== key) {
            this.themeCache = { key, theme: buildTheme(input) };
        }
        return this.themeCache.theme;
    }
    get themeStyle() {
        return themeStyle(this.theme);
    }
    colorOf(key) {
        const theme = this.theme;
        if (key === "others") {
            return theme.other;
        }
        if (!this.colorIndex.has(key)) {
            // La couleur suit le client, pas son rang : elle ne change pas d'un filtre à l'autre.
            this.colorIndex.set(key, this.colorIndex.size);
        }
        return theme.colors[this.colorIndex.get(key) % theme.colors.length];
    }
    selectHandler(v) {
        if (!this.selectHandlers[v.id]) {
            this.selectHandlers[v.id] = (row) => this.select(v.id, row);
        }
        return this.selectHandlers[v.id];
    }
    get pagePreset() {
        return this.page.preset || null;
    }
    get pageCustomers() {
        return (this.page.customers && this.page.customers.length && this.page.customers) || null;
    }
    get drillPages() {
        return this.pages.map((p, i) => ({ page: p, index: i })).filter((x) => x.page.drill === "customer");
    }
    get isDrillPage() {
        return this.page.drill === "customer";
    }
    get waitingDrill() {
        // Une page d'extraction ouverte sans client : on en choisit un.
        return this.isDrillPage && !this.state.drill && this.state.mode === "read";
    }
    presetLabel(key) {
        const p = PRESETS.find((x) => x[0] === key);
        return p ? p[1] : key;
    }
    filters(visual) {
        const sel = this.state.selection;
        const drill = this.isDrillPage && this.state.drill ? { dim: "customer", id: this.state.drill.id } : null;
        return {
            // Les réglages de la page passent avant les segments.
            period: { preset: this.pagePreset || this.state.preset },
            customers: this.pageCustomers || this.state.customers,
            selection: sel && sel.from !== visual.id ? sel.filter : null,
            drill,
        };
    }
    spec(visual) {
        const rich = ["waterfall", "heatmap"].includes(visual.type);
        const spec = {
            measures: visual.measures,
            dimension: ["kpi", "gauge"].includes(visual.type) ? null : visual.dimension || null,
            limit: rich ? Math.min(visual.limit || 10, RICH_LIMIT) : visual.limit || 10,
            trend: visual.type === "kpi",
        };
        if (rich) {
            spec.kind = visual.type;
        }
        if (visual.type === "heatmap") {
            spec.columns = visual.columns || "month";
        }
        return spec;
    }
    complete(v) {
        return isComplete(v);
    }
    hint(v) {
        return missingHint(v);
    }
    async loadChoices() {
        if (!this.choiceMeasure) {
            return;
        }
        const ticket = ++this.choicesTicket; // une réponse en retard ne remplace pas la plus récente
        try {
            const res = await this.orm.call("bf.bi.report", "bf_query", [
                { measures: [this.choiceMeasure], dimension: "customer", limit: 12 },
                { period: { preset: this.state.preset }, customers: [], selection: null },
            ]);
            if (ticket !== this.choicesTicket) {
                return;
            }
            this.state.choices = res.rows.filter((r) => r.key !== "others").map((r) => ({ id: r.key, name: r.label }));
            this.state.choices.forEach((c) => this.colorOf(c.id));
        } catch {
            this.state.choices = [];
        }
    }
    query(v) {
        return this.orm.call("bf.bi.report", "bf_query", [this.spec(v), this.filters(v)]).then(
            (r) => r,
            (error) => ({ failed: (error.data && error.data.message) || _t("This visual cannot be computed.") })
        );
    }
    async refresh() {
        const ticket = ++this.ticket;
        this.state.loading = true;
        const visuals = this.waitingDrill ? [] : this.page.visuals.filter((v) => isComplete(v));
        const answers = await Promise.all(visuals.map((v) => this.query(v).then((r) => [v.id, r])));
        if (ticket !== this.ticket) {
            return; // une réponse plus récente est déjà là
        }
        const results = {};
        for (const [id, r] of answers) {
            results[id] = r;
        }
        this.state.results = results;
        this.state.loading = false;
    }
    requery(v) {
        // Après une modification en conception : ce visuel seul, un peu plus tard.
        clearTimeout(this.requeryTimers[v.id]);
        const epoch = this.epoch;
        this.requeryTimers[v.id] = setTimeout(async () => {
            const ticket = (this.visualTickets[v.id] = (this.visualTickets[v.id] || 0) + 1);
            if (!isComplete(v)) {
                delete this.state.results[v.id];
                return;
            }
            const r = await this.query(v);
            // Les identifiants de visuels se répètent d'une page à l'autre (« v1 ») : une réponse
            // arrivée après un changement de page est écartée.
            if (ticket === this.visualTickets[v.id] && epoch === this.epoch) {
                this.state.results[v.id] = r;
            }
        }, 250);
    }

    // -------------------------------------------------- lecture
    async setPreset(preset) {
        this.state.preset = preset;
        this.state.loading = true; // dès le clic : la liste des clients se recharge d'abord
        if (this.state.selection && this.state.selection.dim !== "customer") {
            this.state.selection = null;
        }
        await this.loadChoices();
        await this.refresh();
    }
    toggleCustomer(id) {
        const set = new Set(this.state.customers);
        set.has(id) ? set.delete(id) : set.add(id);
        this.state.customers = [...set];
        if (this.state.selection && this.state.selection.dim === "customer" && this.state.customers.length
            && !set.has(this.state.selection.key)) {
            this.state.selection = null;
        }
        this.refresh();
    }
    isCustomerOn(id) {
        return !this.state.customers.length || this.state.customers.includes(id);
    }
    select(visualId, row) {
        if (this.state.mode === "design" || row.key === "others") {
            return; // en conception, un clic sélectionne le visuel, il ne filtre pas
        }
        const visual = this.page.visuals.find((v) => v.id === visualId);
        const sel = this.state.selection;
        if (sel && sel.from === visualId && sel.key === row.key) {
            this.state.selection = null;
        } else {
            const dim = row.dim || visual.dimension; // une colonne de carte thermique filtre sur le temps
            const filter = dim === "customer" ? { dim, id: row.key } : { dim, start: row.start };
            const label = dim === "month" ? monthLabel(row.label) : row.label;
            this.state.selection = { from: visualId, dim, key: row.key, label, filter };
        }
        this.refresh();
    }
    clearSelection() {
        this.state.selection = null;
        this.refresh();
    }
    highlightFor(visual) {
        const sel = this.state.selection;
        return sel && sel.from === visual.id ? sel.key : null;
    }
    newEpoch() {
        this.epoch++;
        Object.values(this.requeryTimers).forEach(clearTimeout);
        this.requeryTimers = {};
    }
    // -------------------------------------------------- extraction
    openMenu(row, ev) {
        if (this.state.mode === "design" || !row || row.key === "others" || !this.drillPages.length) {
            return false;
        }
        this.state.menu = { row, x: ev.clientX, y: ev.clientY }; // recadré après rendu (useEffect)
        return true;
    }
    get drillChoices() {
        // Les clients du segment, et celui de l'extraction s'il n'y est pas (hors des 12 premiers).
        const list = this.state.choices.slice();
        const d = this.state.drill;
        if (d && !list.some((c) => c.id === d.id)) {
            list.unshift({ id: d.id, name: d.name });
        }
        return list;
    }
    onDrillSwitch(ev) {
        const id = parseInt(ev.target.value, 10);
        const choice = this.drillChoices.find((c) => c.id === id);
        if (choice) {
            this.switchDrill(choice);
        }
    }
    get pageCustomerNames() {
        const names = new Map(this.state.choices.map((c) => [c.id, c.name]));
        return (this.pageCustomers || []).map((id) => names.get(id) || this.state.partnerNames[id] || `#${id}`);
    }
    get designerChoices() {
        const list = this.state.choices.slice();
        for (const id of this.page.customers || []) {
            if (!list.some((c) => c.id === id)) {
                list.push({ id, name: this.state.partnerNames[id] || `#${id}` });
            }
        }
        return list;
    }
    async loadPartnerNames() {
        // Les noms des clients fixés par une page qui ne sont pas dans le segment.
        const pages = [...((this.state.report && this.state.report.pages) || []), ...(this.state.draft || [])];
        const ids = [...new Set(pages.flatMap((p) => p.customers || []))].filter((id) => !(id in this.state.partnerNames));
        if (!ids.length) {
            return;
        }
        try {
            // search_read écarte ce que la personne ne peut pas lire, au lieu d'échouer pour tous.
            const rows = await this.orm.searchRead("res.partner", [["id", "in", ids]], ["display_name"]);
            rows.forEach((r) => (this.state.partnerNames[r.id] = r.display_name));
        } catch {
            // illisibles pour cette personne : le numéro suffit
        }
    }
    closeMenu() {
        this.state.menu = null;
    }
    onMenuKeydown(ev) {
        const items = [...ev.currentTarget.querySelectorAll("button")];
        const i = items.indexOf(document.activeElement);
        if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
            ev.preventDefault();
            const next = ev.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
            items[next].focus();
        } else if (ev.key === "Tab") {
            this.closeMenu();
        }
    }
    menuDrill(index) {
        const row = this.state.menu.row;
        this.state.menu = null;
        this.drillTo(row.key, row.label, index);
    }
    menuFilter() {
        const row = this.state.menu.row;
        this.state.menu = null;
        this.state.selection = { from: null, dim: "customer", key: row.key, label: row.label, filter: { dim: "customer", id: row.key } };
        this.refresh();
    }
    drillTo(id, name, pageIndex = null) {
        const target = pageIndex !== null ? pageIndex : (this.drillPages[0] && this.drillPages[0].index);
        if (target === undefined || target === null || id === "others") {
            return;
        }
        if (!this.isDrillPage) {
            this.lastPage = this.state.page;
        }
        this.state.drill = { id, name };
        this.newEpoch();
        this.state.page = target;
        this.state.selection = null;
        this.state.selected = null;
        this.refresh();
    }
    get backPage() {
        // La page ordinaire d'où l'on vient, sinon la première page ordinaire ; aucune : pas de Retour.
        const ok = (i) => i !== null && this.pages[i] && this.pages[i].drill !== "customer";
        if (ok(this.lastPage)) {
            return this.lastPage;
        }
        const first = this.pages.findIndex((p) => p.drill !== "customer");
        return first >= 0 ? first : null;
    }
    drillBack() {
        if (this.backPage !== null) {
            this.setPage(this.backPage);
        }
    }
    switchDrill(choice) {
        this.drillTo(choice.id, choice.name, this.state.page);
    }

    setPage(index) {
        this.newEpoch();
        if (!this.isDrillPage && this.state.mode === "read") {
            this.lastPage = this.state.page;
        }
        if (!(this.pages[index] && this.pages[index].drill === "customer")) {
            this.state.drill = null; // l'extraction ne vaut que pour sa page
        }
        this.state.page = index;
        this.state.selection = null;
        this.state.selected = null;
        this.refresh();
    }
    onKeydown(ev) {
        if (ev.key === "Escape" && this.state.menu) {
            this.closeMenu();
        } else if (ev.key === "Escape" && this.state.selection) {
            this.clearSelection();
        }
    }
    gridStyle(v) {
        return `grid-column: ${v.x + 1} / span ${v.w}; grid-row: ${v.y + 1} / span ${v.h};`;
    }
    titleOf(v) {
        if (v.title) {
            return v.title;
        }
        const r = this.state.results[v.id];
        const name = r && r.measures && r.measures[0] && r.measures[0].name;
        if (name && v.type === "waterfall") {
            return _t("%s: what changed", name); // dans la langue de chaque lecteur
        }
        return name || (this.state.mode === "design" ? _t("New visual") : "");
    }
    errorOf(v) {
        const r = this.state.results[v.id];
        if (!r) {
            return null;
        }
        if (r.failed) {
            return r.failed;
        }
        if (r.error) {
            return r.error; // refusé pour ce visuel (une cascade d'un taux)
        }
        const errs = Object.values(r.errors || {});
        return errs.length && !(r.measures || []).length ? errs[0] : null;
    }
    periodNote() {
        const r = Object.values(this.state.results).find((x) => x && x.period);
        if (!r) {
            return "";
        }
        const f = (d) => new Intl.DateTimeFormat(locale(), { day: "numeric", month: "short", year: "numeric" })
            .format(new Date(d + "T12:00:00"));
        const vs = _t("vs"); // hors de la chaîne gabarit : l'extraction des traductions l'y manquerait
        return `${f(r.period.start)} – ${f(r.period.end)} · ${vs} ${f(r.period.previous_start)} – ${f(r.period.previous_end)}`;
    }

    // -------------------------------------------------- conception
    async enterDesign() {
        if (!this.state.fields.length) {
            this.state.fields = await this.orm.call("bf.bi.report", "bf_designer_fields", []);
        }
        this.state.draft = JSON.parse(JSON.stringify(this.state.report.pages));
        if (!this.state.draft.length) {
            // Un rapport sans page (vidé ailleurs) : une page pour pouvoir y travailler.
            this.state.draft.push({ id: null, name: _t("Page %s", 1), visuals: [] });
            this.state.page = 0;
        }
        this.state.theme = (this.state.report.theme && this.state.report.theme.name) || "default";
        this.state.selection = null;
        this.state.drill = null; // le concepteur voit tous les clients, rien de caché
        this.lastPage = null;
        this.state.mode = "design";
        await this.refresh();
    }
    async leaveDesign() {
        await this.flushSave();
        if (this.state.save.status === "conflict" || this.state.save.status === "error") {
            return; // rester en conception tant que ce n'est pas réglé
        }
        await this.loadReport();
        this.state.mode = "read";
        this.state.selected = null;
        this.state.draft = null;
        await this.loadChoices();
        await this.refresh();
    }
    selectVisual(id) {
        this.state.selected = id;
    }
    scheduleSave() {
        this.state.save.status = "dirty";
        if (this.inFlight) {
            this.pendingSave = true; // modifié pendant l'envoi : renvoyer après, avec la nouvelle révision
        }
        clearTimeout(this.saveTimer);
        this.saveTimer = setTimeout(() => this.save(), SAVE_DELAY);
    }
    flushSave() {
        // Tout envoyer maintenant et attendre la fin (quitter la conception, quitter l'écran).
        clearTimeout(this.saveTimer);
        if (this.state.save.status === "dirty") {
            return this.save();
        }
        return this.inFlight || Promise.resolve();
    }
    save() {
        if (!this.state.draft) {
            return Promise.resolve();
        }
        if (this.inFlight) {
            this.pendingSave = true;
            return this.inFlight;
        }
        this.pendingSave = false;
        this.inFlight = this.sendSave().finally(() => {
            this.inFlight = null;
            if (this.pendingSave && this.state.save.status === "dirty") {
                return this.save();
            }
        });
        return this.inFlight;
    }
    async sendSave() {
        this.state.save.status = "saving";
        // Les objets de page envoyés : leurs identifiants leur reviennent même si la liste des
        // pages a changé entre-temps (page ajoutée ou retirée pendant l'envoi).
        const sent = this.state.draft.slice();
        const pages = sent.map((p) => ({
            id: p.id || null, name: p.name, visuals: p.visuals,
            drill: p.drill || null, preset: p.preset || null, customers: p.customers || [],
        }));
        try {
            const r = await this.orm.call("bf.bi.report", "bf_save", [[this.reportId], pages, this.state.revision],
                                          { theme: this.state.theme || null });
            if (r.status === "conflict") {
                this.state.save.status = "conflict";
                this.state.save.author = r.author || "";
                this.serverRevision = r.revision;
                this.pendingSave = false;
                return;
            }
            this.state.revision = r.revision;
            r.page_ids.forEach((id, i) => {
                sent[i].id = id;
            });
            if (!this.pendingSave) {
                this.state.save.status = "saved";
            } else {
                this.state.save.status = "dirty";
            }
        } catch (error) {
            this.state.save.status = "error";
            this.state.save.message = (error.data && error.data.message) || "";
            this.pendingSave = false;
        }
    }
    async reloadTheirs() {
        await this.loadReport();
        this.state.draft = JSON.parse(JSON.stringify(this.state.report.pages));
        this.state.theme = (this.state.report.theme && this.state.report.theme.name) || "default";
        this.state.page = Math.min(this.state.page, this.state.draft.length - 1);
        this.state.save.status = "saved";
        await this.refresh();
    }
    async keepMine() {
        this.state.revision = this.serverRevision;
        this.state.save.status = "dirty";
        await this.save();
    }
    changeVisual(values) {
        const v = this.selectedVisual;
        if (!v) {
            return;
        }
        const specChanged = ["type", "measures", "dimension", "limit", "columns"].some((k) => k in values);
        Object.assign(v, values);
        if (v.target === null) {
            delete v.target;
        }
        if (v.max === null) {
            delete v.max;
        }
        if (!v.title) {
            delete v.title;
        }
        if (specChanged) {
            this.requery(v);
        }
        this.scheduleSave();
    }
    addVisual() {
        if (!this.canAddVisual) {
            return;
        }
        const visuals = this.page.visuals;
        const y = visuals.reduce((m, v) => Math.max(m, v.y + v.h), 0);
        let n = visuals.length + 1;
        while (visuals.some((v) => v.id === "v" + n)) {
            n++;
        }
        const v = { id: "v" + n, type: "column", measures: [], dimension: null, x: 0, y: Math.min(y, 60), w: 6, h: 4 };
        visuals.push(v);
        this.state.selected = v.id;
        this.scheduleSave();
    }
    deleteVisual() {
        const page = this.page;
        page.visuals = page.visuals.filter((v) => v.id !== this.state.selected);
        delete this.state.results[this.state.selected];
        this.state.selected = null;
        this.scheduleSave();
    }
    addPage() {
        if (!this.canAddPage) {
            return;
        }
        this.newEpoch();
        const n = this.state.draft.length + 1;
        this.state.draft.push({ id: null, name: _t("Page %s", n), visuals: [] });
        this.state.page = this.state.draft.length - 1;
        this.state.selected = null;
        this.state.results = {};
        this.scheduleSave();
    }
    deletePage(index) {
        if (this.state.draft.length <= 1) {
            return;
        }
        this.newEpoch();
        this.state.drill = null;
        this.lastPage = null;
        this.state.draft.splice(index, 1);
        this.state.page = Math.min(this.state.page, this.state.draft.length - 1);
        this.state.selected = null;
        this.scheduleSave();
        this.refresh();
    }
    changeTheme(name) {
        this.state.theme = name;
        this.scheduleSave();
    }
    get themeChoices() {
        const colors = (this.state.report && this.state.report.theme && this.state.report.theme.company_colors) || [];
        return [
            { key: "default", label: _t("Symbifox"), preview: buildTheme({ name: "default" }).colors.slice(0, 5) },
            { key: "company", label: _t("Company colors"), preview: buildTheme({ name: "company", company_colors: colors }).colors.slice(0, 5),
              empty: !buildTheme({ name: "company", company_colors: colors }).brand.length },
        ];
    }
    changePageSettings(values) {
        Object.assign(this.page, values);
        if (this.page.drill) {
            this.page.customers = []; // une page d'extraction suit le client sur lequel on l'ouvre
        }
        this.loadPartnerNames();
        this.scheduleSave();
        this.refresh();
    }
    removePageLabel(name) {
        return _t("Remove page %s", name);
    }
    renamePage(name) {
        const clean = (name || "").slice(0, 80);
        if (clean.trim()) {
            this.page.name = clean;
            this.scheduleSave();
        }
    }
    async openHistory() {
        this.state.versions = await this.orm.call("bf.bi.report", "bf_list_versions", [[this.reportId]]);
    }
    closeHistory() {
        this.state.versions = null;
    }
    async restore(versionId) {
        try {
            await this.orm.call("bf.bi.report.version", "bf_restore", [[versionId]]);
        } catch (error) {
            this.notification.add((error.data && error.data.message) || _t("This version cannot be restored."), { type: "danger" });
            return;
        }
        this.state.versions = null;
        await this.reloadTheirs();
        this.notification.add(_t("Version restored."), { type: "success" });
    }
    versionDate(v) {
        return new Intl.DateTimeFormat(locale(), { dateStyle: "medium", timeStyle: "short" })
            .format(new Date(v.date.replace(" ", "T") + "Z"));
    }
    onPointerDown(ev, v) {
        if (this.state.mode !== "design" || ev.button !== 0) {
            return;
        }
        this.state.selected = v.id;
        const el = ev.currentTarget;
        const canvas = this.canvasRef.el;
        if (!canvas || window.innerWidth < 768) {
            return;
        }
        const resize = ev.target.classList.contains("o_bfr_grip");
        const colW = (canvas.getBoundingClientRect().width + GRID_GAP) / 12;
        const rowH = ROW_HEIGHT + GRID_GAP;
        const start = { x: ev.clientX, y: ev.clientY, vx: v.x, vy: v.y, vw: v.w, vh: v.h };
        const next = { x: v.x, y: v.y, w: v.w, h: v.h };
        let moved = false;
        el.setPointerCapture(ev.pointerId);
        const move = (e) => {
            if (!moved && Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) < 4) {
                return;
            }
            moved = true;
            const dx = Math.round((e.clientX - start.x) / colW), dy = Math.round((e.clientY - start.y) / rowH);
            if (resize) {
                next.w = Math.max(2, Math.min(12 - start.vx, start.vw + dx));
                next.h = Math.max(2, Math.min(12, start.vh + dy));
            } else {
                next.x = Math.max(0, Math.min(12 - start.vw, start.vx + dx));
                next.y = Math.max(0, Math.min(60, start.vy + dy));
            }
            // Pendant le geste : le style seul ; l'état (et l'enregistrement) au relâcher.
            el.style.gridColumn = `${next.x + 1} / span ${next.w}`;
            el.style.gridRow = `${next.y + 1} / span ${next.h}`;
        };
        const up = (e) => {
            el.removeEventListener("pointermove", move);
            el.removeEventListener("pointerup", up);
            el.removeEventListener("pointercancel", up);
            if (moved && e.type === "pointerup") {
                Object.assign(v, next);
                this.scheduleSave();
            } else if (moved) {
                el.style.gridColumn = `${v.x + 1} / span ${v.w}`; // geste annulé : remettre en place
                el.style.gridRow = `${v.y + 1} / span ${v.h}`;
            }
        };
        el.addEventListener("pointermove", move);
        el.addEventListener("pointerup", up);
        el.addEventListener("pointercancel", up);
    }
}

registry.category("actions").add("bf_bi_report.report_action", ReportAction);
