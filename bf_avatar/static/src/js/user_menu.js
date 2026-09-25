/** @odoo-module **/
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { _t } from "@web/core/l10n/translation";

function avatarComposerItem(env) {
    return {
        type: "item",
        id: "bf_avatar_composer",
        description: _t("My avatar"),
        show: () => Boolean(session.bf_avatar_composer),
        callback: () => env.services.action.doAction("bf_avatar.action_bf_avatar_composer"),
        sequence: 45,
    };
}

registry.category("user_menuitems").add("bf_avatar_composer", avatarComposerItem);
