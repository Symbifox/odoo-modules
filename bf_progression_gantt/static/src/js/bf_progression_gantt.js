/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

// Layout constants (px). Kept in JS so the left labels and the right bars stay
// pixel-aligned: row heights here MUST match the inline styles in the template.
const HEADER_H = 48; // months row (24) + ticks row (24)
const STEP_H = 36;
const ROW_H = 32;
const BAR_H = 18;
const MIN_BAR = 6;
const DAY_MS = 86400000;
const PX_PER_DAY = { day: 36, week: 14, month: 5 };

export class BfProgressionGantt extends Component {
    static template = "bf_progression_gantt.Gantt";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        const action = this.props.action || {};
        const params = action.params || {};
        const ctx = action.context || {};
        const initialProjectId = params.project_id || ctx.default_project_id || null;

        this.state = useState({
            loading: true,
            portfolio: [],
            projectId: initialProjectId,
            data: null,
            zoom: "week",
            hover: null,
        });

        onWillStart(async () => {
            await this.loadPortfolio();
            if (!this.state.projectId && this.state.portfolio.length) {
                this.state.projectId = this.state.portfolio[0].id;
            }
            if (this.state.projectId) {
                await this.loadProject(this.state.projectId);
            } else {
                this.state.loading = false;
            }
        });
    }

    async loadPortfolio() {
        try {
            this.state.portfolio = await this.orm.call(
                "bf.progression.gantt", "get_portfolio", []
            );
        } catch (e) {
            console.error("Gantt: portfolio load failed", e);
            this.state.portfolio = [];
        }
    }

    async loadProject(projectId) {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "bf.progression.gantt", "get_project_gantt", [projectId]
            );
        } catch (e) {
            console.error("Gantt: project load failed", e);
            this.notification.add(
                _t("Erreur lors du chargement de l'échéancier."),
                { type: "danger", title: _t("Erreur") }
            );
            this.state.data = null;
        }
        this.state.loading = false;
    }

    async onSelectProject(ev) {
        const id = parseInt(ev.target.value, 10);
        this.state.projectId = id;
        await this.loadProject(id);
    }

    setZoom(zoom) {
        this.state.zoom = zoom;
    }

    async refresh() {
        if (this.state.projectId) {
            await this.loadProject(this.state.projectId);
        }
    }

    async openTask(taskId) {
        const action = await this.orm.call(
            "bf.progression.gantt", "action_open_task", [taskId]
        );
        this.action.doAction(action);
    }

    onBarEnter(task, ev) {
        this.state.hover = { task, x: ev.clientX, y: ev.clientY };
    }

    onBarLeave() {
        this.state.hover = null;
    }

    // ------------------------------------------------------------------
    // Date helpers (noon-anchored to dodge DST/timezone drift)
    // ------------------------------------------------------------------

    parseDate(str) {
        if (!str) return null;
        const clean = str.includes(" ") ? str.split(" ")[0] : str;
        return new Date(clean + "T12:00:00");
    }

    daysBetween(a, b) {
        return Math.round((b - a) / DAY_MS);
    }

    addDays(base, n) {
        return new Date(base.getTime() + n * DAY_MS);
    }

    formatDate(str) {
        const d = this.parseDate(str);
        if (!d || isNaN(d.getTime())) return "-";
        return d.toLocaleDateString("fr-CA", {
            day: "numeric", month: "short", year: "numeric",
        });
    }

    monthLabel(d) {
        const s = d.toLocaleDateString("fr-CA", { month: "long", year: "numeric" });
        return s.charAt(0).toUpperCase() + s.slice(1);
    }

    shortLabel(d) {
        return d.toLocaleDateString("fr-CA", { day: "numeric", month: "short" });
    }

    // ------------------------------------------------------------------
    // Derived geometry
    // ------------------------------------------------------------------

    get pxPerDay() {
        return PX_PER_DAY[this.state.zoom] || PX_PER_DAY.week;
    }

    get hasData() {
        return !!(this.state.data && this.state.data.tasks
            && this.state.data.tasks.length);
    }

    get layout() {
        const empty = {
            rows: [], ticks: [], months: [], arrows: [],
            chartWidth: 0, chartHeight: 0, todayX: null,
        };
        const data = this.state.data;
        if (!data || !data.tasks || !data.tasks.length) {
            return empty;
        }

        const ppd = this.pxPerDay;
        const start = this.parseDate(data.range.min);
        const end = this.parseDate(data.range.max);
        const totalDays = Math.max(1, this.daysBetween(start, end));
        const chartWidth = totalDays * ppd;

        // Group tasks by step, following the order in data.steps.
        const byStep = {};
        for (const t of data.tasks) {
            (byStep[t.step_num] = byStep[t.step_num] || []).push(t);
        }

        const rows = [];
        const taskPos = {};
        let y = 0;
        for (const step of data.steps) {
            rows.push({
                type: "step", key: "s" + step.number, step, top: y, height: STEP_H,
            });
            y += STEP_H;
            for (const t of (byStep[step.number] || [])) {
                const ts = this.parseDate(t.start);
                const te = this.parseDate(t.end);
                const x = this.daysBetween(start, ts) * ppd;
                const w = Math.max(MIN_BAR, (this.daysBetween(ts, te) + 1) * ppd);
                rows.push({
                    type: "task", key: "t" + t.id, task: t,
                    top: y, height: ROW_H, x, w,
                });
                taskPos[t.id] = { x, w, cy: y + ROW_H / 2 };
                y += ROW_H;
            }
        }
        const chartHeight = y;

        // Top header: one band per calendar month.
        const monthMap = {};
        for (let i = 0; i <= totalDays; i++) {
            const d = this.addDays(start, i);
            const k = d.getFullYear() + "-" + d.getMonth();
            if (!monthMap[k]) {
                monthMap[k] = { min: i, max: i, label: this.monthLabel(d) };
            } else {
                monthMap[k].max = i;
            }
        }
        const months = Object.values(monthMap).map((m) => ({
            label: m.label,
            left: m.min * ppd,
            width: (m.max - m.min + 1) * ppd,
        }));

        // Bottom header / gridlines, density driven by zoom.
        const ticks = [];
        for (let i = 0; i <= totalDays; i++) {
            const d = this.addDays(start, i);
            const x = i * ppd;
            if (this.state.zoom === "day") {
                ticks.push({ x, label: String(d.getDate()), major: d.getDay() === 1 });
            } else if (this.state.zoom === "week") {
                if (d.getDay() === 1) {
                    ticks.push({ x, label: this.shortLabel(d), major: true });
                }
            } else if (d.getDate() === 1) {
                ticks.push({ x, label: "", major: true });
            }
        }

        // Today line.
        const today = this.parseDate(data.range.today);
        let todayX = null;
        if (today >= start && today <= end) {
            todayX = this.daysBetween(start, today) * ppd;
        }

        // Dependency arrows: depends_on (from) right edge -> dependent (to) left edge.
        const arrows = [];
        const gap = 16;
        for (const dep of (data.deps || [])) {
            const a = taskPos[dep.from];
            const b = taskPos[dep.to];
            if (!a || !b) continue;
            const ax = a.x + a.w, ay = a.cy, bx = b.x, by = b.cy;
            arrows.push({
                d: `M ${ax},${ay} C ${ax + gap},${ay} ${bx - gap},${by} ${bx},${by}`,
            });
        }

        return { rows, ticks, months, arrows, chartWidth, chartHeight, todayX };
    }

    // ------------------------------------------------------------------
    // Presentation helpers
    // ------------------------------------------------------------------

    barClass(status) {
        return "o_bf_gantt_bar_" + (status || "upcoming");
    }

    statusLabel(status) {
        const map = {
            done: _t("Terminé"),
            in_progress: _t("En cours"),
            overdue: _t("En retard"),
            canceled: _t("Annulé"),
            upcoming: _t("À venir"),
        };
        return map[status] || status;
    }

    get headerHeight() {
        return HEADER_H;
    }

    get barHeight() {
        return BAR_H;
    }
}

registry.category("actions").add("bf_progression_gantt", BfProgressionGantt);
