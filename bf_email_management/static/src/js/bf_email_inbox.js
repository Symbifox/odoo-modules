/** @odoo-module **/
/*
 * Boîte de réception Odoo, en action cliente OWL.
 *
 * Même mise en page que le navigateur IMAP — dossiers à gauche, liste en haut,
 * aperçu en bas — mais la source est ``bf.email`` au lieu du serveur IMAP :
 * les « dossiers » sont des états (Boîte, Non lus, À répondre, Sans dossier,
 * Reportés, Envoyés, Traités, par catégorie) et non des boîtes aux lettres.
 *
 * Clavier : j/k/↑/↓ naviguer · r répondre · shift+r répondre à tous ·
 *           f transférer · e Traité · y router · h reporter · t activité ·
 *           o ouvrir le dossier · s ou / rechercher · échap annuler.
 *
 * Tout l'accès aux données passe par les méthodes inbox_* de bf.email, qui
 * restent dans l'environnement de l'usager : aucune ligne d'un collègue ne
 * peut apparaître ici.
 */

import { registry } from "@web/core/registry";
import {
    Component,
    useState,
    onWillStart,
    onMounted,
    onPatched,
    onWillUnmount,
    useRef,
} from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { _t } from "@web/core/l10n/translation";
import {
    loadSettings,
    persistSettings,
    formatRelativeDate,
    senderCell,
    buildPreviewSrcdoc,
    flattenTree,
    paneStyles,
    startPaneDrag,
    columnWidths,
    selectRange,
} from "./bf_email_ui_common";

// Dossiers acceptant un dépôt de ligne, et l'action que ça déclenche.
const DROP_ACTIONS = {
    handled: "handle",
    inbox: "unhandle",
    snoozed: "snooze",
};

// Dossier des envois programmés. Sa source n'est pas ``bf.email`` : la liste
// et l'aperçu changent d'appel serveur quand il est ouvert.
const DRAFTS_FOLDER = "drafts";

export class BfEmailInbox extends Component {
    static template = "bf_email_management.Inbox";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        // Dernière case cochée : point de départ d'un shift+clic. Hors du
        // `useState` volontairement — personne ne l'affiche, et la rendre
        // réactive redessinerait la liste à chaque clic pour rien.
        this._selectionAnchor = null;

        this.searchInputRef = useRef("searchInput");
        this.listBottomRef = useRef("listBottom");
        this.selectAllRef = useRef("selectAll");

        const initialSettings = loadSettings();
        this.state = useState({
            folders: [],
            currentFolder: "inbox",
            messages: [],
            total: 0,
            offset: 0,
            pageSize: initialSettings.pageSize,
            selectedId: null,
            // Objet simple pour la réactivité OWL : clés = ids, valeurs = true.
            selectedIds: {},
            preview: null,
            loadingFolders: true,
            loadingMessages: false,
            loadingMoreMessages: false,
            loadingPreview: false,
            acting: false,
            syncing: false,
            searchQuery: "",
            expandedFolders: { categories: false },
            dragId: null,
            dropTarget: null,
            settings: initialSettings,
            settingsOpen: false,
            columnsOpen: false,
            // Glisser du séparateur liste / aperçu.
            dragging: false,
        });

        onWillStart(async () => {
            await this.loadFolders();
            await this.loadMessages("inbox", 0);
        });

        this.busService = useService("bus_service");
        this._refreshTimer = null;

        onMounted(() => {
            // Référence gardée pour pouvoir se désabonner : sinon chaque
            // ouverture de la boîte laisse un abonné derrière elle.
            this._onBusTick = () => this._refreshSoon();
            this.busService.subscribe("bf_email/changed", this._onBusTick);
            this.busService.start();
            this._observer = new IntersectionObserver((entries) => {
                if (entries.some((e) => e.isIntersecting)
                        && this.canLoadMore
                        && !this.state.loadingMoreMessages
                        && !this.state.loadingMessages) {
                    this.loadMoreMessages();
                }
            }, { rootMargin: "200px" });
            if (this.listBottomRef.el) {
                this._observer.observe(this.listBottomRef.el);
            }
            document.addEventListener("keydown", this._onSlashKey);
        });

        // `indeterminate` ne s'exprime pas en attribut : sans ce crochet, une
        // sélection partielle s'afficherait comme « rien de coché », ce qui
        // est le contraire de ce qu'elle est.
        onPatched(() => {
            if (this.selectAllRef.el) {
                this.selectAllRef.el.indeterminate = this.someLoadedSelected;
            }
        });

        onWillUnmount(() => {
            // Sans ça, un tick reçu juste avant le démontage rappellerait
            // refreshInPlace sur un composant détruit.
            if (this._onBusTick) {
                this.busService.unsubscribe("bf_email/changed", this._onBusTick);
            }
            if (this._refreshTimer) clearTimeout(this._refreshTimer);
            if (this._observer) this._observer.disconnect();
            if (this._searchTimer) clearTimeout(this._searchTimer);
            document.removeEventListener("keydown", this._onSlashKey);
        });

        // --- Raccourcis, même vocabulaire que le navigateur IMAP ---
        useHotkey("arrowdown", () => this.selectNext(), { bypassEditableProtection: false });
        useHotkey("arrowup", () => this.selectPrev(), { bypassEditableProtection: false });
        useHotkey("j", () => this.selectNext());
        useHotkey("k", () => this.selectPrev());
        useHotkey("r", () => this.runAction("reply"));
        useHotkey("shift+r", () => this.runAction("reply_all"));
        useHotkey("f", () => this.runAction("forward"));
        useHotkey("e", () => this.markHandled());
        useHotkey("y", () => this.runAction("reroute"));
        useHotkey("h", () => this.runAction("snooze"));
        useHotkey("t", () => this.runAction("activity"));
        useHotkey("o", () => this.openSourceRecord());
        useHotkey("c", () => this.compose());
        useHotkey("s", () => this.focusSearch());
        useHotkey("escape", () => this.onEscape(), { bypassEditableProtection: true });

        // Odoo n'autorise pas "/" dans sa liste blanche de raccourcis ; on le
        // câble nativement pour la mémoire musculaire Gmail/Thunderbird.
        this._onSlashKey = (ev) => {
            if (ev.key !== "/") return;
            const t = ev.target;
            const editable = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
            if (editable) return;
            ev.preventDefault();
            this.focusSearch();
        };
    }

    // ------------------------------------------------------------------
    // Composer / synchroniser
    // ------------------------------------------------------------------
    /**
     * Nouveau courriel, rattaché à aucun dossier. Le serveur crée une ligne
     * qui sert de fil à elle-même ; à la fermeture du composeur elle est soit
     * adoptée (un message a été posté), soit effacée (composeur annulé).
     */
    async compose() {
        if (this.state.acting) return;
        this.state.acting = true;
        try {
            const action = await this.orm.call("bf.email", "inbox_compose", []);
            const shellId = action && action.context
                ? action.context.bf_email_compose_shell_id
                : null;
            await this.action.doAction(action, {
                onClose: async () => {
                    if (shellId) {
                        try {
                            await this.orm.call(
                                "bf.email", "inbox_close_compose", [],
                                { shell_id: shellId }
                            );
                        } catch (err) {
                            // Le ménage de la coquille ne doit jamais masquer
                            // le fait que le courriel, lui, est parti.
                            console.warn("bf_email_inbox: adoption échouée", err);
                        }
                    }
                    await this.refreshCurrent();
                },
            });
        } catch (err) {
            this.notification.add(
                _t("Composition impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.acting = false;
        }
    }

    /**
     * Même travail que « Synchroniser maintenant » de la vue liste : on tire
     * les chatters et l'IMAP, puis on recharge dossiers et liste sur place.
     * L'action serveur, elle, recharge tout le client web — ce qui jetterait
     * l'aperçu ouvert.
     */
    async syncNow() {
        if (this.state.syncing) return;
        this.state.syncing = true;
        try {
            const res = await this.orm.call("bf.email", "inbox_sync_now", []);
            await this.refreshCurrent();
            this.notification.add(res.message || "", {
                title: res.title,
                type: res.type === "success" ? "success" : "info",
            });
        } catch (err) {
            this.notification.add(
                _t("Synchronisation impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.syncing = false;
        }
    }

    // ------------------------------------------------------------------
    // Dossiers
    // ------------------------------------------------------------------
    async loadFolders() {
        this.state.loadingFolders = true;
        try {
            this.state.folders = await this.orm.call(
                "bf.email", "inbox_get_folders", []
            );
        } catch (err) {
            this.notification.add(
                _t("Impossible de charger les dossiers : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.loadingFolders = false;
        }
    }

    get folderTree() {
        return flattenTree(this.state.folders, this.state.expandedFolders);
    }

    get currentFolderLabel() {
        const f = this.state.folders.find((x) => x.key === this.state.currentFolder);
        return f ? f.label : this.state.currentFolder;
    }

    toggleFolder(key) {
        this.state.expandedFolders[key] = !this.state.expandedFolders[key];
    }

    onFolderClick(folder) {
        if (!folder.selectable) {
            this.toggleFolder(folder.key);
            return;
        }
        this.state.searchQuery = "";
        this.loadMessages(folder.key, 0);
    }

    // ------------------------------------------------------------------
    // Liste
    // ------------------------------------------------------------------
    /** Le dossier ouvert est-il celui des envois programmés ? */
    get isDraftFolder() {
        return this.state.currentFolder === DRAFTS_FOLDER;
    }

    async loadMessages(folder, offset = 0) {
        this.state.loadingMessages = true;
        this.state.currentFolder = folder;
        this.state.offset = offset;
        this.state.selectedId = null;
        this.state.preview = null;
        this.state.selectedIds = {};
        this._selectionAnchor = null;
        try {
            const result = await this._fetchPage(folder, offset);
            this.state.messages = result.messages || [];
            this.state.total = result.total || 0;
        } catch (err) {
            this.state.messages = [];
            this.state.total = 0;
            this.notification.add(
                _t("Chargement impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.loadingMessages = false;
        }
    }

    /**
     * Une page, quelle qu'en soit la source. Les deux dossiers ont le même
     * contrat de sortie ({messages, total}), donc pagination, recherche et
     * défilement infini n'ont pas à savoir lequel est ouvert.
     */
    async _fetchPage(folder, offset, limit = null) {
        const size = limit || this.state.pageSize;
        if (folder === DRAFTS_FOLDER) {
            return this.orm.call("bf.email", "inbox_get_drafts", [], {
                offset,
                limit: size,
                search: this.state.searchQuery || null,
            });
        }
        return this.orm.call("bf.email", "inbox_get_messages", [], {
            folder,
            offset,
            limit: size,
            search: this.state.searchQuery || null,
        });
    }

    /**
     * Rafraîchissement de fond, déclenché par le bus.
     *
     * Volontairement PAS `loadMessages` : celle-ci remet l'offset à zéro et
     * efface la sélection, l'aperçu et les cases cochées. Un courriel qui
     * arrive pendant qu'on lit ne doit pas fermer ce qu'on lit ni faire
     * remonter la liste sous le curseur.
     *
     * Recharge la tranche déjà affichée — défilement infini compris — et ne
     * lâche la sélection que si la ligne a réellement quitté le dossier.
     */
    async refreshInPlace() {
        if (this.state.loadingMessages || this.state.loadingMoreMessages
                || this.state.acting || this.state.dragId
                || this.state.syncing) {
            // Une action est en vol : réessayer plus tard plutôt que de
            // recharger par-dessus.
            this._refreshSoon();
            return;
        }
        const folder = this.state.currentFolder;
        const span = Math.max(this.state.messages.length, this.state.pageSize);
        const keptId = this.state.selectedId;
        try {
            const result = await this._fetchPage(folder, this.state.offset, span);
            if (folder !== this.state.currentFolder) {
                return;  // l'usager a changé de dossier entre-temps
            }
            this.state.messages = result.messages || [];
            this.state.total = result.total || 0;
            if (keptId && !this.state.messages.some((m) => m.id === keptId)) {
                // La ligne a quitté le dossier : garder l'aperçu ouvert
                // pointerait sur quelque chose qui n'est plus là.
                this.state.selectedId = null;
                this.state.preview = null;
            }
            await this.loadFolders();
        } catch (err) {
            // Un rafraîchissement de fond ne dérange personne avec un toast.
            console.warn("bf_email_inbox: refresh failed", err);
        }
    }

    /**
     * Une passe d'ingestion appelle create() par message : une livraison de
     * cinquante courriels produit cinquante ticks. On n'en garde qu'un.
     */
    _refreshSoon() {
        if (this._refreshTimer) {
            clearTimeout(this._refreshTimer);
        }
        this._refreshTimer = setTimeout(() => {
            this._refreshTimer = null;
            this.refreshInPlace();
        }, 500);
    }

    async loadMoreMessages() {
        if (this.state.loadingMoreMessages || !this.canLoadMore) return;
        this.state.loadingMoreMessages = true;
        const nextOffset = this.state.offset + this.state.messages.length;
        try {
            const result = await this._fetchPage(
                this.state.currentFolder, nextOffset
            );
            this.state.messages.push(...(result.messages || []));
        } catch (err) {
            this.notification.add(
                _t("Chargement supplémentaire échoué : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.loadingMoreMessages = false;
        }
    }

    get canLoadMore() {
        return this.state.offset + this.state.messages.length < this.state.total;
    }

    /** La recherche est servie par le serveur : pas de filtrage local. */
    get visibleMessages() {
        return this.state.messages;
    }

    selectMessage(id) {
        if (!id || this.state.selectedId === id) return;
        this.state.selectedId = id;
        this._fetchPreview(id);
    }

    async _fetchPreview(id) {
        this.state.loadingPreview = true;
        this.state.preview = null;
        try {
            if (this.isDraftFolder) {
                this.state.preview = await this.orm.call(
                    "bf.email", "inbox_get_draft_body", [], { draft_id: id }
                );
                return;
            }
            const preview = await this.orm.call(
                "bf.email", "inbox_get_body", [], { email_id: id }
            );
            this.state.preview = preview;
            const row = this.state.messages.find((m) => m.id === id);
            if (row && preview.seen) {
                // Le serveur vient de basculer « nouveau » en « lu » : on
                // enlève le gras tout de suite plutôt qu'au prochain chargement.
                row.seen = true;
                row.status = preview.status;
                this._refreshFolders();
            }
        } catch (err) {
            this.notification.add(
                _t("Aperçu impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.loadingPreview = false;
        }
    }

    selectNext() {
        const list = this.visibleMessages;
        if (!list.length) return;
        if (!this.state.selectedId) {
            this.selectMessage(list[0].id);
            return;
        }
        const i = list.findIndex((m) => m.id === this.state.selectedId);
        if (i >= 0 && i < list.length - 1) {
            this.selectMessage(list[i + 1].id);
        }
    }

    selectPrev() {
        const list = this.visibleMessages;
        if (!list.length) return;
        if (!this.state.selectedId) {
            this.selectMessage(list[0].id);
            return;
        }
        const i = list.findIndex((m) => m.id === this.state.selectedId);
        if (i > 0) {
            this.selectMessage(list[i - 1].id);
        }
    }

    // ------------------------------------------------------------------
    // Recherche
    // ------------------------------------------------------------------
    focusSearch() {
        if (this.searchInputRef.el) {
            this.searchInputRef.el.focus();
            this.searchInputRef.el.select();
        }
    }

    clearSearch() {
        if (!this.state.searchQuery) {
            if (this.searchInputRef.el) this.searchInputRef.el.blur();
            return;
        }
        this.state.searchQuery = "";
        if (this.searchInputRef.el) this.searchInputRef.el.blur();
        this.loadMessages(this.state.currentFolder, 0);
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        // La requête part au serveur : on attend une pause de frappe plutôt
        // que d'en lancer une par caractère.
        if (this._searchTimer) clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => {
            this.loadMessages(this.state.currentFolder, 0);
        }, 300);
    }

    onEscape() {
        if (this.state.columnsOpen) {
            this.state.columnsOpen = false;
        } else if (this.state.settingsOpen) {
            this.closeSettings();
        } else if (this.selectedCount > 0) {
            this.clearSelection();
        } else {
            this.clearSearch();
        }
    }

    // ------------------------------------------------------------------
    // Sélection multiple
    // ------------------------------------------------------------------
    /**
     * Une case, ou toute une plage au shift+clic.
     *
     * ``preventDefault`` n'est pas décoratif : sans lui, le navigateur bascule
     * la case AVANT que le composant ne redessine. Quand l'état calculé se
     * trouve être celui d'avant (cocher une case déjà cochée dans la plage),
     * OWL ne repeint pas ce nœud — l'attribut n'a pas bougé — et la case
     * reste décochée à l'écran tout en comptant dans la sélection. On laisse
     * donc l'état être la seule source de vérité de la case.
     */
    toggleSelection(id, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        const ids = this.visibleMessages.map((m) => m.id);
        if (ev && ev.shiftKey && this._selectionAnchor !== null
                && this._selectionAnchor !== id
                && selectRange(this.state.selectedIds, ids,
                               this._selectionAnchor, id)) {
            // L'ancre suit la dernière extrémité : un second shift+clic
            // prolonge la plage au lieu de repartir du tout début.
            this._selectionAnchor = id;
            return;
        }
        if (this.state.selectedIds[id]) {
            delete this.state.selectedIds[id];
        } else {
            this.state.selectedIds[id] = true;
        }
        this._selectionAnchor = id;
    }

    clearSelection() {
        this.state.selectedIds = {};
        this._selectionAnchor = null;
    }

    /**
     * « Tout sélectionner » porte sur les lignes CHARGÉES, pas sur le dossier :
     * la liste se remplit au défilement, et prétendre sélectionner trois mille
     * courriels dont cent sont en mémoire serait un mensonge qui se paierait au
     * premier clic sur « Traité ». Le compteur de la barre d'actions le dit.
     */
    get allLoadedSelected() {
        const rows = this.visibleMessages;
        return rows.length > 0 && rows.every((m) => this.state.selectedIds[m.id]);
    }

    get someLoadedSelected() {
        return this.selectedCount > 0 && !this.allLoadedSelected;
    }

    toggleSelectAll() {
        if (this.allLoadedSelected) {
            this.clearSelection();
            return;
        }
        const next = {};
        for (const m of this.visibleMessages) {
            next[m.id] = true;
        }
        this.state.selectedIds = next;
        this._selectionAnchor = null;
    }

    get selectAllTitle() {
        if (this.allLoadedSelected) {
            return _t("Tout désélectionner");
        }
        return _t("Sélectionner les %s ligne(s) chargée(s)", this.visibleMessages.length);
    }

    /** Vrai quand le dossier contient plus que ce qui est chargé. */
    get hasMoreThanLoaded() {
        return this.state.total > this.state.messages.length;
    }

    get selectedCount() {
        return Object.keys(this.state.selectedIds).length;
    }

    get selectedIdList() {
        return Object.keys(this.state.selectedIds).map((k) => parseInt(k, 10));
    }

    /** Cibles de l'action : la sélection si elle existe, sinon l'aperçu. */
    get actionTargets() {
        if (this.selectedCount > 0) return this.selectedIdList;
        return this.state.selectedId ? [this.state.selectedId] : [];
    }

    // ------------------------------------------------------------------
    // Glisser-déposer sur un dossier
    // ------------------------------------------------------------------
    onRowDragStart(ev, id) {
        ev.dataTransfer.setData("application/x-bf-email-id", String(id));
        ev.dataTransfer.effectAllowed = "move";
        this.state.dragId = id;
    }

    onRowDragEnd() {
        this.state.dragId = null;
        this.state.dropTarget = null;
    }

    onFolderDragOver(ev, folderKey) {
        if (!(folderKey in DROP_ACTIONS) || folderKey === this.state.currentFolder) {
            return;
        }
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
        this.state.dropTarget = folderKey;
    }

    onFolderDragLeave(folderKey) {
        if (this.state.dropTarget === folderKey) {
            this.state.dropTarget = null;
        }
    }

    async onFolderDrop(ev, folderKey) {
        this.state.dropTarget = null;
        const action = DROP_ACTIONS[folderKey];
        if (!action) return;
        ev.preventDefault();
        const raw = ev.dataTransfer.getData("application/x-bf-email-id");
        const id = parseInt(raw, 10) || this.state.dragId;
        this.state.dragId = null;
        if (!id) return;
        // Un dépôt agit sur la ligne déposée, jamais sur la sélection : c'est
        // ce que le geste désigne.
        await this._dispatch(action, [id], { removeIds: [id] });
    }

    // ------------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------------
    /**
     * Appelle ``inbox_run_action`` et exécute l'action Odoo qui en revient.
     * ``removeIds`` liste les lignes à retirer de la liste chargée une fois
     * l'appel réussi (Traité dans la boîte, Remettre en boîte dans Traités…).
     */
    async _dispatch(action, ids, opts = {}) {
        if (!ids || !ids.length || this.state.acting) return null;
        this.state.acting = true;
        try {
            const result = await this.orm.call(
                "bf.email", "inbox_run_action", [],
                { action, email_ids: ids }
            );
            for (const id of opts.removeIds || []) {
                this._removeAndJump(id);
            }
            if (opts.clearSelection !== false) {
                this.clearSelection();
            }
            if (opts.notify) {
                this.notification.add(opts.notify, { type: "success" });
            }
            this._refreshFolders();
            if (result) {
                this.action.doAction(result, {
                    onClose: () => this.refreshCurrent(),
                });
            }
            return result;
        } catch (err) {
            this.notification.add(
                (opts.errorPrefix || _t("Échec : ")) + (err.message || err),
                { type: "danger" }
            );
            return null;
        } finally {
            this.state.acting = false;
        }
    }

    /** Action sur la cible courante (sélection, sinon aperçu). */
    async runAction(action) {
        // Un envoi programmé n'est pas une ligne bf.email : ses identifiants
        // appartiennent à un autre modèle et les passer à `inbox_run_action`
        // agirait sur le courriel qui porte le même numéro par hasard.
        if (this.isDraftFolder) {
            this.notification.add(
                _t("Cette action ne s'applique pas à un brouillon."),
                { type: "warning" }
            );
            return;
        }
        const ids = this.actionTargets;
        if (!ids.length) {
            this.notification.add(
                _t("Sélectionne d'abord un courriel."), { type: "warning" }
            );
            return;
        }
        // Répondre / transférer / activité ne valent que pour une ligne :
        // on prend l'aperçu quand plusieurs cases sont cochées.
        const single = ["reply", "reply_all", "forward", "activity",
                        "open_record", "open_form", "conversation",
                        "download_eml"];
        const targets = single.includes(action) && ids.length > 1
            ? [this.state.selectedId || ids[0]]
            : ids;
        await this._dispatch(action, targets, { clearSelection: false });
    }

    async markHandled() {
        if (this.isDraftFolder) return;
        const ids = this.actionTargets;
        if (!ids.length) return;
        // Sortir de la boîte ne retire la ligne de la liste que dans les
        // dossiers d'où le traitement la fait disparaître.
        const removes = ["inbox", "unread", "to_reply", "unrouted"]
            .includes(this.state.currentFolder) ? ids : [];
        await this._dispatch("handle", ids, {
            removeIds: removes,
            notify: ids.length > 1
                ? _t("%s courriels traités.", ids.length)
                : _t("Courriel traité."),
            errorPrefix: _t("Échec « Traité » : "),
        });
        if (!removes.length) await this.refreshCurrent();
    }

    async markUnhandled() {
        if (this.isDraftFolder) return;
        const ids = this.actionTargets;
        if (!ids.length) return;
        const removes = ["handled", "snoozed"].includes(this.state.currentFolder)
            ? ids : [];
        await this._dispatch("unhandle", ids, {
            removeIds: removes,
            notify: _t("Remis en boîte de réception."),
            errorPrefix: _t("Échec « Remettre en boîte » : "),
        });
        if (!removes.length) await this.refreshCurrent();
    }

    async markHandledForRow(id, ev) {
        if (ev) ev.stopPropagation();
        if (this.isDraftFolder) return;
        const removes = ["inbox", "unread", "to_reply", "unrouted"]
            .includes(this.state.currentFolder) ? [id] : [];
        await this._dispatch("handle", [id], {
            removeIds: removes,
            clearSelection: false,
            errorPrefix: _t("Échec « Traité » : "),
        });
        if (!removes.length) await this.refreshCurrent();
    }

    async openSourceRecord() {
        const preview = this.state.preview;
        if (!preview || !preview.res_model || !preview.res_id) {
            this.notification.add(
                _t("Ce courriel n'est rattaché à aucun dossier."),
                { type: "warning" }
            );
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: preview.res_model,
            res_id: preview.res_id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async quickReroute(targetModel) {
        if (this.isDraftFolder) return;
        const ids = this.actionTargets;
        if (!ids.length) {
            this.notification.add(
                _t("Sélectionne au moins un courriel à router."),
                { type: "warning" }
            );
            return;
        }
        if (this.state.acting) return;
        this.state.acting = true;
        try {
            const context = { default_bf_email_ids: [[6, 0, ids]] };
            if (targetModel) {
                context.default_target_model_hint = targetModel;
            }
            this.clearSelection();
            await this.action.doAction({
                type: "ir.actions.act_window",
                name: _t("Importer dans un chatter"),
                res_model: "bf.email.reroute",
                view_mode: "form",
                views: [[false, "form"]],
                target: "new",
                context,
            }, { onClose: () => this.refreshCurrent() });
        } catch (err) {
            this.notification.add(
                _t("Routage impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.acting = false;
        }
    }

    /**
     * Retire la ligne de la liste chargée et sélectionne celle qui prend sa
     * place — ou la précédente si c'était la dernière.
     */
    _removeAndJump(id) {
        if (this.state.selectedIds[id]) {
            delete this.state.selectedIds[id];
        }
        const i = this.state.messages.findIndex((m) => m.id === id);
        if (i < 0) return;
        this.state.messages.splice(i, 1);
        this.state.total = Math.max(0, this.state.total - 1);
        if (this.state.selectedId !== id) return;
        const next = this.state.messages[i] || this.state.messages[i - 1];
        this.state.selectedId = null;
        this.state.preview = null;
        if (next) this.selectMessage(next.id);
    }

    async refreshCurrent() {
        const keepId = this.state.selectedId;
        await this.loadMessages(this.state.currentFolder, 0);
        await this.loadFolders();
        if (keepId && this.state.messages.some((m) => m.id === keepId)) {
            this.selectMessage(keepId);
        }
    }

    /** Recompte les dossiers sans bloquer l'interface. */
    _refreshFolders() {
        this.loadFolders();
    }

    // ------------------------------------------------------------------
    // Brouillons (envois programmés)
    // ------------------------------------------------------------------
    /**
     * Envoyer / modifier / ouvrir la fiche / annuler un envoi programmé.
     * ``cancel`` demande confirmation : c'est la seule action de cet écran
     * qui détruit quelque chose qu'on ne peut pas reconstituer.
     */
    async runDraftAction(action) {
        if (!this.isDraftFolder || this.state.acting) return;
        const ids = this.actionTargets;
        if (!ids.length) {
            this.notification.add(
                _t("Sélectionne d'abord un brouillon."), { type: "warning" }
            );
            return;
        }
        const single = ["edit", "open_record"];
        const targets = single.includes(action) && ids.length > 1
            ? [this.state.selectedId || ids[0]]
            : ids;
        if (action === "cancel") {
            const msg = _t("Annuler définitivement %s envoi(s) programmé(s) ?", targets.length);
            if (!window.confirm(msg)) {
                return;
            }
        }
        this.state.acting = true;
        try {
            const result = await this.orm.call(
                "bf.email", "inbox_draft_action", [],
                { action, draft_ids: targets }
            );
            if (result && result.notification) {
                this.notification.add(result.notification.message || "", {
                    title: result.notification.title,
                    type: result.notification.type || "success",
                });
                this.clearSelection();
                await this.refreshCurrent();
                return;
            }
            if (result) {
                this.action.doAction(result, {
                    onClose: () => this.refreshCurrent(),
                });
            }
        } catch (err) {
            this.notification.add(
                _t("Action impossible : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.acting = false;
        }
    }

    // ------------------------------------------------------------------
    // « Ajouter » — créer une fiche à partir du courriel
    // ------------------------------------------------------------------
    /**
     * Même menu que « Nouveau ▾ » de la fiche complète du courriel : la fiche
     * est créée et le courriel importé dans son chatter. Une seule ligne à la
     * fois — créer six tâches d'un coup n'est pas un geste qu'on fait par
     * accident, c'en est un qu'on regrette.
     */
    async createRecord(action) {
        if (this.isDraftFolder) return;
        const id = this.state.selectedId;
        if (!id) {
            this.notification.add(
                _t("Ouvre d'abord un courriel."), { type: "warning" }
            );
            return;
        }
        await this._dispatch(action, [id], { clearSelection: false });
    }

    /** Le menu n'offre que ce qui est installé sur cette base. */
    get canCreate() {
        const p = this.state.preview || {};
        return {
            lead: Boolean(p.has_crm),
            ticket: Boolean(p.has_helpdesk),
            expense: Boolean(p.has_expense),
        };
    }

    // ------------------------------------------------------------------
    // Router / Re-router
    // ------------------------------------------------------------------
    /**
     * « Router… » quand le courriel n'est classé nulle part, « Re-router… »
     * quand il l'est déjà. Le geste diffère : le premier ajoute le courriel à
     * un chatter, le second le DÉPLACE et le retire de là où il était. Un seul
     * libellé pour les deux laissait croire au second qu'il faisait le
     * premier.
     */
    get isRouted() {
        const ids = this.actionTargets;
        if (!ids.length) return false;
        if (this.selectedCount > 0) {
            const rows = this.state.messages.filter(
                (m) => this.state.selectedIds[m.id]
            );
            return rows.length > 0 && rows.every((m) => m.res_model);
        }
        return Boolean(this.state.preview && this.state.preview.res_model);
    }

    get rerouteLabel() {
        return this.isRouted ? _t("Re-router…") : _t("Router…");
    }

    get rerouteTitle() {
        if (!this.isRouted) {
            return _t("Classer ce courriel dans une tâche, un ticket, un contact… (raccourci Y)");
        }
        const current = this.state.preview && this.state.preview.record_name;
        return current
            ? _t("Déplacer ce courriel hors de « %s » vers un autre dossier (raccourci Y)", current)
            : _t("Déplacer ce courriel vers un autre dossier (raccourci Y)");
    }

    // ------------------------------------------------------------------
    // Colonnes de la liste
    // ------------------------------------------------------------------
    /**
     * Colonnes offertes au sélecteur. « Sujet » n'y est pas : c'est la seule
     * qu'on ne peut pas retirer — une liste de courriels sans objet n'est plus
     * une liste de courriels, et permettre de tout décocher fabriquerait un
     * écran vide dont on ne saurait plus sortir.
     *
     * Construit dans un getter et non en constante de module : `_t` traduit à
     * l'appel, et au chargement du fichier les traductions ne sont pas encore
     * en place.
     */
    get columnDefs() {
        return [
            { key: "date", label: _t("Date") },
            { key: "correspondent", label: _t("Correspondant") },
            { key: "folder", label: _t("Dossier") },
            { key: "category", label: _t("Catégorie") },
            { key: "preview", label: _t("Extrait") },
            { key: "state", label: _t("État") },
        ];
    }

    get cols() {
        return this.state.settings.columnsInbox;
    }

    /** Sert au `colspan` de la ligne « rien ici » : deux gouttières + le sujet. */
    get visibleColumnCount() {
        return this.columnDefs.filter((c) => this.cols[c.key]).length + 3;
    }

    /** Idem pour les brouillons, qui n'ont ni catégorie ni extrait. */
    get visibleDraftColumnCount() {
        const shown = ["date", "correspondent", "folder", "state"]
            .filter((k) => this.cols[k]).length;
        return shown + 3;
    }

    toggleColumnsMenu() {
        this.state.columnsOpen = !this.state.columnsOpen;
        if (this.state.columnsOpen) this.state.settingsOpen = false;
    }

    toggleColumn(key) {
        const next = {
            ...this.state.settings,
            columnsInbox: { ...this.cols, [key]: !this.cols[key] },
        };
        this.state.settings = next;
        persistSettings(next);
    }

    /**
     * Libellé lisible d'une catégorie. Les libellés traduits sont déjà calculés
     * par `inbox_get_folders` pour l'arbre de gauche (clés `category:<valeur>`) :
     * les relire ici évite un aller-retour serveur par ligne, et garantit que
     * la colonne et le dossier disent le même mot.
     */
    categoryLabel(value) {
        if (!value) return "";
        const folder = this.state.folders.find((f) => f.key === `category:${value}`);
        return folder ? folder.label : value;
    }

    // ------------------------------------------------------------------
    // Disposition liste / aperçu
    // ------------------------------------------------------------------
    get pane() {
        return paneStyles(this.state.settings);
    }

    get colWidths() {
        return columnWidths(this.state.settings);
    }

    setPaneLayout(layout) {
        if (this.state.settings.paneLayout === layout) return;
        const next = { ...this.state.settings, paneLayout: layout };
        this.state.settings = next;
        persistSettings(next);
    }

    onSplitterMouseDown(ev) {
        startPaneDrag(ev, this);
    }

    // ------------------------------------------------------------------
    // Ruban d'actions de l'aperçu ()
    // ------------------------------------------------------------------
    /**
     * Replié, le ruban devient une seule ligne d'icônes : sur un aperçu placé
     * sous la liste, les douze boutons libellés retombent sur deux ou trois
     * rangées et mangent une bonne part de la hauteur qui devrait servir à
     * lire le courriel. L'en-tête (objet, De, À, date, dossier, pièces
     * jointes) ne se replie pas — c'est le contexte du message, pas une
     * option — et aucune action ne disparaît : les infobulles et les
     * raccourcis clavier restent.
     */
    get ribbonCollapsed() {
        return !!this.state.settings.ribbonCollapsed;
    }

    get ribbonClass() {
        return this.ribbonCollapsed
            ? "mt-2 d-flex gap-1 align-items-center o_bf_email_ribbon o_bf_email_ribbon_compact"
            : "mt-2 d-flex flex-wrap gap-1 o_bf_email_ribbon";
    }

    get ribbonToggleTitle() {
        return this.ribbonCollapsed
            ? _t("Déplier le ruban d'actions")
            : _t("Replier le ruban d'actions en icônes");
    }

    toggleRibbon() {
        const next = {
            ...this.state.settings,
            ribbonCollapsed: !this.ribbonCollapsed,
        };
        this.state.settings = next;
        persistSettings(next);
    }


    // ------------------------------------------------------------------
    // Affichage
    // ------------------------------------------------------------------
    formatDate(iso) {
        return formatRelativeDate(iso, this.state.settings);
    }

    senderCell(m) {
        const raw = m.direction === "out" ? m.to : m.from;
        return senderCell(m.correspondent, raw, this.state.settings);
    }

    get previewSrcdoc() {
        return buildPreviewSrcdoc(this.state.preview && this.state.preview.body_html);
    }

    attachmentUrl(att) {
        return `/web/content/${att.id}?download=true`;
    }

    // ------------------------------------------------------------------
    // Préférences (partagées avec le navigateur IMAP)
    // ------------------------------------------------------------------
    toggleSettings() {
        this.state.settingsOpen = !this.state.settingsOpen;
        // Les deux panneaux occupent le même coin : les laisser ouverts
        // ensemble en superpose un sur l'autre.
        if (this.state.settingsOpen) this.state.columnsOpen = false;
    }

    closeSettings() {
        this.state.settingsOpen = false;
    }

    onSettingChange(key, ev) {
        const value = ev.target.type === "checkbox"
            ? ev.target.checked
            : ev.target.value;
        const next = { ...this.state.settings, [key]: value };
        this.state.settings = next;
        persistSettings(next);
        if (key === "pageSize") {
            this.state.pageSize = parseInt(value, 10);
            this.loadMessages(this.state.currentFolder, 0);
        }
    }
}

registry.category("actions").add("bf_email_inbox", BfEmailInbox);
