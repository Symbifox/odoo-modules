/** @odoo-module **/
// Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
//
// The "Calendars" section of the Calendar app lists by default only the
// calendars that have a meeting in the period on screen. Here it always lists
// every calendar of the current user, in their own order and color, so one can
// be unchecked (or checked again) whatever the period shown.

import { patch } from "@web/core/utils/patch";
import { user } from "@web/core/user";
import { CalendarModel } from "@web/views/calendar/calendar_model";

const FIELD = "bf_dav_calendar_id";

patch(CalendarModel.prototype, {
    async loadDynamicFilterSection(data, fieldName, filterInfo, previousSection) {
        const section = await super.loadDynamicFilterSection(...arguments);
        if (fieldName !== FIELD || this.meta.resModel !== "calendar.event" || !section) {
            return section;
        }
        let calendars;
        try {
            calendars = await this.orm.searchRead(
                "bf.dav.calendar",
                [["owner_id", "=", user.userId]],
                ["display_name", "odoo_color"]
            );
        } catch {
            return section;
        }
        const previousFilters = previousSection ? previousSection.filters : [];
        const own = calendars.map((cal) => {
            const filter =
                section.filters.find((f) => f.value === cal.id) ||
                this.makeFilterDynamic(
                    filterInfo,
                    previousFilters.find((f) => f.type === "dynamic" && f.value === cal.id),
                    fieldName,
                    { id: cal.id, [fieldName]: [cal.id, cal.display_name], colorIndex: cal.odoo_color },
                    []
                );
            filter.colorIndex = cal.odoo_color;
            return filter;
        });
        const ownIds = new Set(calendars.map((cal) => cal.id));
        const others = section.filters
            .filter((f) => !ownIds.has(f.value))
            .sort((a, b) => (a.value ? 0 : 1) - (b.value ? 0 : 1));
        section.filters = [...own, ...others];
        section.canCollapse = section.filters.length > 2;
        return section;
    },
});
