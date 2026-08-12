/** @odoo-module **/

import { AttendeeCalendarModel } from "@calendar/views/attendee_calendar/attendee_calendar_model";
import { patch } from "@web/core/utils/patch";

/**
 * Patch AttendeeCalendarModel to use the event's `color` field
 * when set (non-zero), instead of the default partner-based coloring.
 *
 * This allows Nextcloud-synced events to display colors based on
 * their source calendar configuration.
 */
patch(AttendeeCalendarModel.prototype, {
    async updateAttendeeData(data) {
        await super.updateAttendeeData(data);
        // After the base logic sets colorIndex from partner IDs,
        // override it with the event's color field if non-zero.
        for (const record of Object.values(data.records)) {
            const eventColor = record.rawRecord.color;
            if (eventColor) {
                record.colorIndex = eventColor;
            }
        }
    },
});
