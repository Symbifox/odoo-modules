/** @odoo-module **/
import { reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";

export const bfTimerService = {
    dependencies: ["orm"],
    async start(env, { orm }) {
        const state = reactive({
            timers: [],
            pendingTimers: [],
            todayTotal: 0,
            weekTotal: 0,
        });

        async function refresh() {
            try {
                const [timers, pendingTimers, todayTotal, weekTotal] = await Promise.all([
                    orm.call("bf.timer", "get_active_timers", []),
                    orm.call("bf.timer", "get_pending_timers", []),
                    orm.call("bf.timer", "get_today_total", []),
                    orm.call("bf.timer", "get_week_total", []),
                ]);
                state.timers = timers;
                state.pendingTimers = pendingTimers;
                state.todayTotal = todayTotal;
                state.weekTotal = weekTotal;
            } catch {
                // Silently ignore — user may not be logged in yet
            }
        }

        async function startTimer(taskId) {
            const timer = await orm.call("bf.timer", "start_timer", [taskId]);
            await refresh();
            return timer;
        }

        async function stopTimer(timerId) {
            const data = await orm.call("bf.timer", "stop_timer", [timerId]);
            await refresh();
            return data;
        }

        async function confirmTimesheet(timerId, durationHours, description) {
            await orm.call("bf.timer", "confirm_timesheet", [
                timerId,
                durationHours,
                description,
            ]);
            await refresh();
        }

        async function discardTimer(timerId) {
            await orm.call("bf.timer", "discard_timer", [timerId]);
            await refresh();
        }

        async function pauseTimer(timerId) {
            await orm.call("bf.timer", "pause_timer", [timerId]);
            await refresh();
        }

        async function resumeTimer(timerId) {
            await orm.call("bf.timer", "resume_timer", [timerId]);
            await refresh();
        }

        async function reactivateTimer(timerId) {
            await orm.call("bf.timer", "reactivate_timer", [timerId]);
            await refresh();
        }

        async function getRecentTasks() {
            return orm.call("bf.timer", "get_recent_tasks", [10]);
        }

        async function getDescriptionPresets() {
            return orm.call("bf.timer", "get_description_presets", []);
        }

        async function pinTask(taskId) {
            await orm.call("bf.timer", "pin_task", [taskId]);
        }

        async function unpinTask(taskId) {
            await orm.call("bf.timer", "unpin_task", [taskId]);
        }

        // Initial load (non-blocking — don't await to avoid blocking web client startup)
        refresh();
        // Periodic sync every 60s
        setInterval(refresh, 60000);

        return {
            state,
            refresh,
            startTimer,
            stopTimer,
            pauseTimer,
            resumeTimer,
            confirmTimesheet,
            discardTimer,
            reactivateTimer,
            getRecentTasks,
            getDescriptionPresets,
            pinTask,
            unpinTask,
        };
    },
};

registry.category("services").add("bfTimerService", bfTimerService);
