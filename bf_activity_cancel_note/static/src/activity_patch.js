import { Activity } from "@mail/core/web/activity";
import { ActivityListPopoverItem } from "@mail/core/web/activity_list_popover_item";

import { patch } from "@web/core/utils/patch";

import { ActivityCancelDialog } from "./activity_cancel_dialog";

function openCancelDialog(component, discard, done) {
    const activity = component.props.activity;
    component.env.services.dialog.add(ActivityCancelDialog, {
        activity,
        onCancelWithNote: async (reason) => {
            const activityId = activity.id;
            activity.remove();
            try {
                await component.env.services.orm.call(
                    "mail.activity",
                    "action_cancel_with_note",
                    [[activityId]],
                    { reason: reason || false }
                );
            } finally {
                // Reload either way: on failure the activity comes back instead
                // of staying hidden while it still exists.
                done();
            }
        },
        onDiscard: discard,
    });
}

patch(Activity.prototype, {
    unlink() {
        const thread = this.thread;
        openCancelDialog(
            this,
            () => super.unlink(),
            () => this.props.onActivityChanged(thread)
        );
    },
});

patch(ActivityListPopoverItem.prototype, {
    unlink() {
        openCancelDialog(
            this,
            () => super.unlink(),
            () => this.props.onActivityChanged?.()
        );
    },
});
