/** @odoo-module **/
// Voix d'assemblée : la précision stockée reste à quatre décimales (quotes-parts,
// comparaisons de majorité à VOTE_DIGITS), mais l'écran n'imprime que les
// décimales utiles : « 585 », « 12,3456 », jamais « 585,0000 ». Le widget d'Odoo
// remet toujours les zéros de fin jusqu'à la précision du champ, d'où ce widget.
// Le même format est inscrit aux « formatters » : le total en pied de liste et
// les sous-totaux de groupe le prennent par le nom du widget.
import { registry } from "@web/core/registry";
import { formatFloat } from "@web/views/fields/formatters";
import { FloatField, floatField } from "@web/views/fields/float/float_field";

const VOTE_DIGITS = [16, 4];

export function formatVotes(value, options = {}) {
    return formatFloat(value, {
        ...options,
        digits: options.digits || VOTE_DIGITS,
        trailingZeros: false,
    });
}

export class VotesField extends FloatField {
    get formattedValue() {
        if (
            !this.props.formatNumber ||
            this.props.humanReadable ||
            (this.props.inputType === "number" && !this.props.readonly && this.value)
        ) {
            return super.formattedValue;
        }
        return formatVotes(this.value, { digits: this.props.digits });
    }
}

export const votesField = { ...floatField, component: VotesField };

registry.category("fields").add("bf_property_votes", votesField);
registry.category("formatters").add("bf_property_votes", formatVotes);
