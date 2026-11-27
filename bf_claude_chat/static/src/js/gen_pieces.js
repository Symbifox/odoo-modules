/** @odoo-module **/

// La zone de saisie qui
// grandit avec le texte, sur les DEUX surfaces, et les pièces jointes (bouton et
// glisser-déposer).
//
// Une pièce est lue par le serveur dès qu'on la choisit : la puce dit donc AVANT
// l'envoi si Gen pourra la lire, et combien il en lira. Seul son texte part vers
// Gen, à la suite de la question, et passe par le masque des noms comme elle
// (pieces_jointes.py dit pourquoi le fichier, lui, ne part jamais).

import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";

//: Doit valoir PIECES_MAX de pieces_jointes.py (un essai garde l'accord).
export const PIECES_MAX = 5;
//: Hauteur maximale de la zone de saisie, en px, avant qu'elle défile.
export const SAISIE_MAX = 240;

/** La zone de saisie à la hauteur de son texte, jusqu'à SAISIE_MAX. */
export function ajusterSaisie(textarea) {
    if (!textarea) return;
    textarea.style.height = "auto";
    // `border-box` (Bootstrap) : la hauteur compte les bordures, que `scrollHeight`
    // ne compte pas. Sans elles, la dernière ligne était rognée de 2 px.
    const voulu = textarea.scrollHeight + textarea.offsetHeight - textarea.clientHeight;
    textarea.style.height = Math.min(voulu, SAISIE_MAX) + "px";
    // Défiler seulement au-delà du plafond : sinon une barre apparaît pour un pixel.
    textarea.style.overflowY = voulu > SAISIE_MAX ? "auto" : "hidden";
}

function taille(octets) {
    if (!octets && octets !== 0) return "";
    if (octets < 1024) return `${octets} o`;
    if (octets < 1024 * 1024) return `${Math.round(octets / 1024)} Ko`;
    return `${(octets / 1024 / 1024).toFixed(1).replace(".", ",")} Mo`;
}

export const piecesMixin = {
    piecesState() {
        return { pieces: [], depot: false };
    },

    /** Le trombone : ouvrir le sélecteur de fichiers caché. */
    onPickFiles() {
        if (this.fileInputRef && this.fileInputRef.el) this.fileInputRef.el.click();
    },

    async onFilesPicked(ev) {
        const fichiers = [...(ev.target.files || [])];
        ev.target.value = "";  // le même fichier, choisi deux fois, se relit
        await this.ajouterPieces(fichiers);
    },

    // Glisser-déposer. `dragleave` part aussi en passant sur un enfant : on ne
    // retire la surbrillance que si le pointeur quitte vraiment la zone.
    onDragOver(ev) {
        if (!ev.dataTransfer || ![...ev.dataTransfer.types].includes("Files")) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "copy";
        this.state.depot = true;
    },
    onDragLeave(ev) {
        if (ev.relatedTarget && ev.currentTarget.contains(ev.relatedTarget)) return;
        this.state.depot = false;
    },
    async onDrop(ev) {
        if (!ev.dataTransfer || !ev.dataTransfer.files.length) return;
        ev.preventDefault();
        this.state.depot = false;
        await this.ajouterPieces([...ev.dataTransfer.files]);
    },

    /** Une saisie à l'écran, sinon les pièces partiraient avec une question qu'on ne
     *  voit pas : liste seule au téléphone, plein écran sans conversation ouverte. */
    _saisieVisible() {
        const el = this.inputRef && this.inputRef.el;
        return Boolean(el && el.offsetParent !== null);
    },

    // ⚠️ Les chaînes de l'écran vivent ICI, pas dans claude_chat.js ni claude_systray.js :
    // l'extracteur de traductions d'Odoo ne trouve aucune chaîne de ces deux fichiers
    // (mesuré), et elles resteraient en anglais.
    _avisPiecesEnLecture() {
        this.notification.add(_t("Wait until the attachments are read."), { type: "info" });
    },

    /** La bulle d'une pièce envoyée seule. Le serveur enregistre la même phrase. */
    _questionSansTexte() {
        return _t("📎 Read the attachments.");
    },

    piecesEnCours() {
        return this.state.pieces.some((p) => p.chargement);
    },

    piecesPretes() {
        return this.state.pieces.filter((p) => p.id && !p.erreur);
    },

    async ajouterPieces(fichiers) {
        if (!this._saisieVisible()) {
            this.notification.add(_t("Open a conversation to attach a file."), { type: "info" });
            return;
        }
        for (const fichier of fichiers) {
            if (this.state.pieces.filter((p) => !p.erreur).length >= PIECES_MAX) {
                this.notification.add(
                    _t("At most %s attachments per message.", PIECES_MAX), { type: "warning" });
                return;
            }
            this.state.pieces.push({
                cle: `${Date.now()}-${Math.random()}`, name: fichier.name,
                chargement: true, erreur: "", id: null,
            });
            // Le proxy réactif, pas l'objet poussé : sinon l'écran ne voit pas la fin.
            const piece = this.state.pieces[this.state.pieces.length - 1];
            await this._televerser(piece, fichier);
        }
    },

    async _televerser(piece, fichier) {
        const donnees = new FormData();
        donnees.append("ufile", fichier);
        donnees.append("csrf_token", odoo.csrf_token);
        let rendu;
        try {
            const reponse = await fetch("/claude-chat/upload", { method: "POST", body: donnees });
            rendu = await reponse.json();
        } catch {
            rendu = { error: _t("The file could not be sent.") };
        }
        piece.chargement = false;
        if (piece.retiree) {
            // Retirée pendant la lecture : la pièce arrivée au serveur part aussi.
            if (rendu.id) {
                try {
                    await rpc("/claude-chat/upload/remove", { attachment_id: rendu.id });
                } catch { /* ramassée par le ménage */ }
            }
            return;
        }
        if (rendu.error) {
            piece.erreur = rendu.error;
            return;
        }
        Object.assign(piece, {
            id: rendu.id, size: rendu.size, chars: rendu.chars,
            total: rendu.total, truncated: rendu.truncated,
        });
    },

    pieceTitre(piece) {
        if (piece.erreur) return piece.erreur;
        if (piece.chargement) return _t("Reading the file…");
        const lu = _t("%s characters read", (piece.chars || 0).toLocaleString());
        if (!piece.truncated) return `${taille(piece.size)} · ${lu}`;
        const coupe = _t("cut: Gen will only read the beginning (%s in all)",
            (piece.total || 0).toLocaleString());
        return `${lu} — ${coupe}`;
    },

    async onRemovePiece(piece) {
        piece.retiree = true;
        this.state.pieces = this.state.pieces.filter((p) => p.cle !== piece.cle);
        if (piece.id) {
            try {
                await rpc("/claude-chat/upload/remove", { attachment_id: piece.id });
            } catch { /* elle sera ramassée au prochain téléversement */ }
        }
    },

    /** Les pièces qui partent avec ce message, retirées de la saisie. Les puces en
     *  erreur restent : la personne voit pourquoi, et les retire elle-même. */
    _prendrePieces() {
        const pretes = this.piecesPretes();
        this.state.pieces = this.state.pieces.filter((p) => !pretes.includes(p));
        return pretes.map((p) => ({
            id: p.id, name: p.name, size: p.size, chars: p.chars,
            total: p.total, truncated: p.truncated,
        }));
    },

    /** Un envoi refusé ou différé : les pièces reviennent à la saisie. Celles que le
     *  serveur a nommées dans son refus reviennent EN ERREUR, avec sa raison : sans
     *  cela, la même puce morte repartait et se faisait refuser à chaque envoi. */
    _rendrePieces(pieces, { enCause = [], raison = "" } = {}) {
        if (!pieces || !pieces.length) return;
        const deja = new Set(this.state.pieces.map((p) => p.id));
        const enErreur = new Set(enCause || []);
        for (const p of pieces) {
            if (!deja.has(p.id)) {
                this.state.pieces.push({
                    ...p, cle: `r-${p.id}`, chargement: false,
                    erreur: enErreur.has(p.id) ? (raison || _t("Attachment not found.")) : "",
                });
            }
        }
    },

    pieceUrl(piece) {
        return `/web/content/${piece.id}?download=true`;
    },
};
