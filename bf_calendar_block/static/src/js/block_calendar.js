/** @odoo-module **/

/**
 * Plages de travail typées dans la grille.
 *
 * Le pictogramme du type se met devant le titre, comme ↪ pour une plage
 * retenue : la grille n'a pas d'autre place où le dire d'un coup d'œil. Il
 * n'est pas écrit dans le nom de l'événement : il changerait avec le type, et
 * Nextcloud recevrait un titre chargé de pictogrammes.
 *
 * Sur l'événement privé d'un autre, le cœur rend `bf_block_glyph` vide : la
 * grille montre « Occupé », sans dire à quoi.
 */

import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { CalendarModel } from "@web/views/calendar/calendar_model";
import { BadgeSelectionField, badgeSelectionField } from "@web/views/fields/badge_selection/badge_selection_field";
import { CalendarQuickCreateFormController } from "@calendar/views/calendar_form/calendar_quick_create";

patch(CalendarModel.prototype, {
    normalizeRecord(rawRecord) {
        const record = super.normalizeRecord(...arguments);
        const glyph = rawRecord.bf_block_glyph;
        if (glyph && typeof record.title === "string" && !record.title.startsWith(glyph)) {
            record.title = `${glyph} ${record.title}`;
        }
        return record;
    },
});

/**
 * Les badges du cœur, à une différence près : recliquer le type choisi le
 * retire. Le widget du cœur ne le permet que pour une sélection, pas pour un
 * Many2one, et une plage tracée par erreur garderait sinon son type.
 */
export class BfBlockBadgesField extends BadgeSelectionField {
    onChange(value) {
        if (this.type === "many2one" && value !== false && value === this.value) {
            this.props.record.update({ [this.props.name]: false });
            return;
        }
        super.onChange(value);
    }
}

registry.category("fields").add("bf_block_badges", {
    ...badgeSelectionField,
    component: BfBlockBadgesField,
});

/**
 * « Plus d'options » garde le type choisi : le cœur ne
 * passe au formulaire complet qu'une liste fixe de champs. Même façon de faire
 * que bf_meeting pour l'ordre du jour : appeler la méthode du cœur avec un
 * `props.goToFullEvent` qui ajoute la clé au contexte.
 */
patch(CalendarQuickCreateFormController.prototype, {
    goToFullEvent() {
        const type = this.model.root.data.bf_block_type_id;
        if (!type) {
            return super.goToFullEvent(...arguments);
        }
        const goToFullEvent = this.props.goToFullEvent;
        const props = {
            ...this.props,
            goToFullEvent: (context) =>
                goToFullEvent({ ...context, bf_creation_rapide_type_plage: type[0] }),
        };
        return super.goToFullEvent.call(Object.create(this, { props: { value: props } }));
    },
});
