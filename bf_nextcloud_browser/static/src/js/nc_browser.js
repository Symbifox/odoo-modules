/** @odoo-module **/

import { Component, useState, useRef, onWillStart } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * Small modal to choose where to link a file: a Knowledge Matrix item or
 * (when available) an Odoo Knowledge article.
 */
export class NcLinkDialog extends Component {
    static template = "bf_nextcloud_browser.NcLinkDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        filename: String,
        items: Array,
        articles: Array,
        hasArticles: Boolean,
        onConfirm: Function,
    };

    setup() {
        this.state = useState({
            target: this.props.items.length || !this.props.hasArticles ? "item" : "article",
            itemId: this.props.items[0] ? String(this.props.items[0].id) : false,
            articleId: this.props.articles[0] ? String(this.props.articles[0].id) : false,
        });
    }

    get canConfirm() {
        return this.state.target === "item" ? !!this.state.itemId : !!this.state.articleId;
    }

    confirm() {
        const id = this.state.target === "item" ? this.state.itemId : this.state.articleId;
        this.props.onConfirm(this.state.target, id);
        this.props.close();
    }
}

/**
 * Generic text-prompt modal (rename / new folder) replacing window.prompt.
 */
export class NcPromptDialog extends Component {
    static template = "bf_nextcloud_browser.NcPromptDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        title: String,
        label: String,
        value: { type: String, optional: true },
        confirmLabel: { type: String, optional: true },
        onConfirm: Function,
    };

    setup() {
        this.state = useState({ value: this.props.value || "" });
    }

    get canConfirm() {
        return !!this.state.value.trim();
    }

    confirm() {
        const v = this.state.value.trim();
        if (!v) {
            return;
        }
        this.props.onConfirm(v);
        this.props.close();
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.confirm();
        }
    }
}

/**
 * Share modal: pick a configured share preset (Interne / Externe / ...).
 */
export class NcShareDialog extends Component {
    static template = "bf_nextcloud_browser.NcShareDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        filename: String,
        presets: Array,
        onChoose: Function,
    };
}

/**
 * Full-screen-ish preview modal (PDF / image / text), served same-origin by the
 * Odoo controller so Nextcloud's X-Frame-Options does not block it.
 */
export class NcPreviewDialog extends Component {
    static template = "bf_nextcloud_browser.NcPreviewDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        name: String,
        url: String,
        internalUrl: { type: String, optional: true },
    };
}

/**
 * Recursive folder-tree node (Dolphin-style left navigation).
 */
export class NcTreeNode extends Component {
    static template = "bf_nextcloud_browser.NcTreeNode";
    static props = { node: Object, browser: Object };
}
NcTreeNode.components = { NcTreeNode };

export class NcBrowser extends Component {
    static template = "bf_nextcloud_browser.NcBrowser";
    static components = { NcTreeNode };
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.fileInput = useRef("fileInput");
        // Persisted sort preference (per browser).
        let sortBy = "name";
        let sortDir = 1;
        try {
            const saved = JSON.parse(browser.localStorage.getItem("bf_nc_browser_sort") || "{}");
            if (saved.by) sortBy = saved.by;
            if (saved.dir) sortDir = saved.dir;
        } catch {
            // ignore malformed pref
        }
        this.state = useState({
            ready: false,
            loading: false,
            error: "",
            relPath: "",
            breadcrumb: [],
            entries: [],
            dragOver: false, // OS-file drop on the panel
            tree: [],
            sortBy,
            sortDir,
            openExtensions: [],
            folderColor: "#2D3031",
            presets: [],
            filter: "",
            selected: [], // rels of checked entries
        });
        onWillStart(() => this.load(""));
    }

    get model() {
        return this.props.record.resModel;
    }
    get resId() {
        return this.props.record.resId;
    }

    // --- scope hooks (overridden by the standalone client-action variant) ---
    get rpcModel() {
        return "bf.nc.browser";
    }
    _scopeArgs() {
        return [this.model, this.resId];
    }
    _method(name) {
        return name;
    }
    get needsRecord() {
        return true;
    }
    get canMutate() {
        return !!this.resId;
    }
    get showLink() {
        return true;
    }
    _fileParams(entry) {
        return { model: this.model, res_id: this.resId, rel_path: entry.rel };
    }
    _urlMode(mode) {
        return mode;
    }

    _err(e) {
        const msg = (e && e.data && e.data.message) || (e && e.message) || _t("Erreur.");
        this.notification.add(msg, { type: "danger" });
    }

    _call(method, args) {
        return this.orm.call(this.rpcModel, this._method(method), [...this._scopeArgs(), ...args]);
    }

    async load(relPath) {
        if (this.needsRecord && !this.resId) {
            this.state.error = _t("Enregistrez d'abord cette fiche pour parcourir ses fichiers.");
            this.state.ready = true;
            return;
        }
        this.state.loading = true;
        try {
            const res = await this.orm.call(this.rpcModel, this._method("browse_dir"), [
                ...this._scopeArgs(),
                relPath || "",
            ]);
            this.state.relPath = res.rel_path;
            this.state.breadcrumb = res.breadcrumb;
            this.state.entries = res.entries;
            this.state.openExtensions = res.open_extensions || this.state.openExtensions;
            this.state.folderColor = res.folder_color || this.state.folderColor;
            this.state.presets = res.presets || this.state.presets;
            this.state.error = "";
            this.state.selected = [];
            this.state.filter = "";
            this._syncTree(res.rel_path, res.entries);
        } catch (e) {
            this.state.entries = [];
            this.state.breadcrumb = [];
            this.state.error =
                (e && e.data && e.data.message) || (e && e.message) || _t("Erreur de chargement.");
        } finally {
            this.state.loading = false;
            this.state.ready = true;
        }
    }

    async _do(method, args) {
        this.state.loading = true;
        try {
            await this._call(method, args);
            await this.load(this.state.relPath);
        } catch (e) {
            this._err(e);
        } finally {
            this.state.loading = false;
        }
    }

    // ----------------------------------------------------------------
    // Folder tree (Dolphin left nav)
    // ----------------------------------------------------------------
    _rootNode() {
        if (!this.state.tree.length) {
            this.state.tree = [
                { rel: "", name: _t("Racine"), expanded: true, loaded: false, loading: false, children: [] },
            ];
        }
        return this.state.tree[0];
    }

    _findOrCreateNode(rel) {
        const root = this._rootNode();
        if (!rel) {
            return root;
        }
        let node = root;
        let acc = "";
        for (const part of rel.split("/")) {
            acc = acc ? acc + "/" + part : part;
            let child = (node.children || []).find((c) => c.rel === acc);
            if (!child) {
                child = { rel: acc, name: part, expanded: false, loaded: false, loading: false, children: [] };
                node.children = [...(node.children || []), child];
            }
            node = child;
        }
        return node;
    }

    _foldersFromEntries(node, entries) {
        return (entries || [])
            .filter((e) => e.is_dir)
            .map((e) => {
                const ex = (node.children || []).find((c) => c.rel === e.rel);
                return ex || { rel: e.rel, name: e.name, expanded: false, loaded: false, loading: false, children: [] };
            });
    }

    _syncTree(rel, entries) {
        const node = this._findOrCreateNode(rel);
        node.children = this._foldersFromEntries(node, entries);
        node.loaded = true;
        node.expanded = true;
        // expand ancestors
        this._rootNode().expanded = true;
        let acc = "";
        for (const part of (rel || "").split("/").filter(Boolean)) {
            acc = acc ? acc + "/" + part : part;
            this._findOrCreateNode(acc).expanded = true;
        }
    }

    async _fetchFolders(node) {
        const res = await this.orm.call(this.rpcModel, this._method("browse_dir"), [
            ...this._scopeArgs(),
            node.rel || "",
        ]);
        return this._foldersFromEntries(node, res.entries);
    }

    async toggleNode(node) {
        node.expanded = !node.expanded;
        if (node.expanded && !node.loaded) {
            node.loading = true;
            try {
                node.children = await this._fetchFolders(node);
                node.loaded = true;
            } catch (e) {
                this._err(e);
            } finally {
                node.loading = false;
            }
        }
    }

    openTreeFolder(node) {
        this.load(node.rel);
    }

    nodeHasCaret(node) {
        return !node.loaded || (node.children && node.children.length > 0);
    }

    // ----------------------------------------------------------------
    // Listing helpers (icons, type, sort)
    // ----------------------------------------------------------------
    _ext(name) {
        return (name.split(".").pop() || "").toLowerCase();
    }

    _iconClass(entry) {
        if (entry.is_dir) {
            return "fa fa-folder"; // colour applied inline from config (state.folderColor)
        }
        const map = {
            pdf: "fa-file-pdf-o",
            doc: "fa-file-word-o", docx: "fa-file-word-o", odt: "fa-file-word-o", rtf: "fa-file-word-o",
            xls: "fa-file-excel-o", xlsx: "fa-file-excel-o", ods: "fa-file-excel-o", csv: "fa-file-excel-o",
            ppt: "fa-file-powerpoint-o", pptx: "fa-file-powerpoint-o", odp: "fa-file-powerpoint-o",
            png: "fa-file-image-o", jpg: "fa-file-image-o", jpeg: "fa-file-image-o", gif: "fa-file-image-o",
            svg: "fa-file-image-o", webp: "fa-file-image-o", bmp: "fa-file-image-o",
            zip: "fa-file-archive-o", rar: "fa-file-archive-o", "7z": "fa-file-archive-o",
            gz: "fa-file-archive-o", tar: "fa-file-archive-o",
            mp3: "fa-file-audio-o", wav: "fa-file-audio-o", flac: "fa-file-audio-o", ogg: "fa-file-audio-o",
            mp4: "fa-file-video-o", mov: "fa-file-video-o", avi: "fa-file-video-o",
            mkv: "fa-file-video-o", webm: "fa-file-video-o",
            txt: "fa-file-text-o", md: "fa-file-text-o",
        };
        return "fa " + (map[this._ext(entry.name)] || "fa-file-o") + " text-muted";
    }

    _typeLabel(entry) {
        if (entry.is_dir) {
            return _t("Dossier");
        }
        return entry.name.includes(".") ? this._ext(entry.name).toUpperCase() : _t("Fichier");
    }

    get sortedEntries() {
        const dir = this.state.sortDir;
        const by = this.state.sortBy;
        const val = (e) => {
            if (by === "size") return e.size || 0;
            if (by === "mtime") return e.mtime || "";
            if (by === "type") return this._typeLabel(e).toLowerCase();
            return (e.name || "").toLowerCase();
        };
        const q = (this.state.filter || "").trim().toLowerCase();
        const list = q
            ? this.state.entries.filter((e) => (e.name || "").toLowerCase().includes(q))
            : this.state.entries;
        return [...list].sort((a, b) => {
            if (a.is_dir !== b.is_dir) {
                return a.is_dir ? -1 : 1; // folders pinned first
            }
            const av = val(a);
            const bv = val(b);
            if (av < bv) return -dir;
            if (av > bv) return dir;
            return 0;
        });
    }

    setSort(col) {
        if (this.state.sortBy === col) {
            this.state.sortDir = -this.state.sortDir;
        } else {
            this.state.sortBy = col;
            this.state.sortDir = 1;
        }
        try {
            browser.localStorage.setItem(
                "bf_nc_browser_sort",
                JSON.stringify({ by: this.state.sortBy, dir: this.state.sortDir })
            );
        } catch {
            // ignore storage failures
        }
    }

    // --- multi-selection + bulk actions ---
    isSelected(rel) {
        return this.state.selected.includes(rel);
    }

    toggleSelect(rel) {
        if (this.isSelected(rel)) {
            this.state.selected = this.state.selected.filter((r) => r !== rel);
        } else {
            this.state.selected = [...this.state.selected, rel];
        }
    }

    get allVisibleSelected() {
        const vis = this.sortedEntries;
        return vis.length > 0 && vis.every((e) => this.isSelected(e.rel));
    }

    toggleSelectAll() {
        if (this.allVisibleSelected) {
            this.state.selected = [];
        } else {
            this.state.selected = this.sortedEntries.map((e) => e.rel);
        }
    }

    clearSelection() {
        this.state.selected = [];
    }

    bulkDelete() {
        const rels = [...this.state.selected];
        if (!rels.length) {
            return;
        }
        this.dialog.add(ConfirmationDialog, {
            title: _t("Supprimer la selection"),
            body: _t("Supprimer definitivement %s element(s) ?", rels.length),
            confirmLabel: _t("Supprimer"),
            confirm: async () => {
                this.state.loading = true;
                try {
                    for (const rel of rels) {
                        await this._call("delete_entry", [rel]);
                    }
                    this.state.selected = [];
                    this.notification.add(_t("Selection supprimee."), { type: "success" });
                    await this.load(this.state.relPath);
                } catch (e) {
                    this._err(e);
                } finally {
                    this.state.loading = false;
                }
            },
            cancel: () => {},
        });
    }

    sortIcon(col) {
        if (this.state.sortBy !== col) {
            return "fa fa-sort text-muted opacity-50";
        }
        return this.state.sortDir === 1 ? "fa fa-sort-asc" : "fa fa-sort-desc";
    }

    // ----------------------------------------------------------------
    // Navigation / open
    // ----------------------------------------------------------------
    onEntryClick(entry) {
        if (entry.is_dir) {
            this.load(entry.rel);
            return;
        }
        if (entry.internal_url && this.state.openExtensions.includes(this._ext(entry.name))) {
            window.open(entry.internal_url, "_blank", "noopener,noreferrer");
            return;
        }
        this.dialog.add(NcPreviewDialog, {
            name: entry.name,
            url: this._fileUrl(entry, "preview"),
            internalUrl: entry.internal_url || "",
        });
    }

    openInNextcloud(entry) {
        if (entry.internal_url) {
            window.open(entry.internal_url, "_blank", "noopener,noreferrer");
        }
    }

    _fileUrl(entry, mode) {
        const p = new URLSearchParams(this._fileParams(entry));
        return `/bf_nc_browser/${this._urlMode(mode)}?${p.toString()}`;
    }

    download(entry) {
        window.open(this._fileUrl(entry, "download"), "_blank", "noopener,noreferrer");
    }

    // ----------------------------------------------------------------
    // Upload (button + drag & drop)
    // ----------------------------------------------------------------
    async _uploadOne(file) {
        const b64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result).split(",")[1]);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
        await this._call("upload_file", [this.state.relPath, file.name, b64]);
    }

    async _uploadMany(files) {
        if (!this.canMutate || !files.length) {
            return;
        }
        this.state.loading = true;
        try {
            for (const file of files) {
                await this._uploadOne(file);
            }
            this.notification.add(
                files.length > 1 ? _t("Fichiers televerses.") : _t("Fichier televerse."),
                { type: "success" }
            );
            await this.load(this.state.relPath);
        } catch (e) {
            this._err(e);
        } finally {
            this.state.loading = false;
        }
    }

    triggerUpload() {
        this.fileInput.el.click();
    }

    async onFileChosen(ev) {
        const files = Array.from(ev.target.files || []);
        ev.target.value = "";
        await this._uploadMany(files);
    }

    onDragOver(ev) {
        if (!this.canMutate) {
            return;
        }
        if (ev.dataTransfer.types.includes("application/x-nc-rel")) {
            return; // internal move, not an OS-file upload
        }
        ev.preventDefault();
        this.state.dragOver = true;
    }

    onDragLeave(ev) {
        if (!ev.currentTarget.contains(ev.relatedTarget)) {
            this.state.dragOver = false;
        }
    }

    async onDrop(ev) {
        if (ev.dataTransfer.types.includes("application/x-nc-rel")) {
            return; // handled by folder drop targets
        }
        ev.preventDefault();
        this.state.dragOver = false;
        await this._uploadMany(Array.from(ev.dataTransfer?.files || []));
    }

    // --- internal drag: move an entry into a folder ---
    onRowDragStart(entry, ev) {
        if (!this.canMutate) {
            ev.preventDefault();
            return;
        }
        ev.dataTransfer.setData("application/x-nc-rel", entry.rel);
        ev.dataTransfer.effectAllowed = "move";
    }

    onFolderDragOver(ev) {
        if (this.canMutate && ev.dataTransfer.types.includes("application/x-nc-rel")) {
            ev.preventDefault();
            ev.dataTransfer.dropEffect = "move";
        }
    }

    async onFolderDrop(targetRel, ev) {
        const src = ev.dataTransfer.getData("application/x-nc-rel");
        if (!src) {
            return; // OS-file drop: let it bubble to the panel uploader
        }
        ev.preventDefault();
        ev.stopPropagation();
        if (src === targetRel) {
            return;
        }
        await this._do("move_entry", [src, targetRel]);
    }

    // List-row variants: only folder rows are valid move targets.
    onListDragOver(entry, ev) {
        if (entry.is_dir) {
            this.onFolderDragOver(ev);
        }
    }

    async onListDrop(entry, ev) {
        if (!entry.is_dir) {
            return; // not a folder: let OS-file drops bubble to the uploader
        }
        await this.onFolderDrop(entry.rel, ev);
    }

    // ----------------------------------------------------------------
    // Mutations
    // ----------------------------------------------------------------
    newFolder() {
        this.dialog.add(NcPromptDialog, {
            title: _t("Nouveau dossier"),
            label: _t("Nom du dossier"),
            confirmLabel: _t("Creer"),
            onConfirm: (name) => this._do("make_folder", [this.state.relPath, name]),
        });
    }

    rename(entry) {
        this.dialog.add(NcPromptDialog, {
            title: _t("Renommer"),
            label: _t("Nouveau nom"),
            value: entry.name,
            confirmLabel: _t("Renommer"),
            onConfirm: (name) => {
                if (name !== entry.name) {
                    this._do("rename_entry", [entry.rel, name]);
                }
            },
        });
    }

    remove(entry) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Supprimer"),
            body: _t("Supprimer definitivement « %s » ?", entry.name),
            confirmLabel: _t("Supprimer"),
            confirm: () => this._do("delete_entry", [entry.rel]),
            cancel: () => {},
        });
    }

    // ----------------------------------------------------------------
    // Share (preset chooser)
    // ----------------------------------------------------------------
    shareDialog(entry) {
        this.dialog.add(NcShareDialog, {
            filename: entry.name,
            presets: this.state.presets,
            onChoose: (preset) => this.share(entry, preset),
        });
    }

    async share(entry, preset) {
        try {
            const res = await this._call("create_share", [
                entry.rel,
                preset ? preset.id : false,
                !!entry.is_dir,
            ]);
            if (!res.url) {
                return;
            }
            const body = res.password ? res.url + "\n" + _t("Mot de passe : ") + res.password : res.url;
            try {
                await navigator.clipboard.writeText(res.url);
                this.notification.add(
                    res.password
                        ? _t("Lien copie. Mot de passe : %s", res.password)
                        : _t("Lien de partage copie dans le presse-papiers."),
                    { type: "success", sticky: !!res.password }
                );
            } catch {
                this.notification.add(body, { title: _t("Lien de partage"), sticky: true });
            }
        } catch (e) {
            this._err(e);
        }
    }

    async link(entry) {
        let targets;
        try {
            targets = await this._call("knowledge_targets", []);
        } catch (e) {
            return this._err(e);
        }
        this.dialog.add(NcLinkDialog, {
            filename: entry.name,
            items: targets.items,
            articles: targets.articles,
            hasArticles: targets.has_articles,
            onConfirm: async (target, id) => {
                try {
                    if (target === "item") {
                        await this._call("link_to_knowledge_item", [entry.rel, id]);
                    } else {
                        await this._call("link_to_article", [entry.rel, id]);
                    }
                    this.notification.add(_t("Fichier lie."), { type: "success" });
                } catch (e) {
                    this._err(e);
                }
            },
        });
    }
}

export const ncBrowserWidget = {
    component: NcBrowser,
};
registry.category("view_widgets").add("nc_browser_panel", ncBrowserWidget);

/**
 * Standalone client-action variant: no record context, browses from the config
 * browser_root_prefix via the server-side root_* methods. Reuses the same
 * template; Knowledge linking is hidden (no project context).
 */
export class NcBrowserApp extends NcBrowser {
    static template = "bf_nextcloud_browser.NcBrowser";
    static components = { NcTreeNode };
    static props = { "*": true };

    get model() {
        return null;
    }
    get resId() {
        return null;
    }
    _scopeArgs() {
        return [];
    }
    _method(name) {
        return "root_" + name;
    }
    get needsRecord() {
        return false;
    }
    get canMutate() {
        return true;
    }
    get showLink() {
        return false;
    }
    _fileParams(entry) {
        return { rel_path: entry.rel };
    }
    _urlMode(mode) {
        return "root_" + mode;
    }
}

registry.category("actions").add("bf_nc_browser_app", NcBrowserApp);
