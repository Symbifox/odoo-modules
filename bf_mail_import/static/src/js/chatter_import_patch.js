/** @odoo-module **/

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.actionService = useService("action");
    },

    async onClickImportEml() {
        const thread = this.state.thread;
        if (!thread?.id) return;
        await this.actionService.doAction(
            {
                type: "ir.actions.act_window",
                name: "Importer des courriels (.eml)",
                res_model: "bf.mail.import.wizard",
                views: [[false, "form"]],
                target: "new",
                context: {
                    active_model: thread.model,
                    active_id: thread.id,
                },
            },
            {
                onClose: () => {
                    this.load(this.state.thread, ["messages"]);
                },
            }
        );
    },
});
