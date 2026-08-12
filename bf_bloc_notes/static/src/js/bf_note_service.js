/** @odoo-module **/
import { registry } from "@web/core/registry";
import { BfNoteQuickCreateDialog } from "./bf_note_quick_create";

/**
 * Service exposing helpers to open the bloc-notes:
 *  - openQuickCreate({ resModel, resId }) → opens the inline dialog
 *  - openList()                            → opens the kanban with "Mes notes" filter
 *
 * Reads the current breadcrumb to auto-fill res_model / res_id.
 */
export const bfNoteService = {
    dependencies: ["action", "dialog", "orm", "notification"],
    start(env, { action, dialog, orm, notification }) {
        const getCurrentContext = () => {
            // Only auto-link when the user is on a form view of a single record.
            // Falling back to context.active_id leaks stale ids from previous
            // breadcrumbs and pins notes/activities on the wrong chatter.
            try {
                const controller = action?.currentController;
                if (!controller) return {};
                const view = controller.view?.type || controller.props?.type;
                if (view && view !== "form") return {};
                const props = controller.props || {};
                const resModel = props.resModel;
                const resId = props.resId;
                if (resModel && typeof resId === "number" && resId) {
                    return { resModel, resId };
                }
            } catch (_e) {
                /* ignore */
            }
            return {};
        };

        return {
            openQuickCreate(opts = {}) {
                const ctx = getCurrentContext();
                const resModel = opts.resModel ?? ctx.resModel;
                const resId = opts.resId ?? ctx.resId;
                dialog.add(BfNoteQuickCreateDialog, {
                    resModel: resModel || false,
                    resId: resId || false,
                    onSaved: () => {
                        // notification already shown in dialog
                    },
                });
            },
            openList() {
                action.doAction({
                    type: "ir.actions.act_window",
                    name: "Bloc-notes",
                    res_model: "bf.note",
                    views: [
                        [false, "kanban"],
                        [false, "list"],
                        [false, "form"],
                    ],
                    context: { search_default_my_notes: 1 },
                    target: "current",
                });
            },
        };
    },
};

registry.category("services").add("bf_note", bfNoteService);
