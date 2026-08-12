/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

const messageActionsRegistry = registry.category("mail.message/actions");

messageActionsRegistry.add("create-knowledge-item", {
    condition: (component) => {
        const thread = component.props.thread;
        return (
            thread?.model === "project.task" &&
            component.props.message.id &&
            component.store?.self?.isInternalUser
        );
    },
    icon: "fa fa-lightbulb-o",
    title: _t("Capturer dans la matrice"),
    onClick: async (component) => {
        const message = component.props.message;
        const thread = component.props.thread;

        const result = await component.env.services.orm.call(
            "project.task",
            "action_create_knowledge_item_from_message",
            [],
            {
                task_id: thread.id,
                message_id: message.id,
                author_name: message.author?.name || "",
                message_body: message.body || "",
            }
        );

        if (result) {
            component.env.services.action.doAction(result);
        }
    },
    sequence: 75,
});
