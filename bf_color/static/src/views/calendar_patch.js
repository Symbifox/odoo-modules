/**
 * Free colors in calendar views, opt-in per view: a calendar whose arch lists
 * ``<field name="color_resolved" invisible="1"/>`` paints each event with the
 * color bf_color resolves for the current user. An event without one keeps
 * the view's own color (``color=`` attribute), so nothing changes on screen
 * until a color is set.
 *
 * Odoo paints a CSS color as a saturated background with the default text
 * color. Free colors are painted like Odoo's own palette instead: soft
 * background (55 % white), full color on the edge, readable text.
 */
import { patch } from "@web/core/utils/patch";
import { CalendarModel } from "@web/views/calendar/calendar_model";
import { CalendarCommonRenderer } from "@web/views/calendar/calendar_common/calendar_common_renderer";
import { CalendarCommonPopover } from "@web/views/calendar/calendar_common/calendar_common_popover";
import { CalendarYearRenderer } from "@web/views/calendar/calendar_year/calendar_year_renderer";
import { isHex, mixWhite, rgbString, textColor } from "../core_utils";

patch(CalendarModel.prototype, {
    normalizeRecord(rawRecord) {
        const record = super.normalizeRecord(...arguments);
        if (this.meta.fieldNames.includes("color_resolved") && isHex(rawRecord.color_resolved)) {
            record.colorIndex = rawRecord.color_resolved;
        }
        return record;
    },
});

export function paintFreeColor(el, color) {
    if (!isHex(color)) {
        return;
    }
    const subtle = mixWhite(color, 0.55);
    el.style.backgroundColor = "";
    el.classList.add("o_bf_color_event");
    el.style.setProperty("--fc-event-bg-color", subtle);
    el.style.setProperty("--fc-bg-event-color", subtle);
    el.style.setProperty("--o-event-bg", color);
    el.style.setProperty("--o-event-bg--subtle-rgb", rgbString(subtle));
    el.style.setProperty("--fc-event-border-color", mixWhite(color, 0.5));
    if (!el.classList.contains("o_event_dot")) {
        el.style.setProperty("--fc-event-text-color", textColor(subtle));
    }
}

patch(CalendarCommonRenderer.prototype, {
    onEventDidMount(info) {
        super.onEventDidMount(...arguments);
        const record = this.props.model.records[info.event.id];
        if (record) {
            paintFreeColor(info.el, record.colorIndex);
        }
    },
});

patch(CalendarYearRenderer.prototype, {
    onEventDidMount(info) {
        super.onEventDidMount(...arguments);
        const record = this.props.model.records[info.event.id];
        if (record) {
            paintFreeColor(info.el, record.colorIndex);
        }
    },
});

patch(CalendarCommonPopover.prototype, {
    get bfHeaderStyle() {
        const color = this.props.record.colorIndex;
        if (!isHex(color)) {
            return "";
        }
        const header = mixWhite(color, 0.65);
        return `background-color: ${header}; color: ${textColor(header)};`;
    },
});
