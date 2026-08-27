/** @odoo-module **/
import { Component, markup, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { BfTimerStopDialog } from "./bf_timer_stop_dialog";

export class BfTimerSystray extends Component {
    static template = "bf_timesheet_timer.Systray";
    static components = { Dropdown, DropdownItem };
    static props = [];

    setup() {
        this.timerService = useService("bfTimerService");
        this.actionService = useService("action");
        this.dialogService = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            elapsed: {},
            recentTasks: [],
            recentLoaded: false,
            searchQuery: "",
        });
        this._tickInterval = null;
        this._boundKeyDown = this._onKeyDown.bind(this);
        // Cache presets
        this._presets = null;

        onMounted(() => {
            // Defensive: never stack a second interval onto the same instance.
            // A remount (onMounted firing again before teardown) would otherwise
            // leak the previous interval — they all share this._tickCount and
            // compound into a runaway refresh() storm.
            if (this._tickInterval) {
                clearInterval(this._tickInterval);
            }
            this._tickInterval = setInterval(() => this._tick(), 1000);
            document.addEventListener("keydown", this._boundKeyDown);
            // Check for pending timers on mount
            this._checkPendingTimers();
        });

        // Use onWillUnmount (fires on every unmount, including remounts) rather
        // than onWillDestroy (fires only on permanent teardown) so the interval
        // is always cleared before a possible remount re-adds one.
        onWillUnmount(() => {
            if (this._tickInterval) {
                clearInterval(this._tickInterval);
                this._tickInterval = null;
            }
            document.removeEventListener("keydown", this._boundKeyDown);
        });
    }

    // -------------------------------------------------------------------------
    // Getters
    // -------------------------------------------------------------------------

    get timers() {
        return this.timerService.state.timers || [];
    }

    get pendingTimers() {
        return this.timerService.state.pendingTimers || [];
    }

    get todayTotal() {
        return this.timerService.state.todayTotal || 0;
    }

    get weekTotal() {
        return this.timerService.state.weekTotal || 0;
    }

    get hasActiveTimers() {
        return this.timers.length > 0;
    }

    get primaryTimer() {
        return this.timers.length > 0 ? this.timers[0] : null;
    }

    get timerCount() {
        return this.timers.length;
    }

    get formattedTodayTotal() {
        return this._formatHours(this.todayTotal);
    }

    get formattedWeekTotal() {
        return this._formatHours(this.weekTotal);
    }

    get filteredRecentTasks() {
        const q = this.state.searchQuery.toLowerCase().trim();
        if (!q) return this.state.recentTasks;
        return this.state.recentTasks.filter(
            (t) => t.task_name.toLowerCase().includes(q) || t.project_name.toLowerCase().includes(q)
        );
    }

    _formatHours(total) {
        const h = Math.floor(total);
        const m = Math.round((total - h) * 60);
        if (m > 0) {
            return `${h}h${String(m).padStart(2, "0")}`;
        }
        return `${h}h`;
    }

    // -------------------------------------------------------------------------
    // Timer display
    // -------------------------------------------------------------------------

    getElapsed(timer) {
        if (this.state.elapsed[timer.id] !== undefined) {
            return this.state.elapsed[timer.id];
        }
        return timer.elapsed_seconds || 0;
    }

    formatTime(seconds) {
        const s = Math.max(0, Math.floor(seconds));
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sec = s % 60;
        return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    }

    getTimerClass(timer) {
        const elapsed = this.getElapsed(timer);
        if (elapsed >= 7200) return "bf-timer-danger";
        if (elapsed >= 3600) return "bf-timer-warning";
        return "bf-timer-normal";
    }

    get hasRunningTimer() {
        return this.timers.some((t) => !t.is_paused);
    }

    get allTimersPaused() {
        return this.timers.length > 0 && this.timers.every((t) => t.is_paused);
    }

    get redTimers() {
        return this.timers.filter((t) => this.getElapsed(t) >= 7200);
    }

    get hasRedTimers() {
        return this.redTimers.length > 0;
    }

    get hasMultipleTimers() {
        return this.timers.length > 1;
    }

    _tick() {
        const now = Date.now() / 1000;
        for (const timer of this.timers) {
            if (timer.is_paused) {
                this.state.elapsed[timer.id] = timer.accumulated_seconds || 0;
            } else {
                const startTs = new Date(timer.start_time_iso + "Z").getTime() / 1000;
                this.state.elapsed[timer.id] = Math.max(0, (timer.accumulated_seconds || 0) + (now - startTs));
            }
        }
        // Refresh service data every 5 seconds to catch pending timers quickly
        // (e.g. when Stop is clicked from kanban/form buttons)
        this._tickCount = (this._tickCount || 0) + 1;
        if (this._tickCount % 5 === 0) {
            this.timerService.refresh().then(() => this._checkPendingTimers());
        }
    }

    // -------------------------------------------------------------------------
    // Pending timer detection (for form button stops)
    // -------------------------------------------------------------------------

    _checkPendingTimers() {
        for (const pt of this.pendingTimers) {
            // ⚠️ La réservation vit dans le service, pas ici : la page plein
            // écran détecte le MÊME timer en attente, et deux registres privés
            // ouvraient deux dialogues pour un seul arrêt.
            if (this.timerService.claimPendingDialog(pt.timer_id)) {
                this._showStopDialog(pt);
            }
        }
    }

    async _showStopDialog(timerData) {
        if (!this._presets) {
            try {
                this._presets = await this.timerService.getDescriptionPresets();
            } catch {
                this._presets = [];
            }
        }
        this.dialogService.add(BfTimerStopDialog, {
            timerData,
            presets: this._presets,
            onConfirm: async (tid, hours, desc) => {
                await this.timerService.confirmTimesheet(tid, hours, desc);
                this.timerService.releasePendingDialog(tid);
            },
            onDiscard: async (tid) => {
                await this.timerService.discardTimer(tid);
                this.timerService.releasePendingDialog(tid);
            },
            onCancel: async (tid) => {
                await this.timerService.reactivateTimer(tid);
                this.timerService.releasePendingDialog(tid);
            },
        });
    }

    // -------------------------------------------------------------------------
    // Actions
    // -------------------------------------------------------------------------

    async loadRecentTasks() {
        this.state.recentTasks = await this.timerService.getRecentTasks();
        this.state.recentLoaded = true;
        this.state.searchQuery = "";
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
    }

    isTaskTimerActive(taskId) {
        return this.timers.some((t) => t.task_id === taskId);
    }

    _getTimerForTask(taskId) {
        return this.timers.find((t) => t.task_id === taskId);
    }

    async _startTimerWithWarning(taskId) {
        if (this.timers.length > 0 && !localStorage.getItem("bf_timer_skip_warning")) {
            return new Promise((resolve) => {
                this.dialogService.add(ConfirmationDialog, {
                    title: _t("Timer déjà en cours"),
                    body: markup(
                        `<p>Un timer est déjà en cours. Voulez-vous en démarrer un autre?</p>
                         <div class="form-check mt-2">
                           <input type="checkbox" class="form-check-input" id="bf_skip_warning_cb"/>
                           <label class="form-check-label" for="bf_skip_warning_cb">Ne plus me demander</label>
                         </div>`
                    ),
                    confirmLabel: _t("Continuer"),
                    confirm: async () => {
                        const cb = document.getElementById("bf_skip_warning_cb");
                        if (cb && cb.checked) {
                            localStorage.setItem("bf_timer_skip_warning", "1");
                        }
                        await this.timerService.startTimer(taskId);
                        this.state.recentTasks = await this.timerService.getRecentTasks();
                        resolve(true);
                    },
                    cancel: () => resolve(false),
                });
            });
        }
        await this.timerService.startTimer(taskId);
        this.state.recentTasks = await this.timerService.getRecentTasks();
    }

    async onStartTask(taskId) {
        await this._startTimerWithWarning(taskId);
    }

    async onStopTaskTimer(taskId) {
        const timer = this._getTimerForTask(taskId);
        if (timer) {
            await this.onStopTimer(timer.id);
        }
    }

    async onStopTimer(timerId) {
        const data = await this.timerService.stopTimer(timerId);
        // Réservé tout de suite : sans ça, le prochain sondage — le sien ou
        // celui de la page — rouvrirait un dialogue sur le même arrêt.
        if (this.timerService.claimPendingDialog(data.timer_id)) {
            await this._showStopDialog(data);
        }
    }

    async onStopAllTimers() {
        // Snapshot ids first — stopping mutates this.timers via service refresh.
        const ids = this.timers.map((t) => t.id);
        for (const id of ids) {
            await this.onStopTimer(id);
        }
    }

    async onStopAllRedTimers() {
        const ids = this.redTimers.map((t) => t.id);
        for (const id of ids) {
            await this.onStopTimer(id);
        }
    }

    async onPauseTimer(timerId) {
        await this.timerService.pauseTimer(timerId);
    }

    async onResumeTimer(timerId) {
        await this.timerService.resumeTimer(timerId);
    }

    async onPinTask(taskId) {
        await this.timerService.pinTask(taskId);
        this.state.recentTasks = await this.timerService.getRecentTasks();
    }

    async onUnpinTask(taskId) {
        await this.timerService.unpinTask(taskId);
        this.state.recentTasks = await this.timerService.getRecentTasks();
    }

    onClickTaskName(taskId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "project.task",
            res_id: taskId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    onOpenTimesheets() {
        this.actionService.doAction("hr_timesheet.act_hr_timesheet_line");
    }

    async _onKeyDown(ev) {
        if (ev.ctrlKey && ev.shiftKey && ev.key === "T") {
            ev.preventDefault();
            if (this.hasActiveTimers) {
                await this.onStopTimer(this.primaryTimer.id);
            } else {
                await this.loadRecentTasks();
                if (this.state.recentTasks.length > 0) {
                    await this.timerService.startTimer(this.state.recentTasks[0].task_id);
                } else {
                    this.notification.add(_t("Aucune tâche récente trouvée."), { type: "info" });
                }
            }
        }
    }
}

export const systrayTimerMenu = {
    Component: BfTimerSystray,
};

registry.category("systray").add("TimerMenu", systrayTimerMenu, { force: true, sequence: 2 });
