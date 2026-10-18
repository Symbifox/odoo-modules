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
        // override it with the event's own color: the free color bf_color
        // resolves for the current user (the Nextcloud calendar's hex)
        // when the view loads it, else the color index if non-zero.
        for (const record of Object.values(data.records)) {
            const freeColor = record.rawRecord.color_resolved;
            const eventColor = record.rawRecord.color;
            if (typeof freeColor === "string" && /^#[0-9a-f]{6}$/i.test(freeColor)) {
                record.colorIndex = freeColor;
            } else if (eventColor) {
                record.colorIndex = eventColor;
            }
        }
    },
});
