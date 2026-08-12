/** @odoo-module **/

import { Composer } from "@mail/core/common/composer";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

// Models whose chatter exposes `action_bf_create_chatter_timesheet(hours, body)`.
// project.task ships here; helpdesk.ticket adds the same method in bf_helpdesk.
const BF_TIMESHEET_MODELS = ["project.task", "helpdesk.ticket"];

patch(Composer.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfTimesheet = useState({
            enabled: false,
            hours: 0,
            minutes: 15,
        });
        this.bfOrm = useService("orm");
        this.bfNotification = useService("notification");
    },

    /**
     * Show the chatter→timesheet controls only when:
     *   1. The composer is in "Log note" mode (not "Send message")
     *   2. The thread is a timesheet-capable record (task or helpdesk ticket)
     */
    get bfTimesheetVisible() {
        const thread = this.props.composer?.thread;
        return (
            this.props.type === "note"
            && !this.props.composer?.message
            && !!thread
            && BF_TIMESHEET_MODELS.includes(thread.model)
            && !!thread.id
        );
    },

    /**
     * Total duration in decimal hours from the current state, clamped to >= 0.
     */
    get bfTimesheetDurationHours() {
        const h = Math.max(0, parseInt(this.bfTimesheet.hours, 10) || 0);
        const m = Math.max(0, parseInt(this.bfTimesheet.minutes, 10) || 0);
        return h + m / 60;
    },

    /**
     * Set total duration from a minutes preset (5/15/30/60) and tick the box.
     */
    bfTimesheetSetPreset(minutes) {
        this.bfTimesheet.hours = Math.floor(minutes / 60);
        this.bfTimesheet.minutes = minutes % 60;
        this.bfTimesheet.enabled = true;
    },

    async sendMessage() {
        const shouldCreate = this.bfTimesheetVisible && this.bfTimesheet.enabled;
        const duration = this.bfTimesheetDurationHours;
        const thread = this.props.composer?.thread;
        const recordId = thread?.id;
        const recordModel = thread?.model;
        const bodySnapshot = this.props.composer?.text || "";

        if (shouldCreate && duration <= 0) {
            this.bfNotification.add(
                _t("Coche la case mais saisis une durée supérieure à 0."),
                { type: "warning" },
            );
            return;
        }

        await super.sendMessage(...arguments);

        if (!shouldCreate || !recordId || !recordModel || duration <= 0) {
            return;
        }
        try {
            const result = await this.bfOrm.call(
                recordModel,
                "action_bf_create_chatter_timesheet",
                [[recordId], duration, bodySnapshot],
            );
            this.bfNotification.add(
                _t("Feuille de temps créée (%s h).", result.unit_amount.toFixed(2)),
                { type: "success" },
            );
            this.bfTimesheet.enabled = false;
            this.bfTimesheet.hours = 0;
            this.bfTimesheet.minutes = 15;
        } catch (err) {
            this.bfNotification.add(
                _t("Note postée, mais la feuille de temps n'a pas été créée : %s",
                   err?.data?.message || err?.message || err),
                { type: "danger", sticky: true },
            );
            throw err;
        }
    },
});
