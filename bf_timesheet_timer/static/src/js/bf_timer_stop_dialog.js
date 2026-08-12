/** @odoo-module **/
import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

export class BfTimerStopDialog extends Component {
    static template = "bf_timesheet_timer.StopDialog";
    static components = { Dialog };
    static props = {
        timerData: Object,
        presets: { type: Array, optional: true },
        onConfirm: Function,
        onDiscard: Function,
        onCancel: Function,
        close: { type: Function, optional: true },
    };

    setup() {
        this.notification = useService("notification");
        const data = this.props.timerData;
        const suggestedMinutes = data.suggested_minutes || 5;
        this.roundingIncrement = data.rounding_increment || 5;
        this.roundingMode = data.rounding_mode || "round_all";
        this.state = useState({
            hours: Math.floor(suggestedMinutes / 60),
            minutes: Math.round(suggestedMinutes % 60),
            description: data.description || data.task_name || "",
            belowMinimum: false,
        });
    }

    get totalMinutes() {
        return this.state.hours * 60 + this.state.minutes;
    }

    get rawElapsed() {
        const s = this.props.timerData.elapsed_seconds || 0;
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sec = Math.floor(s % 60);
        return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    }

    onChangeHours(ev) {
        this.state.hours = Math.max(0, parseInt(ev.target.value) || 0);
        this.state.belowMinimum = this.roundingMode !== "none" && this.totalMinutes < this.roundingIncrement;
    }

    onChangeMinutes(ev) {
        this.state.minutes = Math.max(0, Math.min(59, parseInt(ev.target.value) || 0));
        this.state.belowMinimum = this.roundingMode !== "none" && this.totalMinutes < this.roundingIncrement;
    }

    onChangeDescription(ev) {
        this.state.description = ev.target.value;
    }

    onClickPreset(preset) {
        this.state.description = preset.text;
    }

    async onConfirm() {
        let totalMin = this.totalMinutes;
        if (this.roundingMode !== "none" && totalMin < this.roundingIncrement) {
            totalMin = this.roundingIncrement;
        }
        const durationHours = Math.round((totalMin / 60) * 100) / 100;
        await this.props.onConfirm(
            this.props.timerData.timer_id,
            durationHours,
            this.state.description
        );
        this.notification.add(_t("Feuille de temps enregistrée."), { type: "success" });
        this.props.close();
    }

    async onDiscard() {
        await this.props.onDiscard(this.props.timerData.timer_id);
        this.notification.add(_t("Timer supprimé sans enregistrement."), { type: "warning" });
        this.props.close();
    }

    async onCancel() {
        // Re-activate the timer — it was marked is_active=False by stop_timer
        await this.props.onCancel(this.props.timerData.timer_id);
        this.props.close();
    }
}
