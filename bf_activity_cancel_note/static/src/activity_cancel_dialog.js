import { Component, useState } from "@odoo/owl";

import { Dialog } from "@web/core/dialog/dialog";

/**
 * Asks how to cancel an activity: cancel and leave a note in the chatter
 * (the highlighted choice), or discard it without a trace, as Odoo does.
 * Closing the dialog keeps the activity.
 */
export class ActivityCancelDialog extends Component {
    static components = { Dialog };
    static props = ["activity", "onCancelWithNote", "onDiscard", "close"];
    static template = "bf_activity_cancel_note.ActivityCancelDialog";

    setup() {
        this.state = useState({ reason: "", busy: false });
    }

    async run(action) {
        // One click only; the dialog closes even when the call fails, so it
        // never stays open over an error.
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await action();
        } finally {
            this.props.close();
        }
    }

    cancelWithNote() {
        return this.run(() => this.props.onCancelWithNote(this.state.reason.trim()));
    }

    discard() {
        return this.run(() => this.props.onDiscard());
    }
}
