/** @odoo-module **/
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { BfTimerStopDialog } from "./bf_timer_stop_dialog";

/**
 * Le timer en pleine page — pensé pour un téléphone posé sur le bureau.
 *
 * Rien de neuf côté données : même `bfTimerService`, mêmes tâches épinglées,
 * mêmes récentes. Ce qui change est la SURFACE. La barre système d'Odoo n'est
 * visible que quand un onglet Odoo est devant ; un écran qu'on lit d'un bout à
 * l'autre du bureau, lui, se consulte sans rien ouvrir.
 *
 * Deux commandes que la barre système n'a pas, et qui font toute la différence
 * entre « une page » et « un afficheur » :
 *
 * - le **verrou d'écran** (`navigator.wakeLock`), sans quoi le téléphone
 *   s'éteint au bout d'une minute et l'afficheur ne montre plus rien ;
 * - le **plein écran** du navigateur, qui retire la barre d'adresse — la seule
 *   façon de gagner de la hauteur sur un téléphone.
 *
 * Les deux sont explicites et réversibles : rien n'est imposé au chargement.
 * ⚠️ `wakeLock` n'existe pas partout et le verrou saute au moindre passage en
 * arrière-plan ; d'où la reprise sur `visibilitychange`, faute de quoi revenir
 * à la page la laisse s'éteindre alors que le bouton dit le contraire.
 */
export class BfTimerPage extends Component {
    static template = "bf_timesheet_timer.Page";
    static props = ["*"];

    setup() {
        this.timerService = useService("bfTimerService");
        this.actionService = useService("action");
        this.dialogService = useService("dialog");
        this.notification = useService("notification");

        this.state = useState({
            elapsed: {},
            tasks: [],
            searchQuery: "",
            awake: false,
            fullscreen: false,
        });

        this._tickInterval = null;
        this._wakeLock = null;
        this._presets = null;
        this._boundVisibility = this._onVisibilityChange.bind(this);
        this._boundFullscreen = this._onFullscreenChange.bind(this);

        onMounted(async () => {
            // Défensif, comme la barre système : un remontage empilerait un
            // second intervalle sur la même instance.
            if (this._tickInterval) {
                clearInterval(this._tickInterval);
            }
            this._tickInterval = setInterval(() => this._tick(), 1000);
            document.addEventListener("visibilitychange", this._boundVisibility);
            document.addEventListener("fullscreenchange", this._boundFullscreen);
            await this.timerService.refresh();
            await this.loadTasks();
        });

        onWillUnmount(() => {
            if (this._tickInterval) {
                clearInterval(this._tickInterval);
                this._tickInterval = null;
            }
            document.removeEventListener("visibilitychange", this._boundVisibility);
            document.removeEventListener("fullscreenchange", this._boundFullscreen);
            // Le verrou survivrait à la page : le relâcher, sinon l'écran d'un
            // téléphone rangé dans une poche resterait allumé.
            this._releaseWakeLock();
        });
    }

    // ── Lecture ───────────────────────────────────────────────────────────

    get timers() {
        return this.timerService.state.timers || [];
    }

    get hasActiveTimers() {
        return this.timers.length > 0;
    }

    get formattedTodayTotal() {
        return this._formatHours(this.timerService.state.todayTotal || 0);
    }

    get formattedWeekTotal() {
        return this._formatHours(this.timerService.state.weekTotal || 0);
    }

    get awakeTitle() {
        return this.state.awake
            ? _t("Laisser l'écran s'éteindre")
            : _t("Garder l'écran allumé");
    }

    get fullscreenTitle() {
        return this.state.fullscreen ? _t("Quitter le plein écran") : _t("Plein écran");
    }

    get filteredTasks() {
        const q = this.state.searchQuery.toLowerCase().trim();
        const tasks = this.state.tasks.filter((t) => !this.isTaskTimerActive(t.task_id));
        if (!q) return tasks;
        return tasks.filter(
            (t) =>
                t.task_name.toLowerCase().includes(q) ||
                t.project_name.toLowerCase().includes(q)
        );
    }

    _formatHours(total) {
        const h = Math.floor(total);
        const m = Math.round((total - h) * 60);
        return m > 0 ? `${h}h${String(m).padStart(2, "0")}` : `${h}h`;
    }

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
        // Mêmes seuils que la barre système : deux surfaces qui donneraient des
        // couleurs différentes au même timer se contrediraient à l'écran.
        const elapsed = this.getElapsed(timer);
        if (elapsed >= 7200) return "bf-timer-danger";
        if (elapsed >= 3600) return "bf-timer-warning";
        return "bf-timer-normal";
    }

    isTaskTimerActive(taskId) {
        return this.timers.some((t) => t.task_id === taskId);
    }

    _tick() {
        const now = Date.now() / 1000;
        for (const timer of this.timers) {
            if (timer.is_paused) {
                this.state.elapsed[timer.id] = timer.accumulated_seconds || 0;
            } else {
                const startTs = new Date(timer.start_time_iso + "Z").getTime() / 1000;
                this.state.elapsed[timer.id] = Math.max(
                    0,
                    (timer.accumulated_seconds || 0) + (now - startTs)
                );
            }
        }
        this._tickCount = (this._tickCount || 0) + 1;
        if (this._tickCount % 5 === 0) {
            this.timerService.refresh();
        }
    }

    // ── Écran ─────────────────────────────────────────────────────────────

    async onToggleAwake() {
        if (this.state.awake) {
            this._releaseWakeLock();
            return;
        }
        if (!("wakeLock" in navigator)) {
            this.notification.add(
                _t("Ce navigateur ne sait pas garder l'écran allumé."),
                { type: "warning" }
            );
            return;
        }
        try {
            this._wakeLock = await navigator.wakeLock.request("screen");
            this._wakeLock.addEventListener("release", () => {
                this._wakeLock = null;
                this.state.awake = false;
            });
            this.state.awake = true;
        } catch {
            this.notification.add(_t("L'écran n'a pas pu être maintenu allumé."), {
                type: "warning",
            });
        }
    }

    _releaseWakeLock() {
        if (this._wakeLock) {
            this._wakeLock.release().catch(() => {});
            this._wakeLock = null;
        }
        this.state.awake = false;
    }

    async _onVisibilityChange() {
        // Le verrou est perdu dès que l'onglet passe derrière. Sans cette
        // reprise, le bouton resterait allumé sur un verrou qui n'existe plus.
        if (document.visibilityState === "visible" && this.state.awake && !this._wakeLock) {
            try {
                this._wakeLock = await navigator.wakeLock.request("screen");
            } catch {
                this.state.awake = false;
            }
        }
    }

    onToggleFullscreen() {
        if (document.fullscreenElement) {
            document.exitFullscreen().catch(() => {});
        } else {
            document.documentElement.requestFullscreen().catch(() => {
                this.notification.add(_t("Le plein écran a été refusé."), {
                    type: "warning",
                });
            });
        }
    }

    _onFullscreenChange() {
        // Sorti par la touche Échap ou le geste du système : le bouton doit
        // suivre l'état réel, pas celui qu'on a demandé.
        this.state.fullscreen = Boolean(document.fullscreenElement);
    }

    // ── Actions ───────────────────────────────────────────────────────────

    async loadTasks() {
        this.state.tasks = await this.timerService.getRecentTasks();
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
    }

    async onStartTask(taskId) {
        // Pas d'avertissement « un timer est déjà en cours » ici : travailler
        // sur plusieurs tâches à la fois est le cas normal, et cette page
        // existe précisément pour les voir toutes.
        await this.timerService.startTimer(taskId);
        await this.loadTasks();
    }

    async onStopTimer(timerId) {
        const data = await this.timerService.stopTimer(timerId);
        if (this.timerService.claimPendingDialog(data.timer_id)) {
            await this._showStopDialog(data);
        }
        await this.loadTasks();
    }

    async onPauseTimer(timerId) {
        await this.timerService.pauseTimer(timerId);
    }

    async onResumeTimer(timerId) {
        await this.timerService.resumeTimer(timerId);
    }

    async onPinTask(taskId) {
        await this.timerService.pinTask(taskId);
        await this.loadTasks();
    }

    async onUnpinTask(taskId) {
        await this.timerService.unpinTask(taskId);
        await this.loadTasks();
    }

    onOpenTask(taskId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "project.task",
            res_id: taskId,
            views: [[false, "form"]],
            target: "current",
        });
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
}

registry.category("actions").add("bf_timesheet_timer.page", BfTimerPage);
