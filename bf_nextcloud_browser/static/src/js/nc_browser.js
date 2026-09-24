/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onWillUnmount } from "@odoo/owl";
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
        presets: { type: Array, optional: true },
        onConfirm: Function,
    };

    setup() {
        this.state = useState({
            target: this.props.items.length || !this.props.hasArticles ? "item" : "article",
            itemId: this.props.items[0] ? String(this.props.items[0].id) : false,
            articleId: this.props.articles[0] ? String(this.props.articles[0].id) : false,
            // Un lien interne par defaut : l'element de matrice est lu dans Odoo,
            // par des gens qui ont deja acces aux fichiers.
            linkChoice: "internal",
        });
    }

    get presets() {
        return this.props.presets || [];
    }

    get canConfirm() {
        return this.state.target === "item" ? !!this.state.itemId : !!this.state.articleId;
    }

    confirm() {
        const id = this.state.target === "item" ? this.state.itemId : this.state.articleId;
        const choice = this.state.linkChoice;
        const mode = choice === "internal" ? "internal" : "share";
        const presetId = choice.startsWith("preset:") ? Number(choice.slice(7)) : false;
        this.props.onConfirm(this.state.target, id, mode, presetId);
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
 * Share modal: the entry's existing shares (with their expiry, and a way to
 * revoke them), then the presets to create a new one.
 *
 * The existing shares came with 18.0.4.1.0: on a real account, most public
 * links had no expiry, and nothing in Odoo showed them.
 */
export class NcShareDialog extends Component {
    static template = "bf_nextcloud_browser.NcShareDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        filename: String,
        presets: Array,
        onChoose: Function,
        fetchShares: { type: Function, optional: true },
        revokeShare: { type: Function, optional: true },
    };

    setup() {
        this.notification = useService("notification");
        this.state = useState({ loading: !!this.props.fetchShares, shares: [], error: "" });
        onWillStart(() => this.refresh());
    }

    async refresh() {
        if (!this.props.fetchShares) {
            return;
        }
        this.state.loading = true;
        try {
            const res = await this.props.fetchShares();
            this.state.shares = (res && res.shares) || [];
            this.state.error = "";
        } catch (e) {
            this.state.error = (e && e.data && e.data.message) || (e && e.message) || _t("Erreur.");
        } finally {
            this.state.loading = false;
        }
    }

    presetLabel(p) {
        const parts = [p.access === "read_write" ? _t("lecture / écriture") : _t("lecture seule")];
        // Toujours une echeance : le serveur rend l'echeance reelle (celle du
        // prereglage, ou la duree par defaut de la configuration).
        parts.push(p.expiry_days ? _t("expire après %s jours", p.expiry_days) : _t("sans expiration"));
        if (p.password_protected) {
            parts.push(_t("mot de passe"));
        }
        return "(" + parts.join(", ") + ")";
    }

    async copy(share) {
        try {
            await navigator.clipboard.writeText(share.url);
            this.notification.add(_t("Lien copié."), { type: "success" });
        } catch {
            this.notification.add(share.url, { title: _t("Lien de partage"), sticky: true });
        }
    }

    async revoke(share) {
        if (!this.props.revokeShare) {
            return;
        }
        try {
            const res = await this.props.revokeShare(share);
            if (res && res.gone) {
                this.notification.add(
                    _t("Ce partage n'existe plus, ou Nextcloud ne vous le montre pas : rien n'a été retiré."),
                    { type: "warning" }
                );
            } else {
                this.notification.add(_t("Partage retiré."), { type: "success" });
            }
        } catch (e) {
            this.notification.add(
                (e && e.data && e.data.message) || (e && e.message) || _t("Erreur."),
                { type: "danger" }
            );
        }
        await this.refresh();
    }
}

/**
 * Which link to put in a message: the person chooses each time (decision of
 * 2026-09-14). An internal link opens only for someone who can already see
 * the file; a share opens for whoever has the link.
 */
export class NcInsertDialog extends Component {
    static template = "bf_nextcloud_browser.NcInsertDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        filename: String,
        isDir: Boolean,
        presets: Array,
        onChoose: Function,
    };

    presetLabel(p) {
        return NcShareDialog.prototype.presetLabel.call(this, p);
    }

    choose(mode, preset) {
        this.props.onChoose(mode, preset || null);
        this.props.close();
    }
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

    /** Below this many characters the box filters the folder; from it on, it searches the tree. */
    static SEARCH_MIN_CHARS = 3;
    static SEARCH_DELAY_MS = 350;

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
        // Preferences par personne, lues une fois au montage.
        let showHidden = false;
        try {
            showHidden = browser.localStorage.getItem("bf_nc_browser_hidden") === "1";
        } catch {
            // ignore malformed pref
        }
        this.state = useState({
            ready: false,
            loading: false,
            error: "",
            relPath: "",
            showHidden,
            dropTarget: "", // rel du dossier survole pendant un glisser
            maxUploadBytes: 0, // rempli par le serveur au premier listing
            breadcrumb: [],
            entries: [],
            dragOver: false, // OS-file drop on the panel
            tree: [],
            sortBy,
            sortDir,
            openExtensions: [],
            folderColor: "#2E3132",
            presets: [],
            filter: "",
            // Recherche dans l'arbre (18.0.4.1.0) : au-dela de quelques caracteres,
            // la boite ne filtre plus le dossier, elle cherche sous la racine.
            search: { term: "", loading: false, entries: [], truncated: false, done: false },
            // Partages des enfants du dossier courant : { rel: { public, other } }.
            shareStates: {},
            selected: [], // rels of checked entries
            // Connexion Nextcloud de la personne (18.0.4.0.0). Le navigateur
            // n'emprunte plus le compte de la configuration : sans connexion,
            // il n'affiche rien d'autre que la carte qui invite a en ouvrir une.
            conn: {
                checked: false,
                connected: false,
                pending: false,
                rejected: false,
                login: "",
                displayName: "",
                server: "",
                loginUrl: "",
            },
        });
        this._pollTimer = null;
        this._searchTimer = null;
        this._searchSeq = 0;
        this._shareSeq = 0;
        onWillStart(() => this._start());
        onWillUnmount(() => {
            this._stopPolling();
            browser.clearTimeout(this._searchTimer);
        });
    }

    async _start() {
        if (this.needsRecord && !this.resId) {
            return this.load();
        }
        try {
            this._applyConnection(await this._call("nc_status", []));
        } catch (e) {
            this.state.error =
                (e && e.data && e.data.message) || (e && e.message) || _t("Erreur de chargement.");
            this.state.ready = true;
            return;
        }
        if (this.state.conn.connected) {
            return this.load(this._rememberedPath());
        }
        if (this.state.conn.pending) {
            // Une approbation lancee avant un rechargement de la page : le flux
            // vit cote serveur, on reprend simplement le sondage.
            this._startPolling();
        }
        this.state.ready = true;
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
    /** Picker mode: the composer's dialog passes `onPick`, rows get « Insérer ». */
    get pickMode() {
        return typeof this.props.onPick === "function";
    }
    _fileParams(entry) {
        return { model: this.model, res_id: this.resId, rel_path: entry.rel };
    }
    _urlMode(mode) {
        return mode;
    }

    _err(e) {
        if (this._handleConnectionError(e)) {
            return;
        }
        const msg = (e && e.data && e.data.message) || (e && e.message) || _t("Erreur.");
        this.notification.add(msg, { type: "danger" });
    }

    // ----------------------------------------------------------------
    // Connexion Nextcloud par personne
    // ----------------------------------------------------------------
    /** Le nom de classe Python de l'erreur, tel que le serveur le serialise. */
    _ncErrorKind(e) {
        const name = (e && e.data && e.data.name) || "";
        if (name.endsWith(".NcNotConnected")) {
            return "not_connected";
        }
        if (name.endsWith(".NcTokenRejected")) {
            return "rejected";
        }
        return "";
    }

    /**
     * Une connexion absente ou revoquee n'est pas une erreur a afficher en
     * rouge : c'est l'etat « connectez-vous ». On bascule sur la carte.
     */
    _handleConnectionError(e) {
        const kind = this._ncErrorKind(e);
        if (!kind) {
            return false;
        }
        this.state.conn.connected = false;
        this.state.conn.rejected = kind === "rejected";
        this.state.entries = [];
        this.state.breadcrumb = [];
        this.state.error = "";
        return true;
    }

    _applyConnection(res) {
        Object.assign(this.state.conn, {
            checked: true,
            // Une reconnexion en cours n'est pas une connexion : tant que la
            // nouvelle approbation n'est pas arrivee, on garde la carte et son
            // attente, meme si l'ancienne ligne dit encore « connecte ».
            connected: !!res.connected && !res.pending,
            pending: !!res.pending,
            login: res.login || "",
            displayName: res.display_name || res.login || "",
            server: res.server || this.state.conn.server,
        });
        if (res.connected) {
            this.state.conn.rejected = false;
        }
    }

    get showConnectCard() {
        return (
            this.state.conn.checked &&
            !this.state.conn.connected &&
            !this.state.error &&
            !(this.needsRecord && !this.resId)
        );
    }

    async connect() {
        // La fenetre s'ouvre pendant le clic, avant l'appel au serveur : un
        // window.open fait apres un await est bloque par les navigateurs.
        const win = window.open("", "bf_nc_connect", "width=560,height=720");
        try {
            const res = await this._call("nc_connect_start", []);
            this._applyConnection(res);
            this.state.conn.loginUrl = res.login_url;
            if (win && !win.closed) {
                // La page de Nextcloud ne doit pas pouvoir piloter l'onglet Odoo.
                // Le prix : Odoo ne peut plus fermer cette fenetre (Chrome refuse
                // un close() a qui n'est plus son ouvreur), d'ou la consigne de la
                // fermer soi-meme une fois l'acces autorise.
                win.opener = null;
                win.location.href = res.login_url;
            }
            this._startPolling();
        } catch (e) {
            if (win && !win.closed) {
                win.close();
            }
            this._err(e);
        }
    }

    reopenConnectWindow() {
        if (!this.state.conn.loginUrl) {
            return;
        }
        window.open(this.state.conn.loginUrl, "bf_nc_connect", "width=560,height=720,noopener");
    }

    _startPolling() {
        this._stopPolling();
        this._pollTimer = browser.setInterval(() => this._pollOnce(), 2000);
    }

    _stopPolling() {
        if (this._pollTimer) {
            browser.clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    }

    async _pollOnce() {
        if (this._polling) {
            return; // un sondage lent ne doit pas en empiler un second
        }
        this._polling = true;
        try {
            const res = await this._call("nc_connect_poll", []);
            this._applyConnection(res);
            if (res.error) {
                this._stopPolling();
                this.state.conn.loginUrl = "";
                this.notification.add(res.error, { type: "danger", sticky: true });
                if (res.connected) {
                    // Reconnexion refusee : l'ancienne connexion tient toujours.
                    await this.load(this._rememberedPath());
                }
                return;
            }
            if (res.expired) {
                this._stopPolling();
                this.state.conn.loginUrl = "";
                this.notification.add(
                    _t("La demande de connexion a expire. Recommencez."),
                    { type: "warning" }
                );
                if (res.connected) {
                    await this.load(this._rememberedPath());
                }
                return;
            }
            if (res.just_connected) {
                this._stopPolling();
                this.state.conn.loginUrl = "";
                this.notification.add(
                    _t("Nextcloud connecte : %s", this.state.conn.displayName),
                    { type: "success" }
                );
                await this.load(this._rememberedPath());
            } else if (!res.pending) {
                // Le flux a pris fin ailleurs (annule dans un autre onglet) :
                // l'ancienne connexion, s'il y en a une, tient toujours.
                this._stopPolling();
                this.state.conn.loginUrl = "";
                if (res.connected) {
                    await this.load(this._rememberedPath());
                }
            }
        } catch (e) {
            this._stopPolling();
            this._err(e);
        } finally {
            this._polling = false;
        }
    }

    async cancelConnect() {
        this._stopPolling();
        try {
            this._applyConnection(await this._call("nc_connect_cancel", []));
        } catch (e) {
            this._err(e);
        }
        this.state.conn.loginUrl = "";
        if (this.state.conn.connected) {
            // Reconnexion abandonnee : l'ancienne connexion tient toujours.
            await this.load(this._rememberedPath());
        }
    }

    disconnect() {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Deconnecter Nextcloud"),
            body: _t(
                "Le navigateur n'aura plus acces a vos fichiers, et l'acces donne a Odoo sera supprime de votre Nextcloud."
            ),
            confirmLabel: _t("Deconnecter"),
            confirm: async () => {
                try {
                    this._applyConnection(await this._call("nc_disconnect", []));
                    this.state.entries = [];
                    this.state.breadcrumb = [];
                    this.state.tree = [];
                } catch (e) {
                    this._err(e);
                }
            },
            cancel: () => {},
        });
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
            this.state.maxUploadBytes = res.max_upload_bytes || this.state.maxUploadBytes;
            this._rememberPath(res.rel_path);
            this.state.error = "";
            this.state.selected = [];
            this.state.filter = "";
            this._resetSearch();
            this._syncTree(res.rel_path, res.entries);
            this._loadShareStates(res.rel_path);
        } catch (e) {
            if (this._handleConnectionError(e)) {
                return;
            }
            // Le dossier memorise a pu etre supprime ou renomme depuis la
            // derniere visite : on retombe sur la racine plutot que d'ouvrir
            // le panneau sur une erreur.
            if (relPath) {
                this._rememberPath("");
                this.state.loading = false;
                return this.load("");
            }
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
        const source = this.searching ? this.state.search.entries : this.state.entries;
        let list = this.state.showHidden ? source : source.filter((e) => !e.is_hidden);
        if (q && !this.searching) {
            list = list.filter((e) => (e.name || "").toLowerCase().includes(q));
        }
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

    /**
     * Cle de memoire du dernier dossier ouvert. Elle porte la portee, sinon le
     * panneau autonome et l'onglet d'une fiche se voleraient leur position.
     */
    get _pathKey() {
        return "bf_nc_browser_path:" + (this.needsRecord ? `${this.model}:${this.resId}` : "root");
    }

    _rememberedPath() {
        try {
            return browser.localStorage.getItem(this._pathKey) || "";
        } catch {
            return "";
        }
    }

    _rememberPath(rel) {
        try {
            browser.localStorage.setItem(this._pathKey, rel || "");
        } catch {
            // navigation privee, quota plein : on rouvre a la racine, sans plus
        }
    }

    toggleHidden() {
        this.state.showHidden = !this.state.showHidden;
        this.state.selected = [];
        try {
            browser.localStorage.setItem(
                "bf_nc_browser_hidden",
                this.state.showHidden ? "1" : "0"
            );
        } catch {
            // sans stockage, le choix ne vaut que pour cette ouverture
        }
    }

    get hiddenCount() {
        return this.state.entries.filter((e) => e.is_hidden).length;
    }

    // ----------------------------------------------------------------
    // Recherche dans l'arbre (18.0.4.1.0)
    // ----------------------------------------------------------------
    /** Is the box asking for a search of the whole tree (rather than a filter)? */
    get searching() {
        return (this.state.filter || "").trim().length >= this.constructor.SEARCH_MIN_CHARS;
    }

    _resetSearch() {
        browser.clearTimeout(this._searchTimer);
        this._searchSeq++;
        Object.assign(this.state.search, { term: "", loading: false, entries: [], truncated: false, done: false });
    }

    onFilterInput() {
        this.state.selected = [];
        browser.clearTimeout(this._searchTimer);
        if (!this.searching) {
            this._resetSearch();
            return;
        }
        Object.assign(this.state.search, { loading: true, done: false });
        this._searchTimer = browser.setTimeout(() => this._runSearch(), this.constructor.SEARCH_DELAY_MS);
    }

    async _runSearch() {
        const term = (this.state.filter || "").trim();
        const seq = ++this._searchSeq;
        this.state.search.loading = true;
        try {
            const res = await this._call("search_entries", [term]);
            if (seq !== this._searchSeq) {
                return; // a later keystroke already asked something else
            }
            Object.assign(this.state.search, {
                term: res.term,
                entries: res.entries || [],
                truncated: !!res.truncated,
                done: true,
            });
        } catch (e) {
            if (seq === this._searchSeq) {
                this.state.search.done = false;
                this._err(e);
            }
        } finally {
            if (seq === this._searchSeq) {
                this.state.search.loading = false;
            }
        }
    }

    clearSearch() {
        this.state.filter = "";
        this.state.selected = [];
        this._resetSearch();
    }

    /** A search hit's folder, as shown under its name. */
    parentLabel(entry) {
        return entry.parent_rel ? entry.parent_rel : _t("Racine");
    }

    // ----------------------------------------------------------------
    // Liens et partages (18.0.4.1.0)
    // ----------------------------------------------------------------
    async _loadShareStates(rel) {
        const seq = ++this._shareSeq;
        this.state.shareStates = {};
        try {
            const res = await this._call("share_states", [rel || ""]);
            if (seq === this._shareSeq) {
                this.state.shareStates = (res && res.states) || {};
            }
        } catch {
            // Sans l'etat des partages, la liste reste utilisable : pas de pastille.
        }
    }

    shareState(entry) {
        return (!this.searching && this.state.shareStates[entry.rel]) || null;
    }

    async copyInternalLink(entry) {
        if (!entry.link_url) {
            return;
        }
        try {
            await navigator.clipboard.writeText(entry.link_url);
            this.notification.add(
                _t("Lien interne copié : il s'ouvre pour qui a déjà accès au fichier dans Nextcloud."),
                { type: "success" }
            );
        } catch {
            this.notification.add(entry.link_url, { title: _t("Lien interne"), sticky: true });
        }
    }

    pickEntry(entry) {
        if (!this.pickMode) {
            return;
        }
        this.dialog.add(NcInsertDialog, {
            filename: entry.name,
            isDir: !!entry.is_dir,
            presets: this.state.presets,
            onChoose: async (mode, preset) => {
                try {
                    const res = await this._call("make_link", [entry.rel, mode, preset ? preset.id : false]);
                    this.props.onPick(res);
                } catch (e) {
                    this._err(e);
                }
            },
        });
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

    /** Selection and bulk delete stay in one folder: search hits span the whole tree. */
    get canSelect() {
        return this.canMutate && !this.searching;
    }

    get allVisibleSelected() {
        const vis = this.sortedEntries;
        return vis.length > 0 && vis.every((e) => this.isSelected(e.rel));
    }

    toggleSelectAll() {
        if (!this.canSelect) {
            return;
        }
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
        if (!rels.length || !this.canSelect) {
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
    /**
     * Ouvrir un document dans Nextcloud (donc dans Collabora pour les formats
     * bureautiques), en REUTILISANT le meme onglet.
     *
     * `window.open(url, "_blank")` en ouvrait un neuf a chaque clic : dix
     * documents consultes, dix onglets. Un nom de fenetre les rassemble.
     *
     * ⚠️ Le lien reste `/f/<fileid>`, donc l'ouverture se fait dans la session
     * Nextcloud DE LA PERSONNE. L'API d'edition directe irait droit a
     * l'editeur, mais ses jetons sont lies au compte de service : tout le
     * monde editerait sous la meme identite et l'historique des versions
     * deviendrait faux.
     */
    openOffice(url) {
        const win = window.open(url, "bf_nc_office", "noopener,noreferrer");
        if (win && !win.closed) {
            win.focus(); // l'onglet existe deja : le ramener devant
        }
    }

    /** Un clic sur ce fichier ouvrira-t-il Collabora ? */
    isOfficeFile(entry) {
        return (
            !entry.is_dir &&
            !!entry.internal_url &&
            this.state.openExtensions.includes(this._ext(entry.name))
        );
    }

    onEntryClick(entry) {
        if (entry.is_dir) {
            this.load(entry.rel);
            return;
        }
        if (this.pickMode) {
            this.pickEntry(entry);
            return;
        }
        if (this.isOfficeFile(entry)) {
            this.openOffice(entry.internal_url);
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
            this.openOffice(entry.internal_url);
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

    /**
     * Televerser, en refusant AVANT lecture ce que le serveur refuserait apres.
     *
     * Le plafond etait connu du seul serveur : le navigateur lisait donc un
     * fichier de 500 Mo en memoire, l'encodait en base64 et l'envoyait, pour se
     * faire refuser a l'arrivee. On le sait maintenant d'avance.
     *
     * Et un echec ne fait plus tomber le reste du lot : chaque fichier est tente
     * separement, le compte rendu dit ce qui est passe et ce qui a bloque.
     */
    async _uploadMany(files) {
        if (!this.canMutate || !files.length) {
            return;
        }
        const cap = this.state.maxUploadBytes;
        const tooBig = cap ? files.filter((f) => f.size > cap) : [];
        const toSend = cap ? files.filter((f) => f.size <= cap) : files;

        if (tooBig.length) {
            const capMo = Math.floor(cap / (1024 * 1024));
            this.notification.add(
                tooBig.length === 1
                    ? _t("« %s » depasse la limite de %s Mo et n'a pas ete envoye.", tooBig[0].name, capMo)
                    : _t("%s fichiers depassent la limite de %s Mo et n'ont pas ete envoyes.", tooBig.length, capMo),
                { type: "warning", sticky: true }
            );
        }
        if (!toSend.length) {
            return;
        }

        this.state.loading = true;
        const failed = [];
        let sent = 0;
        try {
            for (const file of toSend) {
                try {
                    await this._uploadOne(file);
                    sent++;
                } catch (e) {
                    failed.push(file.name);
                    if (failed.length === 1) {
                        this._err(e); // la cause, une seule fois
                    }
                }
            }
            if (sent) {
                this.notification.add(
                    sent > 1 ? _t("%s fichiers televerses.", sent) : _t("Fichier televerse."),
                    { type: "success" }
                );
            }
            if (failed.length) {
                this.notification.add(
                    _t("Echec pour : %s", failed.join(", ")),
                    { type: "danger", sticky: true }
                );
            }
            await this.load(this.state.relPath);
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

    // Un glisser abandonne (touche Echap, relache dans le vide) laisserait
    // sinon la derniere ligne survolee allumee.
    onRowDragEnd() {
        this.state.dropTarget = "";
    }

    onFolderDragOver(ev, rel) {
        if (this.canMutate && ev.dataTransfer.types.includes("application/x-nc-rel")) {
            ev.preventDefault();
            ev.dataTransfer.dropEffect = "move";
            // La regle CSS `.o_nc_drop_target` existait depuis la v3 sans que
            // personne ne pose la classe : on visait un dossier a l'aveugle.
            if (rel !== undefined) {
                this.state.dropTarget = rel;
            }
        }
    }

    onFolderDragLeave() {
        this.state.dropTarget = "";
    }

    isDropTarget(rel) {
        return this.state.dropTarget === rel && !!this.state.dropTarget;
    }

    async onFolderDrop(targetRel, ev) {
        this.state.dropTarget = "";
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
            this.onFolderDragOver(ev, entry.rel);
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
            fetchShares: () => this._call("entry_shares", [entry.rel]),
            revokeShare: async (share) => {
                const res = await this._call("revoke_share", [share.id]);
                if (!this.searching) {
                    this._loadShareStates(this.state.relPath);
                }
                return res;
            },
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
            if (!this.searching) {
                this._loadShareStates(this.state.relPath);
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
            presets: this.state.presets,
            onConfirm: async (target, id, mode, presetId) => {
                try {
                    const res =
                        target === "item"
                            ? await this._call("link_to_knowledge_item", [entry.rel, id, mode, presetId || false])
                            : await this._call("link_to_article", [entry.rel, id, mode, presetId || false]);
                    this.notification.add(_t("Fichier lie."), { type: "success" });
                    if (res && res.password) {
                        // Sinon personne ne connait le mot de passe du lien joint.
                        this.notification.add(
                            _t("Mot de passe du partage, à transmettre séparément : %s", res.password),
                            { type: "warning", sticky: true }
                        );
                    }
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
