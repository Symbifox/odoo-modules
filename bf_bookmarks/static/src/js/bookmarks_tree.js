/** @odoo-module **/
import { Component, useState } from "@odoo/owl";
import { GLISSE } from "./bookmarks_data";

/**
 * Un signet : icône du site, titre, étoile, et, là où c'est permis, glisser-déposer.
 *
 * `glisse` : "ranger" (page, section modifiable : vers un autre dossier ou rang),
 * "favori" (réordonner les favoris), ou absent.
 */
export class BookmarkLink extends Component {
    static template = "bf_bookmarks.Link";
    static props = {
        lien: Object,
        gestes: Object,
        glisse: { type: String, optional: true },
        parentId: { type: String, optional: true },
        index: { type: Number, optional: true },
        onDeposer: { type: Function, optional: true },
        perso: { type: Boolean, optional: true },
    };

    onDragStart(ev) {
        if (!this.props.glisse) return;
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData(GLISSE, JSON.stringify({ id: this.props.lien.id, glisse: this.props.glisse }));
    }

    onDragOver(ev) {
        if (this.props.onDeposer && ev.dataTransfer.types.includes(GLISSE)) {
            ev.preventDefault();
            ev.dataTransfer.dropEffect = "move";
        }
    }

    onDrop(ev) {
        if (!this.props.onDeposer) return;
        const brut = ev.dataTransfer.getData(GLISSE);
        if (!brut) return;
        ev.preventDefault();
        ev.stopPropagation();
        this.props.onDeposer(JSON.parse(brut), this.props.parentId, this.props.index, this.props.lien);
    }
}

/** Un dossier et son contenu, repliable ; dépôt possible sur son titre. */
export class BookmarkFolderTree extends Component {
    static template = "bf_bookmarks.FolderTree";
    static components = { BookmarkFolderTree, BookmarkLink };
    static props = {
        node: Object,
        gestes: Object,
        modifiable: { type: Boolean, optional: true },
        onDeposer: { type: Function, optional: true },
        perso: { type: Boolean, optional: true },
        depth: { type: Number, optional: true },
        open: { type: Boolean, optional: true },
    };

    setup() {
        this.state = useState({ open: this.props.open ?? false, survol: false });
    }

    toggle() {
        this.state.open = !this.state.open;
    }

    get peutRecevoir() {
        return Boolean(this.props.modifiable && this.props.onDeposer);
    }

    onDragOver(ev) {
        if (this.peutRecevoir && ev.dataTransfer.types.includes(GLISSE)) {
            ev.preventDefault();
            this.state.survol = true;
        }
    }

    onDragLeave() {
        this.state.survol = false;
    }

    onDrop(ev) {
        this.state.survol = false;
        if (!this.peutRecevoir) return;
        const brut = ev.dataTransfer.getData(GLISSE);
        if (!brut) return;
        ev.preventDefault();
        ev.stopPropagation();
        // Déposé sur le titre : à la fin du dossier.
        this.props.onDeposer(JSON.parse(brut), this.props.node.id, this.props.node.children.length, null);
        this.state.open = true;
    }
}
