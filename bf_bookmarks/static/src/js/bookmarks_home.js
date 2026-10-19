/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import {
    aplatir, chargerSignets, chercher, compter, gestes, nouvelOrdre, rangDeDepot, signetsConnus,
} from "./bookmarks_data";
import { BookmarkFolderTree, BookmarkLink } from "./bookmarks_tree";

const PORTEES = {
    company: "Entreprise",
    department: "Équipe",
    project: "Projet",
    personal: "Perso",
};

/**
 * La page Signets : accueil du module, et application installable sur un téléphone
 * (/odoo/signets). Favoris et récents en tête, une carte par section, une recherche
 * qui traverse tout, et le rangement par glisser-déposer là où l'on peut écrire.
 */
export class BookmarksHome extends Component {
    static template = "bf_bookmarks.Home";
    static components = { BookmarkFolderTree, BookmarkLink };
    // Le service d'action passe ses propres props à une action cliente.
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ sections: [], favoris: [], recents: [], requete: "" });
        this.portees = PORTEES;
        this.compter = compter;
        this.gestes = gestes(this.orm, () => this.charger(), {
            notification: useService("notification"), action: useService("action") });
        this.onDeposerFavori = this.onDeposerFavori.bind(this);
        this.onDeposerRanger = this.onDeposerRanger.bind(this);
        onWillStart(() => {
            const connus = signetsConnus();
            if (!connus) {
                return this.charger();
            }
            // Déjà lu dans cette page (par la barre) : la page s'affiche tout de suite,
            // la lecture serveur suit et la met à jour.
            Object.assign(this.state, connus);
            this.charger();
        });
    }

    async charger() {
        Object.assign(this.state, await chargerSignets(this.orm));
    }

    get resultats() {
        return chercher(aplatir(this.state.sections), this.state.requete, 100);
    }

    onDeposerFavori(donnee, _parent, _index, cible) {
        if (donnee.glisse !== "favori") return;
        this.gestes.ordonnerFavoris(nouvelOrdre(this.state.favoris, donnee.id, cible?.id));
    }

    async onDeposerRanger(donnee, parentId, index) {
        if (donnee.glisse !== "ranger" || donnee.id === parentId) return;
        const rang = rangDeDepot(this.state.sections, donnee.id, parentId, index);
        const erreur = await this.gestes.deplacer(donnee.id, parentId, rang);
        if (erreur) this.notification.add(erreur, { type: "warning" });
    }

    gerer() {
        this.action.doAction("bf_bookmarks.action_bookmark");
    }

    async ajouter() {
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "bf.bookmark",
            views: [[false, "form"]],
            target: "new",
            context: { form_view_ref: "bf_bookmarks.bookmark_view_form_quick" },
        }, { onClose: () => this.charger() });
    }
}

registry.category("actions").add("bf_bookmarks.home", BookmarksHome);
