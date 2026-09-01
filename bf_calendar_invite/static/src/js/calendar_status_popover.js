/** @odoo-module **/

/**
 * Two things on the calendar popover that otherwise cost a trip through the
 * form: setting the meeting's status, and poking a guest who has not shown up.
 *
 * The status buttons deliberately copy the shape of core's Yes/No/Maybe
 * attendee group sitting next to them. They answer a different question — is
 * the meeting happening, rather than am I going — and putting them in a
 * different shape would suggest they are a different kind of control than they
 * are.
 */

import { patch } from "@web/core/utils/patch";
import { AttendeeCalendarCommonPopover } from "@calendar/views/attendee_calendar/common/attendee_calendar_common_popover";
import { AttendeeCalendarCommonRenderer } from "@calendar/views/attendee_calendar/common/attendee_calendar_common_renderer";

patch(AttendeeCalendarCommonPopover.prototype, {
    /**
     * The three statuses, read off the field itself.
     *
     * ⚠️ Not a hand-written list with `_t()` labels, which is what this was at
     * first and what put an untranslated "Confirmed" between "Tentative" and
     * "Annulé" in a French interface. JavaScript terms live in ONE dictionary
     * shared by every installed module, keyed by the English source string: a
     * word as ordinary as "Cancelled" is already claimed by somebody else, and
     * whoever loads last decides how it reads here.
     *
     * `props.model.fields` carries the selection Odoo has already translated
     * for this field, per field and with no collision possible. It also means
     * adding a status is a one-line change in Python.
     */
    get bfStatusChoices() {
        const field = this.props.model.fields.bf_event_status;
        return (field?.selection || []).map(([value, label]) => ({ value, label }));
    },

    get bfEventStatus() {
        return this.props.record.rawRecord.bf_event_status;
    },

    /**
     * Hidden unless the user may actually write. Core's own footer buttons key
     * on `user_can_edit`, and a button group that silently fails to save is
     * worse than no button group: the popover closes either way, so the failure
     * looks like a successful change until the page is reloaded.
     *
     * Also hidden on a recurring event. Writing the status there raises the
     * "this event / this and following / all events" question, and answering it
     * from a popover with no way to show the choice would silently pick one.
     */
    get bfDisplayStatusChoice() {
        return (
            this.isEventEditable &&
            !this.props.record.rawRecord.recurrency &&
            this.bfEventStatus !== undefined
        );
    },

    async bfChangeStatus(status) {
        const record = this.props.record;
        if (record.rawRecord.bf_event_status === status) {
            return this.props.close();
        }
        await this.orm.write(this.props.model.resModel, [record.id], {
            bf_event_status: status,
        });
        await this.props.model.load();
        this.props.close();
    },

    async bfOnClickPoke() {
        const action = await this.orm.call("calendar.event", "action_bf_poke", [
            [this.props.record.id],
        ]);
        this.props.close();
        this.actionService.doAction(action);
    },
});

/**
 * A cancelled meeting has to LOOK cancelled in the grid.
 *
 * Setting the status is only half the request: a cancelled event that renders
 * exactly like a confirmed one still has to be opened to be read, which is the
 * trip the whole feature exists to save.
 *
 * Done by adding a class rather than by extending the event template, because
 * the class has to reach the chip element itself — the template only fills its
 * inside — and because `eventClassNames` is the hook core provides for exactly
 * this (`o_event_hatched`, `o_past_event` are its own uses of it).
 */
patch(AttendeeCalendarCommonRenderer.prototype, {
    eventClassNames(info) {
        const classes = super.eventClassNames(info);
        const record = this.props.model.records[info.event.id];
        if (record?.rawRecord?.bf_event_status === "cancelled") {
            classes.push("bf_event_cancelled");
        }
        return classes;
    },
});
