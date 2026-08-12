/** @odoo-module **/
import { registry } from "@web/core/registry";

/**
 * Registers global hotkeys for the bloc-notes:
 *   Alt+N        → ouvre la dialog quick-create (auto-lié à la fiche courante)
 *   Alt+Shift+N  → ouvre la liste de mes notes
 *
 * Implemented as an ordinary service so we can grab the bf_note service
 * and the hotkey service at startup.
 */
export const bfNoteHotkeysService = {
    dependencies: ["hotkey", "bf_note"],
    start(env, { hotkey, bf_note }) {
        hotkey.add(
            "alt+n",
            () => bf_note.openQuickCreate(),
            { global: true, allowRepeat: false, bypassEditableProtection: true },
        );
        hotkey.add(
            "alt+shift+n",
            () => bf_note.openList(),
            { global: true, allowRepeat: false, bypassEditableProtection: true },
        );
        return {};
    },
};

registry.category("services").add("bf_note_hotkeys", bfNoteHotkeysService);
