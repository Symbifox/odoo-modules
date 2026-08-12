/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

const messageActionsRegistry = registry.category("mail.message/actions");

// Shown on messages that can carry a bf.email mirror: real emails and
// chatter comments (which project when they notified by email) — never
// internal notes or system notifications.
function bfEmailishCondition(component) {
    const message = component.props.message;
    return (
        Boolean(message?.id) &&
        component.store?.self?.isInternalUser &&
        (message.message_type === "email" ||
            (message.message_type === "comment" && !message.is_note))
    );
}

function bfCallMessageAction(methodName) {
    return async (component) => {
        const message = component.props.message;
        const result = await component.env.services.orm.call(
            "mail.message",
            methodName,
            [[message.id]]
        );
        if (result) {
            component.env.services.action.doAction(result);
        }
    };
}

messageActionsRegistry.add("bf-email-mark-handled", {
    condition: bfEmailishCondition,
    icon: "fa fa-check",
    title: _t("Marquer traité (courriels)"),
    onClick: bfCallMessageAction("action_bf_mark_handled"),
    sequence: 76,
});

messageActionsRegistry.add("bf-email-snooze", {
    condition: bfEmailishCondition,
    icon: "fa fa-clock-o",
    title: _t("Reporter (courriels)"),
    onClick: bfCallMessageAction("action_bf_snooze"),
    sequence: 77,
});

messageActionsRegistry.add("bf-email-unhandle", {
    condition: bfEmailishCondition,
    icon: "fa fa-inbox",
    title: _t("Remettre en boîte (courriels)"),
    onClick: bfCallMessageAction("action_bf_unhandle"),
    sequence: 78,
});

messageActionsRegistry.add("bf-download-eml", {
    condition: (component) => {
        const message = component.props.message;
        return Boolean(message?.id) && component.store?.self?.isInternalUser;
    },
    icon: "fa fa-download",
    title: _t("Télécharger en .eml"),
    onClick: async (component) => {
        const message = component.props.message;
        const result = await component.env.services.orm.call(
            "mail.message",
            "action_download_eml",
            [[message.id]]
        );
        if (result) {
            component.env.services.action.doAction(result);
        }
    },
    sequence: 80,
});
