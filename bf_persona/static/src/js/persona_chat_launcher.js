/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Client action that opens the Claude Chat systray panel and pre-fills its
 * input with a given prompt. Optionally auto-sends.
 *
 * Usage from a Python button:
 *   return {
 *       "type": "ir.actions.client",
 *       "tag": "claude_chat_launch",
 *       "params": {"prompt": "/persona refresh ACME Corp.", "autosend": false},
 *   }
 *
 * The systray (bf_claude_chat) listens for the `bf-claude-chat-open` window
 * event and reacts. Decoupled by design so any module can dispatch it.
 */
function claudeChatLaunch(env, action) {
    const params = (action && action.params) || {};
    window.dispatchEvent(new CustomEvent("bf-claude-chat-open", {
        detail: {
            prompt: params.prompt || "",
            autosend: !!params.autosend,
        },
    }));
    return { type: "ir.actions.act_window_close" };
}

registry.category("actions").add("claude_chat_launch", claudeChatLaunch);
