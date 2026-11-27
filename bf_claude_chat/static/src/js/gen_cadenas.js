/** @odoo-module **/

// Le cadenas d'une réponse : comment CE tour est parti vers le modèle.
//
// Trois états, parce que le relais de masque remplace deux familles séparément : les
// identifiants (courriels, téléphones, NAS) et les noms de personnes et d'organisations.
// Un locataire dont la table ne connaît pas les contacts, et un tour que le contexte
// exclut, partent avec leurs NOMS en clair et leurs identifiants masqués : un seul état
// cacherait cette moitié-là.
//
// ⚠️ Ce que l'état dit, c'est le MASQUAGE QUI ÉTAIT EN SERVICE pour ce tour, pas une
// garantie de couverture. Le relais remplace les noms de son répertoire : un nom qu'il ne
// connaît pas, écrit en NFD, ou logé dans une clé d'argument d'outil, part en clair sous un
// cadenas « Masqué ». Les libellés le disent donc au mot : « masquage complet », jamais
// « tous les noms ont été remplacés ».
//
// 🔴 Et un trou de plus, qui touche des noms que le relais CONNAÎT : les blocs de pensée
// du modèle repartent tels quels, parce que leur signature les scelle. Le CLI les rejoue à
// chaque requête, donc une conversation ouverte AVANT que les noms soient masqués
// renvoie ses anciennes pensées, avec leurs noms et leurs numéros en clair,
// sous un cadenas « Masqué ». Une
// phrase de plus le dit, sur les états qui masquent.
//
// ⚠️ Deux sources, et l'infobulle le dit toujours. Au départ du tour, le pont n'a que sa
// sonde : elle donne le MODE du relais, pas ce qui est parti. À la fin, le relais a compté
// pour ce tour-là, et ce compte remplace l'annonce. Un cadenas annoncé que rien n'a
// confirmé se présente comme tel, en pointillé : le relais a pu changer de mode entre la
// sonde et la requête.
//
// Rien du tout — pas même « en clair » — quand le champ est vide : le système n'a pas de
// relais, ou le tour est d'avant ce cadenas. Afficher un cadenas ouvert là dirait qu'un
// masque a manqué à son devoir là où il n'y en a jamais eu.

import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

const ETATS = {
    complet: {
        icone: "fa-lock",
        classe: "bf-cadenas-complet",
        libelle: () => _t("Masked"),
        quoi: () => _t(
            "Full masking was on for this turn: identifiers, and the names the directory knows."),
    },
    identifiants: {
        icone: "fa-unlock-alt",
        classe: "bf-cadenas-partiel",
        libelle: () => _t("Names in the clear"),
        quoi: () => _t(
            "Identifier masking was on (emails, phone numbers, SINs); name masking was not."),
    },
    clair: {
        icone: "fa-unlock",
        classe: "bf-cadenas-clair",
        libelle: () => _t("Not masked"),
        quoi: () => _t("No masking was on: this turn left as it was written."),
    },
};

export class GenCadenas extends Component {
    static template = "bf_claude_chat.GenCadenas";
    static props = { msg: Object };

    get etat() {
        return ETATS[this.props.msg.masque] || null;
    }

    get classe() {
        const etat = this.etat;
        if (!etat) return "";
        return etat.classe + (this.props.msg.masque_constate ? "" : " bf-cadenas-annonce");
    }

    get icone() {
        return this.etat ? this.etat.icone : "";
    }

    get libelle() {
        return this.etat ? this.etat.libelle() : "";
    }

    get title() {
        const etat = this.etat;
        if (!etat) return "";
        const source = this.props.msg.masque_constate
            ? _t("Counted by the relay on what actually left.")
            : _t("Announced when the turn started; never confirmed.");
        const lignes = [etat.quoi()];
        if (this.props.msg.masque !== "clair") {
            // ⚠️ Une seule chaîne, sur une seule ligne : deux littéraux accolés sont du
            // Python, pas du JavaScript, et l'extracteur de catalogue n'accepte de toute
            // façon qu'un littéral.
            lignes.push(_t("The model's own earlier thinking is replayed as it was written, so a conversation older than name masking can carry names in the clear."));
        }
        lignes.push(source);
        return lignes.join("\n");
    }
}
