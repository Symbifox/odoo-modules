/** @odoo-module **/
import { Component, useRef, useState } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { aplatir, chargerSignets, chercher, gestes, nouvelOrdre, signetsConnus } from "./bookmarks_data";
import { BookmarkFolderTree, BookmarkLink } from "./bookmarks_tree";

/** L'icône Signets de la barre : favoris, récents, recherche, ajout de la page courante. */
export class BookmarksSystray extends Component {
    static template = "bf_bookmarks.Systray";
    static components = { Dropdown, BookmarkFolderTree, BookmarkLink };
    static props = [];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dropdown = useDropdownState();
        this.state = useState({ sections: [], favoris: [], recents: [], requete: "",
                                charge: false, erreur: false });
        this.recherche = useRef("recherche");
        this.gestes = gestes(this.orm, () => this.charger(), {
            notification: useService("notification"), action: useService("action") });
        this.onDeposerFavori = this.onDeposerFavori.bind(this);
    }

    appliquer(donnees) {
        Object.assign(this.state, donnees, { erreur: false, charge: true });
    }

    async charger({ partager = false } = {}) {
        try {
            this.appliquer(await chargerSignets(this.orm, { partager }));
        } catch {
            // Rien encore à montrer : on le dit. Sinon le dernier état lu reste affiché.
            if (!signetsConnus()) {
                this.state.erreur = true;
            }
            this.state.charge = true;
        }
    }

    /** Au survol ou au focus de l'icône : la première lecture part avant le clic. */
    precharger() {
        if (!signetsConnus()) {
            this.charger({ partager: true });
        }
    }

    async onBeforeOpen() {
        // Chaque ouverture repart d'une recherche vide.
        this.state.requete = "";
        // Déjà lu dans cette page : le menu s'ouvre tout de suite sur le dernier état
        // connu, et la lecture serveur, qui porte les droits, le rafraîchit en
        // revenant. La première fois seulement, on l'attend.
        const connus = signetsConnus();
        if (connus) {
            this.appliquer(connus);
        }
        const lecture = this.charger({ partager: true });
        if (!connus) {
            await lecture;
        }
    }

    /** Le curseur est déjà dans la recherche : on ouvre, on tape. */
    onOpened() {
        this.recherche.el?.focus();
    }

    onDeposerFavori(donnee, _parent, _index, cible) {
        if (donnee.glisse !== "favori") return;
        this.gestes.ordonnerFavoris(nouvelOrdre(this.state.favoris, donnee.id, cible?.id));
    }

    get resultats() {
        // Dérivé de l'état réactif à chaque frappe : une copie gardée à part n'était pas
        // celle que lisait le rendu (trouvé au parcours navigateur).
        return chercher(aplatir(this.state.sections), this.state.requete, 30);
    }

    /**
     * « + » : garder la page Odoo où l'on est (une tâche, un client, un rapport).
     * Titre et adresse pré-remplis, modifiables ; dossier Perso par défaut, et
     * seulement ceux où l'on peut écrire.
     */
    ajouterPageActuelle() {
        const titre = (document.title || "").replace(/\s*[-|–]\s*Odoo\s*$/i, "").trim();
        const adresse = window.location.href;
        this.dropdown.close();
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Ajouter un signet",
            res_model: "bf.bookmark",
            views: [[false, "form"]],
            target: "new",
            context: {
                default_name: titre || adresse,
                default_url: adresse,
                form_view_ref: "bf_bookmarks.bookmark_view_form_quick",
            },
        });
    }

    toutVoir() {
        this.dropdown.close();
        // Sans ça, Odoo empile le fil d'Ariane : /odoo/discuss/signets.
        this.action.doAction("bf_bookmarks.action_bookmarks_home", { clearBreadcrumbs: true });
    }
}

registry.category("systray").add("bf_bookmarks.systray", { Component: BookmarksSystray }, { sequence: 30 });
