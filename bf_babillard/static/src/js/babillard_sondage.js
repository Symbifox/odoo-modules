/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Le bulletin de vote d'une publication.
 *
 * 🔴 Ce composant ne décide RIEN, comme la barre de réactions dont il reprend
 * le patron. Il affiche ce que le serveur lui donne, et chaque clic rappelle
 * une méthode qui revérifie l'état du sondage, l'audience, et que le choix
 * appartient bien à cette publication. Un identifiant bricolé dans la console
 * n'ouvre rien.
 *
 * ⚠️ Un sondage anonyme sous le seuil n'envoie AUCUN chiffre au navigateur :
 * il n'y a rien à cacher en CSS, parce qu'il n'y a rien dans les données.
 */
export class BabillardSondage extends Component {
    static template = "bf_babillard.Sondage";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ nouveau: "", enCours: false });
    }

    get donnees() {
        return this.props.record.data[this.props.name] || {};
    }

    get options() {
        return this.donnees.options || [];
    }

    get etiquetteVotants() {
        const nb = this.donnees.nb_votants || 0;
        return nb === 1 ? _t("1 personne a répondu") : _t("%s personnes ont répondu", nb);
    }

    get etiquetteFerme() {
        return _t("Sondage fermé");
    }

    get etiquetteAjouter() {
        return _t("Ajouter un choix");
    }

    survol(option) {
        return (option.noms || []).join("\n");
    }

    async _appeler(methode, args) {
        const record = this.props.record;
        // ⚠️ Comme la barre de réactions : le formulaire peut porter des
        // retouches non enregistrées, et `load()` les jetterait sans le dire.
        if (await record.isDirty()) {
            await record.save();
        }
        this.state.enCours = true;
        try {
            await this.orm.call(record.resModel, methode, [[record.resId], ...args]);
            await record.load();
        } finally {
            this.state.enCours = false;
        }
    }

    async basculer(optionId) {
        if (!this.donnees.peut_voter || this.state.enCours) {
            return;
        }
        await this._appeler("action_basculer_vote", [optionId]);
    }

    async ajouter() {
        const texte = (this.state.nouveau || "").trim();
        if (!texte || this.state.enCours) {
            return;
        }
        await this._appeler("action_ajouter_option", [texte]);
        this.state.nouveau = "";
    }

    surTouche(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.ajouter();
        }
    }
}

export const babillardSondage = {
    component: BabillardSondage,
    displayName: _t("Sondage du babillard"),
    supportedTypes: ["json"],
};

registry.category("fields").add("bf_babillard_sondage", babillardSondage);
