/** @odoo-module **/
/*
 * Two-pane IMAP browser (Apple Mail / Thunderbird layout).
 *
 * Left:   folder tree (collapsible parents, unread counts).
 * Right:  message list (top, infinite scroll, per-row Traité) + body
 *         preview (bottom, iframe-rendered).
 *
 * Keyboard:  j/k/↑/↓ navigate, r reply, shift+r reply-all, f forward,
 *            e Traité, delete/backspace Supprimer, y router, h snooze,
 *            t créer activité, / search, esc clear search/selection.
 *
 * All IMAP I/O happens on the server side via the imap_browser_* methods
 * on bf.email. This component is just orchestration + rendering.
 */

import { registry } from "@web/core/registry";
import {
    Component,
    useState,
    onWillStart,
    onMounted,
    onWillUnmount,
    useRef,
} from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { _t } from "@web/core/l10n/translation";
// Préférences, format de date, rendu de l'expéditeur et gabarit d'aperçu sont
// partagés avec la boîte de réception Odoo (`bf_email_inbox`) : les deux
// interfaces doivent se ressembler, donc elles lisent le même code et la même
// clé de stockage local.
import {
    loadSettings,
    persistSettings,
    formatRelativeDate,
    senderCell,
    buildPreviewSrcdoc,
    paneStyles,
    startPaneDrag,
    columnWidths,
    selectRange,
} from "./bf_email_ui_common";

export class BfEmailBrowser extends Component {
    static template = "bf_email_management.Browser";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        // Point de départ d'un shift+clic. Voir bf_email_inbox.js — même
        // comportement des deux côtés, c'est la même liste pour l'usager.
        this._selectionAnchor = null;

        this.searchInputRef = useRef("searchInput");
        this.listBottomRef = useRef("listBottom");

        const initialSettings = loadSettings();
        this.state = useState({
            folders: [],
            currentFolder: "INBOX",
            messages: [],
            total: 0,
            offset: 0,
            pageSize: initialSettings.pageSize,
            selectedUid: null,
            // Plain object for OWL reactivity: keys are UIDs, values are `true`.
            // Cleared when folder changes or after a successful bulk action.
            selectedUids: {},
            preview: null,
            loadingFolders: true,
            loadingMessages: false,
            loadingMoreMessages: false,
            loadingPreview: false,
            acting: false,
            searchQuery: "",
            expandedFolders: {},
            dragUid: null,
            settings: initialSettings,
            settingsOpen: false,
            columnsOpen: false,
            dragging: false,
        });

        onWillStart(async () => {
            await this.loadFolders();
            await this.loadMessages("INBOX", 0);
        });

        onMounted(() => {
            this._observer = new IntersectionObserver((entries) => {
                if (entries.some(e => e.isIntersecting)
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

        onWillUnmount(() => {
            if (this._observer) this._observer.disconnect();
            document.removeEventListener("keydown", this._onSlashKey);
        });

        // --- Keyboard shortcuts ---
        // Allow these to fire even when focus is on a non-input element;
        // useHotkey defaults to ignoring inputs which is exactly what we
        // want for j/k/r/e (the search input keeps typing normally).
        useHotkey("arrowdown", () => this.selectNext(), { bypassEditableProtection: false });
        useHotkey("arrowup", () => this.selectPrev(), { bypassEditableProtection: false });
        useHotkey("j", () => this.selectNext());
        useHotkey("k", () => this.selectPrev());
        useHotkey("r", () => this.reply());
        useHotkey("shift+r", () => this.replyAll());
        useHotkey("f", () => this.forward());
        useHotkey("e", () => this.markHandled());
        useHotkey("y", () => this.ingestAndReroute());
        useHotkey("h", () => this.snooze());
        useHotkey("t", () => this.createActivity());
        useHotkey("delete", () => this.moveToTrash());
        useHotkey("backspace", () => this.moveToTrash());
        // Odoo's hotkey whitelist excludes "/" (only alphanums + nav keys
        // + escape are allowed). We use "s" for search-focus and bind "/"
        // natively for muscle-memory compatibility with Gmail/Thunderbird.
        useHotkey("s", () => this.focusSearch());
        // Escape clears multi-selection first; if none, clears search.
        useHotkey("escape", () => this.onEscape(), { bypassEditableProtection: true });

        // Native "/" handler — fires on document keydown when not in editable.
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
    // Folder list + tree
    // ------------------------------------------------------------------
    async loadFolders() {
        this.state.loadingFolders = true;
        try {
            this.state.folders = await this.orm.call(
                "bf.email", "imap_browser_get_folders", []
            );
            // Auto-expand all parents on first load.
            const expanded = {};
            for (const f of this.state.folders) {
                if (f.has_children) expanded[f.name] = true;
            }
            this.state.expandedFolders = expanded;
        } catch (err) {
            this.notification.add(
                _t("Impossible de lister les dossiers IMAP : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.loadingFolders = false;
        }
    }

    /**
     * Build a nested tree from the flat folder list. Folders with `/` in
     * their name are attached as descendants of their parent path. Top-
     * level nodes are at depth 0; each child carries `depth` for indent.
     */
    get folderTree() {
        const byName = {};
        const tree = [];
        // Pass 1: register every folder.
        for (const f of this.state.folders) {
            byName[f.name] = { ...f, children: [] };
        }
        // Pass 2: attach to parent if name contains "/".
        for (const f of this.state.folders) {
            if (f.name.includes("/")) {
                const parentName = f.name.slice(0, f.name.lastIndexOf("/"));
                const parent = byName[parentName];
                if (parent) {
                    parent.children.push(byName[f.name]);
                    continue;
                }
            }
            tree.push(byName[f.name]);
        }
        // Pass 3: flatten with depth, hiding collapsed children.
        const flat = [];
        const walk = (nodes, depth) => {
            for (const n of nodes) {
                flat.push({ ...n, depth });
                const expanded = this.state.expandedFolders[n.name];
                if (expanded && n.children && n.children.length) {
                    walk(n.children, depth + 1);
                }
            }
        };
        walk(tree, 0);
        return flat;
    }

    toggleFolder(name) {
        this.state.expandedFolders[name] = !this.state.expandedFolders[name];
    }

    onFolderClick(folderName) {
        this.state.searchQuery = "";
        this.loadMessages(folderName, 0);
    }

    // ------------------------------------------------------------------
    // Message list
    // ------------------------------------------------------------------
    async loadMessages(folder, offset = 0) {
        this.state.loadingMessages = true;
        this.state.currentFolder = folder;
        this.state.offset = offset;
        this.state.selectedUid = null;
        this.state.preview = null;
        this.state.selectedUids = {};
        this._selectionAnchor = null;
        try {
            const result = await this.orm.call(
                "bf.email", "imap_browser_get_messages", [],
                { folder, offset, limit: this.state.pageSize }
            );
            this.state.messages = result.messages || [];
            this.state.total = result.total || 0;
        } catch (err) {
            this.state.messages = [];
            this.state.total = 0;
            this.notification.add(
                _t("Impossible de charger ") + folder + " : " + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.loadingMessages = false;
        }
    }

    async loadMoreMessages() {
        if (this.state.loadingMoreMessages || !this.canLoadMore) return;
        this.state.loadingMoreMessages = true;
        const nextOffset = this.state.offset + this.state.messages.length;
        try {
            const result = await this.orm.call(
                "bf.email", "imap_browser_get_messages", [],
                {
                    folder: this.state.currentFolder,
                    offset: nextOffset,
                    limit: this.state.pageSize,
                }
            );
            this.state.messages.push(...(result.messages || []));
            // total stays the same (computed at first load).
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

    /** Subject/sender/from filter on the loaded page. */
    get visibleMessages() {
        const q = (this.state.searchQuery || "").trim().toLowerCase();
        if (!q) return this.state.messages;
        return this.state.messages.filter(m => {
            const hay = (
                (m.subject || "") + " " +
                (m.sender_name || "") + " " +
                (m.from || "")
            ).toLowerCase();
            return hay.includes(q);
        });
    }

    selectMessage(uid) {
        if (!uid) return;
        if (this.state.selectedUid === uid) return;
        this.state.selectedUid = uid;
        this._fetchPreview(uid);
    }

    async _fetchPreview(uid) {
        this.state.loadingPreview = true;
        this.state.preview = null;
        try {
            this.state.preview = await this.orm.call(
                "bf.email", "imap_browser_get_body", [],
                { folder: this.state.currentFolder, uid }
            );
            // Mark as seen locally so the row un-bolds immediately.
            const m = this.state.messages.find(x => x.uid === uid);
            if (m) m.seen = true;
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
        if (!this.state.selectedUid) {
            this.selectMessage(list[0].uid);
            return;
        }
        const i = list.findIndex(m => m.uid === this.state.selectedUid);
        if (i >= 0 && i < list.length - 1) {
            this.selectMessage(list[i + 1].uid);
        }
    }

    selectPrev() {
        const list = this.visibleMessages;
        if (!list.length) return;
        if (!this.state.selectedUid) {
            this.selectMessage(list[0].uid);
            return;
        }
        const i = list.findIndex(m => m.uid === this.state.selectedUid);
        if (i > 0) {
            this.selectMessage(list[i - 1].uid);
        }
    }

    // ------------------------------------------------------------------
    // Search
    // ------------------------------------------------------------------
    focusSearch() {
        if (this.searchInputRef.el) {
            this.searchInputRef.el.focus();
            this.searchInputRef.el.select();
        }
    }

    clearSearch() {
        this.state.searchQuery = "";
        if (this.searchInputRef.el) this.searchInputRef.el.blur();
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
    }

    // ------------------------------------------------------------------
    // Drag & drop between folders
    // ------------------------------------------------------------------
    onRowDragStart(ev, uid) {
        ev.dataTransfer.setData("application/x-bf-uid", uid);
        ev.dataTransfer.effectAllowed = "move";
        this.state.dragUid = uid;
    }

    onRowDragEnd() {
        this.state.dragUid = null;
    }

    onFolderDragOver(ev, folderName) {
        if (folderName === this.state.currentFolder) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
    }

    async onFolderDrop(ev, dstFolder) {
        ev.preventDefault();
        const uid = ev.dataTransfer.getData("application/x-bf-uid") || this.state.dragUid;
        if (!uid || dstFolder === this.state.currentFolder) {
            this.state.dragUid = null;
            return;
        }
        try {
            await this.orm.call(
                "bf.email", "imap_browser_move", [],
                {
                    folder: this.state.currentFolder,
                    uid,
                    dst_folder: dstFolder,
                }
            );
            this.notification.add(
                _t("Déplacé vers ") + dstFolder,
                { type: "success" }
            );
            // Remove from current view + auto-jump.
            this._removeAndJump(uid);
        } catch (err) {
            this.notification.add(
                _t("Déplacement échoué : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.dragUid = null;
        }
    }

    /**
     * After a destructive action on ``uid`` (Traité, Supprimer, déplacé),
     * remove the row from the loaded list and select the row that took its
     * place (or the previous one if it was the last).
     */
    _removeAndJump(uid) {
        // Also drop it from the multi-selection so the bulk bar count stays
        // accurate after destructive actions.
        if (this.state.selectedUids[uid]) {
            delete this.state.selectedUids[uid];
        }
        const i = this.state.messages.findIndex(m => m.uid === uid);
        if (i < 0) {
            this.state.preview = null;
            this.state.selectedUid = null;
            return;
        }
        this.state.messages.splice(i, 1);
        this.state.total = Math.max(0, this.state.total - 1);
        const next = this.state.messages[i] || this.state.messages[i - 1];
        if (next) {
            this.selectMessage(next.uid);
        } else {
            this.state.preview = null;
            this.state.selectedUid = null;
        }
    }

    // ------------------------------------------------------------------
    // Mail-client actions on the selected message
    // ------------------------------------------------------------------
    async _runAction(rpcMethod, opts = {}) {
        const uid = opts.uid || this.state.selectedUid;
        if (!uid || this.state.acting) return null;
        this.state.acting = true;
        try {
            const result = await this.orm.call(
                "bf.email", rpcMethod, [],
                { folder: this.state.currentFolder, uid }
            );
            if (opts.markIngested) {
                const m = this.state.messages.find(x => x.uid === uid);
                if (m) m.already_in_bf_email = true;
                if (this.state.preview && this.state.selectedUid === uid) {
                    this.state.preview.already_in_bf_email = true;
                }
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

    async ingestAndReroute() {
        // Legacy alias kept for the `y` hotkey: route to whatever the user
        // picks in the wizard, no model hint. Delegates to quickReroute.
        return this.quickReroute(null);
    }

    // ------------------------------------------------------------------
    // Multi-selection (checkboxes in the message list)
    // ------------------------------------------------------------------
    /**
     * Une case, ou toute une plage au shift+clic. ``preventDefault`` laisse
     * l'état piloter seul la case cochée — sans lui, une case déjà cochée
     * reprise dans une plage se décoche à l'écran sans quitter la sélection.
     */
    toggleSelection(uid, ev) {
        if (ev) {
            ev.stopPropagation();
            ev.preventDefault();
        }
        const uids = this.visibleMessages.map((m) => m.uid);
        if (ev && ev.shiftKey && this._selectionAnchor !== null
                && this._selectionAnchor !== uid
                && selectRange(this.state.selectedUids, uids,
                               this._selectionAnchor, uid)) {
            this._selectionAnchor = uid;
            return;
        }
        if (this.state.selectedUids[uid]) {
            delete this.state.selectedUids[uid];
        } else {
            this.state.selectedUids[uid] = true;
        }
        this._selectionAnchor = uid;
    }

    clearSelection() {
        this.state.selectedUids = {};
        this._selectionAnchor = null;
    }

    get selectedCount() {
        return Object.keys(this.state.selectedUids).length;
    }

    get selectedUidsList() {
        return Object.keys(this.state.selectedUids);
    }

    onEscape() {
        if (this.state.columnsOpen) {
            this.state.columnsOpen = false;
        } else if (this.selectedCount > 0) {
            this.clearSelection();
        } else {
            this.clearSearch();
        }
    }

    /**
     * Bulk or single reroute, with optional model hint that pre-fills the
     * wizard's ``target_reference``. Falls back to the preview-selected UID
     * when nothing is checked.
     */
    async quickReroute(targetModel) {
        if (this.state.acting) return;
        const uids = this.selectedCount > 0
            ? this.selectedUidsList
            : (this.state.selectedUid ? [this.state.selectedUid] : []);
        if (!uids.length) {
            this.notification.add(
                _t("Sélectionne au moins un courriel à router."),
                { type: "warning" }
            );
            return;
        }
        this.state.acting = true;
        try {
            const action = await this.orm.call(
                "bf.email", "imap_browser_quick_reroute", [],
                {
                    folder: this.state.currentFolder,
                    uids,
                    target_model: targetModel || null,
                }
            );
            // Mark each ingested UID locally so badges update immediately.
            for (const uid of uids) {
                const m = this.state.messages.find(x => x.uid === uid);
                if (m) m.already_in_bf_email = true;
            }
            if (this.state.preview && uids.includes(this.state.selectedUid)) {
                this.state.preview.already_in_bf_email = true;
            }
            this.clearSelection();
            if (action) this.action.doAction(action);
        } catch (err) {
            this.notification.add(
                _t("Routage échoué : ") + (err.message || err),
                { type: "danger" }
            );
        } finally {
            this.state.acting = false;
        }
    }

    async bulkMarkHandled() {
        if (!this.selectedCount) return;
        const uids = this.selectedUidsList.slice();
        this.clearSelection();
        for (const uid of uids) {
            await this.markHandledForRow(uid);
        }
    }

    async snooze() {
        const action = await this._runAction("imap_browser_snooze", {
            markIngested: true,
            errorPrefix: _t("Snooze impossible : "),
        });
        if (action) this.action.doAction(action);
    }

    async createActivity() {
        const action = await this._runAction("imap_browser_create_activity", {
            markIngested: true,
            errorPrefix: _t("Création d'activité impossible : "),
        });
        if (action) this.action.doAction(action);
    }

    async reply() {
        const action = await this._runAction("imap_browser_reply", {
            markIngested: true,
            errorPrefix: _t("Réponse impossible : "),
        });
        if (action) this.action.doAction(action);
    }

    async replyAll() {
        const action = await this._runAction("imap_browser_reply_all", {
            markIngested: true,
            errorPrefix: _t("Réponse à tous impossible : "),
        });
        if (action) this.action.doAction(action);
    }

    async forward() {
        const action = await this._runAction("imap_browser_forward", {
            markIngested: true,
            errorPrefix: _t("Transfert impossible : "),
        });
        if (action) this.action.doAction(action);
    }

    async markHandled() {
        const uid = this.state.selectedUid;
        const result = await this._runAction("imap_browser_mark_handled", {
            markIngested: true,
            errorPrefix: _t("Échec « Traité » : "),
        });
        if (result && uid) {
            this.notification.add(
                _t("Marqué comme traité et déplacé vers Archives."),
                { type: "success" }
            );
            this._removeAndJump(uid);
        }
    }

    /** Per-row Traité (no need to first select the row). */
    async markHandledForRow(uid) {
        const prev = this.state.selectedUid;
        this.state.selectedUid = uid;
        const result = await this._runAction("imap_browser_mark_handled", {
            uid,
            markIngested: true,
            errorPrefix: _t("Échec « Traité » : "),
        });
        if (result) {
            this._removeAndJump(uid);
        } else if (prev) {
            this.state.selectedUid = prev;
        }
    }

    async moveToTrash() {
        const uid = this.state.selectedUid;
        const result = await this._runAction("imap_browser_move_to_trash", {
            errorPrefix: _t("Suppression impossible : "),
        });
        if (result && uid) {
            this.notification.add(_t("Déplacé vers Trash."), { type: "success" });
            this._removeAndJump(uid);
        }
    }

    // ------------------------------------------------------------------
    // Display helpers
    // ------------------------------------------------------------------
    /**
     * Date relative à la Apple Mail (ou absolue selon les préférences).
     * Implémentation partagée avec la boîte de réception Odoo.
     */
    formatRelativeDate(iso) {
        return formatRelativeDate(iso, this.state.settings);
    }

    /**
     * Contenu de la colonne « Expéditeur », selon la préférence senderDisplay.
     * L'adresse complète reste dans l'infobulle de la ligne (`m.from`).
     */
    senderCell(m) {
        return senderCell(m.sender_name, m.from, this.state.settings);
    }

    // ------------------------------------------------------------------
    // Settings
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

    updateSetting(key, value) {
        const next = { ...this.state.settings, [key]: value };
        this.state.settings = next;
        persistSettings(next);
        if (key === "pageSize") {
            const intVal = parseInt(value, 10);
            this.state.pageSize = intVal;
            // Reload current folder with the new page size.
            this.loadMessages(this.state.currentFolder, 0);
        }
    }

    onSettingChange(key, ev) {
        const value = ev.target.type === "checkbox"
            ? ev.target.checked
            : ev.target.value;
        this.updateSetting(key, value);
    }

    /**
     * Enveloppe le corps du courriel pour l'iframe d'aperçu. Gabarit partagé
     * avec la boîte de réception Odoo.
     */
    // ------------------------------------------------------------------
    // Colonnes de la liste
    // ------------------------------------------------------------------
    /** « Sujet » n'est pas offert : c'est la colonne qu'on ne peut pas retirer. */
    get columnDefs() {
        return [
            { key: "date", label: _t("Date") },
            { key: "sender", label: _t("Expéditeur") },
            { key: "state", label: _t("État") },
        ];
    }

    get cols() {
        return this.state.settings.columnsBrowser;
    }

    get visibleColumnCount() {
        return this.columnDefs.filter((c) => this.cols[c.key]).length + 3;
    }

    toggleColumnsMenu() {
        this.state.columnsOpen = !this.state.columnsOpen;
        if (this.state.columnsOpen) this.state.settingsOpen = false;
    }

    toggleColumn(key) {
        const next = {
            ...this.state.settings,
            columnsBrowser: { ...this.cols, [key]: !this.cols[key] },
        };
        this.state.settings = next;
        persistSettings(next);
    }

    // ------------------------------------------------------------------
    // Disposition liste / aperçu — partagée avec la boîte de réception
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


    get previewSrcdoc() {
        return buildPreviewSrcdoc(this.state.preview && this.state.preview.body_html);
    }
}

registry.category("actions").add("bf_email_browser", BfEmailBrowser);
