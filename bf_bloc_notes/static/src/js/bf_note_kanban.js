/**
 * Mises en page du bloc-notes : cartes façon Google Keep,
 * minimaliste, liste. Le choix est une préférence de l'usager (res.users),
 * partagée avec la page /notes et Symbifox Mobile ; le menu de la barre la
 * change et l'enregistre.
 */
import { onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { kanbanView } from "@web/views/kanban/kanban_view";

const PREF_FIELDS = ["bf_note_layout", "bf_note_density", "bf_note_color_style", "bf_note_group_by_color"];

export class BfNoteKanbanController extends KanbanController {
    static template = "bf_bloc_notes.NoteKanbanView";
    static components = { ...KanbanController.components, Dropdown, DropdownItem };

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.bfPrefs = useState({
            bf_note_layout: "cards",
            bf_note_density: "comfortable",
            bf_note_color_style: "fill",
            bf_note_group_by_color: false,
        });
        this.bfLayouts = [
            { value: "cards", label: _t("Cards"), icon: "fa-th-large" },
            { value: "minimal", label: _t("Minimal"), icon: "fa-bars" },
            { value: "list", label: _t("List"), icon: "fa-list" },
        ];
        onWillStart(async () => {
            const [prefs] = await this.orm.read("res.users", [user.userId], PREF_FIELDS);
            Object.assign(this.bfPrefs, prefs);
            if (prefs.bf_note_group_by_color && !this.env.searchModel.groupBy.length) {
                const item = this.env.searchModel
                    .getSearchItems((i) => i.type === "groupBy" && i.fieldName === "color")[0];
                if (item) {
                    this.env.searchModel.toggleSearchItem(item.id);
                }
            }
        });
    }

    get className() {
        const p = this.bfPrefs;
        return [
            super.className,
            `o_bf_notes_layout_${p.bf_note_layout}`,
            `o_bf_notes_density_${p.bf_note_density}`,
            `o_bf_notes_color_${p.bf_note_color_style}`,
        ].filter(Boolean).join(" ");
    }

    get bfLayoutIcon() {
        return this.bfLayouts.find((l) => l.value === this.bfPrefs.bf_note_layout)?.icon || "fa-th-large";
    }

    async bfSetPref(field, value) {
        this.bfPrefs[field] = value;
        await this.orm.write("res.users", [user.userId], { [field]: value });
    }
}

registry.category("views").add("bf_note_kanban", {
    ...kanbanView,
    Controller: BfNoteKanbanController,
});
