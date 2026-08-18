/** @odoo-module **/

/**
 * Sélecteur de fiche cible : une seule zone de saisie.
 *
 * Remplace le couple « liste de modèles + many2one » du widget `reference`
 * standard, et le champ « Lien rapide » séparé que deux assistants portaient.
 * Les résultats viennent de `bf.chatter.target.search_targets`, donc le rendu
 * est celui de la recherche universelle : groupés par modèle, avec icône,
 * ligne de contexte, et les fiches terminées grisées et biffées.
 */

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { AutoComplete } from "@web/core/autocomplete/autocomplete";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const MIN_QUERY_LENGTH = 2;

export class ChatterTargetPicker extends Component {
    static template = "bf_chatter_target.Picker";
    static components = { AutoComplete };
    static props = {
        ...standardFieldProps,
        placeholder: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
    }

    get value() {
        return this.props.record.data[this.props.name] || false;
    }

    get displayName() {
        return this.value ? this.value.displayName || "" : "";
    }

    get placeholder() {
        return this.props.placeholder || _t("Nom, numéro, task:22299, ou URL Odoo…");
    }

    get sources() {
        return [{
            options: this.loadOptions.bind(this),
            optionTemplate: "bf_chatter_target.Option",
            placeholder: _t("Recherche…"),
        }];
    }

    /**
     * Une option par fiche. `groupLabel` n'est posé que sur la première d'un
     * groupe : le gabarit s'en sert pour dessiner l'en-tête du modèle, ce que
     * la liste plate d'AutoComplete ne sait pas faire autrement.
     */
    async loadOptions(request) {
        const query = (request || "").trim();
        if (query.length < MIN_QUERY_LENGTH) {
            return [{
                label: _t("Tape au moins deux caractères, un numéro, ou colle une URL Odoo."),
                unselectable: true,
                classList: "text-muted fst-italic",
            }];
        }
        let groups;
        try {
            groups = await this.orm.call("bf.chatter.target", "search_targets", [query]);
        } catch (e) {
            console.error("bf_chatter_target: erreur RPC", e);
            return [{
                label: _t("Recherche impossible pour le moment."),
                unselectable: true,
                classList: "text-danger fst-italic",
            }];
        }

        const options = [];
        for (const group of groups) {
            let first = true;
            for (const result of group.results) {
                options.push({
                    label: result.name,
                    groupLabel: first ? group.model_label : false,
                    icon: group.icon,
                    detail: result.detail || "",
                    closed: Boolean(result.closed),
                    resModel: group.model,
                    resId: result.id,
                });
                first = false;
            }
        }
        if (!options.length) {
            options.push({
                label: _t("Aucune fiche trouvée"),
                unselectable: true,
                classList: "text-muted fst-italic",
            });
        }
        return options;
    }

    onSelect(option) {
        if (!option.resModel || !option.resId) {
            return;
        }
        this.props.record.update({
            [this.props.name]: {
                resModel: option.resModel,
                resId: option.resId,
                displayName: option.label,
            },
        });
    }

    onClear() {
        this.props.record.update({ [this.props.name]: false });
    }

    /** Ouvre la fiche choisie, pour vérifier qu'on vise bien la bonne. */
    get targetUrl() {
        const value = this.value;
        return value ? `/odoo/m-${value.resModel}/${value.resId}` : "#";
    }
}

export const chatterTargetPicker = {
    component: ChatterTargetPicker,
    displayName: _t("Cible de chatter"),
    supportedTypes: ["reference"],
    extractProps({ attrs, options }) {
        return {
            placeholder: attrs.placeholder || options.placeholder,
        };
    },
};

registry.category("fields").add("bf_chatter_target", chatterTargetPicker);
