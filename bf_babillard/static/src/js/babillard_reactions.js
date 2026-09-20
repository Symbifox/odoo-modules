/** @odoo-module **/

import { Component } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * La barre de réactions d'une publication.
 *
 * Le catalogue étant configurable, le nombre de boutons ne se connaît qu'à
 * l'exécution : ni un champ par réaction, ni un gabarit figé ne pouvaient le
 * rendre. Tout arrive dans un seul champ calculé (`reactions`), déjà borné par
 * le serveur.
 *
 * 🔴 Ce composant ne décide RIEN. Il affiche ce que le serveur lui donne et
 * rappelle `action_basculer_reaction`, qui revérifie l'état, l'audience et le
 * catalogue. Un identifiant de réaction bricolé dans la console n'ouvre rien.
 */
export class BabillardReactions extends Component {
    static template = "bf_babillard.Reactions";
    static components = { Dropdown, DropdownItem };
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
    }

    get donnees() {
        return this.props.record.data[this.props.name] || {};
    }

    get posees() {
        return this.donnees.posees || [];
    }

    get offertes() {
        return this.donnees.offertes || [];
    }

    get etiquetteAjouter() {
        return _t("Réagir");
    }

    /**
     * Les noms de qui a réagi, un par ligne.
     *
     * Le serveur les envoie déjà triés, et il n'envoie que ce que l'audience a
     * le droit de lire : la règle d'enregistrement sur `bf.babillard.geste`
     * borne la lecture à l'audience de la publication.
     */
    survol(reaction) {
        return (reaction.noms || []).join("\n");
    }

    async basculer(reactionId) {
        const record = this.props.record;
        // ⚠️ Sur le formulaire, la publication peut porter des retouches non
        // enregistrées : `load()` les jetterait sans le dire. On enregistre
        // d'abord, comme le fait le composant de pièce jointe d'Odoo.
        if (await record.isDirty()) {
            await record.save();
        }
        await this.orm.call(
            record.resModel,
            "action_basculer_reaction",
            [[record.resId], reactionId]
        );
        await record.load();
    }
}

export const babillardReactions = {
    component: BabillardReactions,
    displayName: _t("Réactions du babillard"),
    supportedTypes: ["json"],
};

registry.category("fields").add("bf_babillard_reactions", babillardReactions);
