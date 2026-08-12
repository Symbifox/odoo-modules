/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

export class MeetingDashboard extends Component {
    static template = "bf_meeting.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.userId = user.userId;

        this.state = useState({
            data: null,
            loading: true,
            search: "",
            selected: {},
            groupByProject: false,
            collapsedProjects: {},
            onlyMine: true,
        });

        onWillStart(async () => {
            await this.loadData();
        });
    }

    async loadData() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "meeting.dashboard",
                "get_dashboard_data",
                []
            );
            this.state.selected = {};
        } catch (error) {
            console.error("Error loading meeting dashboard:", error);
        }
        this.state.loading = false;
    }

    async refresh() {
        await this.loadData();
    }

    formatDate(iso) {
        if (!iso) return "";
        const dt = new Date(iso);
        return dt.toLocaleDateString("fr-CA", {
            year: "numeric",
            month: "short",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    cardBorderClass(state) {
        switch (state) {
            case "danger":  return "border-danger";
            case "warning": return "border-warning";
            case "info":    return "border-info";
            default:        return "";
        }
    }

    stepNodeClass(status) {
        switch (status) {
            case "done":    return "bf_stepper_done";
            case "current": return "bf_stepper_current";
            case "skipped": return "bf_stepper_skipped";
            default:        return "bf_stepper_pending";
        }
    }

    stepBarClass(status) {
        return (status === "done" || status === "skipped")
            ? "bf_stepper_bar_done"
            : "bf_stepper_bar_pending";
    }

    // ---- Filtering ----

    matchesSearch(card) {
        const q = this.state.search.trim().toLowerCase();
        if (!q) return true;
        return (
            (card.name || "").toLowerCase().includes(q) ||
            (card.project_name || "").toLowerCase().includes(q) ||
            (card.partner_name || "").toLowerCase().includes(q) ||
            (card.agenda_resp_name || "").toLowerCase().includes(q) ||
            (card.minutes_resp_name || "").toLowerCase().includes(q)
        );
    }

    respLabel(side) {
        return side === "left" ? "Responsable de l'OdJ" : "Responsable du CR";
    }

    matchesMine(card, side) {
        if (!this.state.onlyMine) return true;
        const respId = side === "left" ? card.agenda_resp_id : card.minutes_resp_id;
        return respId === this.userId;
    }

    filteredCards(side) {
        const list = side === "left"
            ? this.state.data.cards_left
            : this.state.data.cards_right;
        return list.filter(c => this.matchesSearch(c) && this.matchesMine(c, side));
    }

    // KPI tiles recomputed from the same filtered set the cards use,
    // so search + "Mes responsabilités" apply to widgets too.
    displayedKpis() {
        const left  = this.filteredCards("left");
        const right = this.filteredCards("right");
        const all   = [...left, ...right];

        const stepIs = (c, idx, status) => c.steps[idx - 1].status === status;

        return {
            upcoming_without_agenda:   left.filter(c => stepIs(c, 1, "current")).length,
            upcoming_agenda_not_sent:  left.filter(c =>
                c.steps[0].status === "done"
                && c.steps[2].status !== "done"
                && c.steps[2].status !== "skipped"
            ).length,
            past_without_minutes:      right.filter(c => stepIs(c, 5, "current")).length,
            minutes_to_review:         all.filter(c => stepIs(c, 6, "current")).length,
            minutes_to_send:           all.filter(c => stepIs(c, 7, "current")).length,
            this_week:                 all.filter(c => c.in_this_week).length,
        };
    }

    groupedCards(side) {
        const cards = this.filteredCards(side);
        const groups = {};
        for (const c of cards) {
            const key = c.project_id || 0;
            if (!groups[key]) {
                groups[key] = {
                    project_id: c.project_id,
                    project_name: c.project_name || "(Sans projet)",
                    cards: [],
                };
            }
            groups[key].cards.push(c);
        }
        return Object.values(groups).sort((a, b) =>
            a.project_name.localeCompare(b.project_name)
        );
    }

    toggleProjectCollapse(projectId) {
        const key = projectId || 0;
        this.state.collapsedProjects[key] = !this.state.collapsedProjects[key];
    }

    isProjectCollapsed(projectId) {
        return !!this.state.collapsedProjects[projectId || 0];
    }

    // ---- Selection ----

    toggleSelect(card) {
        if (this.state.selected[card.event_id]) {
            delete this.state.selected[card.event_id];
        } else {
            this.state.selected[card.event_id] = true;
        }
    }

    isSelected(card) {
        return !!this.state.selected[card.event_id];
    }

    selectedCount() {
        return Object.keys(this.state.selected).length;
    }

    clearSelection() {
        this.state.selected = {};
    }

    async dismissSelection() {
        const ids = Object.keys(this.state.selected).map(Number);
        if (!ids.length) return;
        await this.orm.call("meeting.dashboard", "dismiss_events", [ids]);
        await this.loadData();
    }

    // ---- Click-throughs ----

    async openFiltered(filterKey) {
        const act = await this.orm.call(
            "meeting.dashboard",
            "open_filtered_list",
            [filterKey]
        );
        return this.action.doAction(act);
    }

    async openRecord(model, resId) {
        if (!resId) return;
        const act = await this.orm.call(
            "meeting.dashboard",
            "open_record",
            [model, resId]
        );
        return this.action.doAction(act);
    }

    openEvent(row)     { return this.openRecord("calendar.event",  row.event_id);  }
    openAgenda(row)    { return this.openRecord("meeting.agenda",  row.agenda_id); }
    openRecordRow(row) { return this.openRecord("meeting.record",  row.record_id); }

    openRowBest(row) {
        if (row.record_id) return this.openRecordRow(row);
        if (row.agenda_id) return this.openAgenda(row);
        return this.openEvent(row);
    }

    async dismissEvent(row) {
        if (!row.event_id) return;
        await this.orm.call("meeting.dashboard", "dismiss_event", [row.event_id]);
        await this.loadData();
    }

    async toggleStepSkip(card, step) {
        // Only allow toggling on pending or skipped steps — not on done.
        // Orphan rows (no calendar event) can't store skipped_steps, so disable.
        if (step.status === "done" || !card.event_id) return;
        await this.orm.call(
            "meeting.dashboard",
            "toggle_step_skip",
            [card.event_id, step.idx]
        );
        await this.loadData();
    }
}

registry.category("actions").add("meeting_dashboard", MeetingDashboard);
