/** @odoo-module **/

/**
 * Make "+ Nextcloud Talk" work on an event that is not saved yet.
 *
 * Core's "+ Odoo meeting" button is a dummy server method: the real work happens
 * in `CalendarFormController.beforeExecuteActionButton`, which computes the URL
 * client-side and writes it into the in-memory record. That is why it works in
 * the calendar's quick-create popover, where no record exists yet.
 *
 * Our button used to be a plain `type="object"` button, so it needed a saved
 * record and had to be hidden with `not id` — which is exactly why it only ever
 * showed up once you opened the full event form. We intercept it the same way
 * core does: ask the server for a room (the room really is created on
 * Nextcloud), then update the record locally instead of letting the button
 * round-trip to an unsaved id.
 *
 * Patching `CalendarFormController` covers the quick-create form too, since
 * `CalendarQuickCreateFormController` extends it.
 */

import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { CalendarFormController } from "@calendar/views/calendar_form/calendar_form_controller";

const NC_TALK_ACTION = "action_set_nc_talk_videocall_location";

patch(CalendarFormController.prototype, {
    setup() {
        super.setup();
        this.bfNcTalkOrm = useService("orm");
    },

    async beforeExecuteActionButton(clickParams) {
        if (clickParams.name === NC_TALK_ACTION) {
            const roomUrl = await this.bfNcTalkOrm.call(
                "calendar.event",
                "create_nc_talk_room",
                [this.model.root.data.name || ""]
            );
            if (roomUrl) {
                this.model.root.update({
                    videocall_location: roomUrl,
                    // Not a Discuss URL, so core would compute 'custom' anyway;
                    // setting it keeps the field read-write while unsaved.
                    videocall_source: "custom",
                });
            }
            return false; // handled — do not call the server button
        }
        return super.beforeExecuteActionButton(...arguments);
    },
});
