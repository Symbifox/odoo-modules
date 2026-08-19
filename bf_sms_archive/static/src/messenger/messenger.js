/** @odoo-module **/
import { Component, useState, useRef, onWillStart, onWillUnmount, onPatched } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";

const MODEL = "sms.archive.thread";
const PAGE = 50;
const MAX_MEDIA = 3;

// Alphabet GSM 7 bits (miroir de sms.archive.message côté serveur) : sert au
// compteur du composeur. Hors de ces tables → UCS-2 (enveloppe de 70 car.).
const GSM7_BASIC = new Set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./" +
    "0123456789:;<=>?¡" +
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿" +
    "abcdefghijklmnopqrstuvwxyzäöñüà"
);
const GSM7_EXT = new Set("^{}\\[~]|€");
const SMS_GSM7_LEN = 160;
const SMS_UCS2_LEN = 70;

class SmsMessenger extends Component {
    static template = "bf_sms_archive.Messenger";
    static components = { Dropdown, DropdownItem };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.busService = useService("bus_service");

        this.messagesRef = useRef("messages");
        this.fileInputRef = useRef("fileInput");

        this.state = useState({
            threads: [],
            lines: [],
            activeThreadId: null,
            activeThread: null,
            conversation: [],
            hasMore: false,
            loadingThreads: false,
            loadingConv: false,
            sending: false,
            search: "",
            archived: false,
            composeBody: "",
            selectedLineId: null,
            attachments: [],
            newConvMode: false,
            newConvPhone: "",
            newConvResults: [],
            newConvPartnerId: false,
            // multi-select (bulk archive)
            selectMode: false,
            selectedIds: [],
            // poster sur une tâche : sélection de messages + sélecteur de tâche
            msgSelectMode: false,
            selectedMsgIds: [],
            taskPickerOpen: false,
            taskTargets: [],
            taskSearch: "",
            taskResults: [],
            taskPosting: false,
            autoTaskId: false,
            autoTaskName: "",
            // link unknown number to an Odoo contact
            linkMode: false,
            linkSearch: "",
            linkResults: [],
            // filter by line + in-conversation search
            lineFilter: false,
            convSearch: "",
            convSearchMode: false,
            // sound on inbound
            soundOn: localStorage.getItem("bf_sms_sound") !== "0",
            // UI density: simple (default, decluttered) vs advanced (legacy full toolbars)
            advanced: localStorage.getItem("bf_sms_ui_advanced") === "1",
            // Web Push (notifications mobiles, navigateur fermé)
            pushSupported: "serviceWorker" in navigator && "PushManager" in window,
            pushReady: false,
        });

        this.tz = false;  // user timezone (resolved server-side via bf_timezone)
        this._audioCtx = null;
        this._busCb = (payload) => this._onBusMessage(payload);
        // Read-state pings (no beep): just refresh the thread list / badges.
        this._readCb = () => { if (!this.state.archived) { this.loadThreads(); } };
        // Set when a freshly opened/sent conversation must snap to the bottom
        // once the DOM has actually been patched (handled in onPatched).
        this._scrollPending = false;

        onWillStart(async () => {
            const cfg = await this.orm.call(MODEL, "get_messenger_config", []);
            this.tz = cfg.tz || false;
            this._vapidKey = cfg.vapid_public_key || "";
            await Promise.all([this.loadLines(), this.loadThreads()]);
            // Le client est déjà abonné au canal de son partenaire (bus core) ; on
            // se contente d'écouter le type de notification.
            this.busService.subscribe("sms.archive/new", this._busCb);
            this.busService.subscribe("sms.archive/read", this._readCb);
            this._requestNotifyPermission();
            // Web Push : réveille le téléphone navigateur fermé (indépendant du
            // bus). Non bloquant — n'empêche jamais l'ouverture de la messagerie.
            this._setupPush();
        });

        onWillUnmount(() => {
            this.busService.unsubscribe("sms.archive/new", this._busCb);
            this.busService.unsubscribe("sms.archive/read", this._readCb);
        });

        // Scroll the open conversation to the latest message AFTER the DOM has
        // been patched with it — a microtask fires too early (old layout).
        onPatched(() => {
            if (this._scrollPending) {
                this._scrollPending = false;
                this._scrollToBottom();
            }
        });
    }

    // ── Data loading ───────────────────────────────────────────

    async loadLines() {
        this.state.lines = await this.orm.call(MODEL, "get_lines", []);
        if (!this.state.selectedLineId && this.state.lines.length) {
            const def = this.state.lines.find((l) => l.is_default) || this.state.lines[0];
            this.state.selectedLineId = def.id;
        }
    }

    async loadThreads() {
        this.state.loadingThreads = true;
        try {
            this.state.threads = await this.orm.call(MODEL, "get_messenger_threads", [], {
                archived: this.state.archived,
                search: this.state.search || false,
                line_id: this.state.lineFilter || false,
            });
        } finally {
            this.state.loadingThreads = false;
        }
    }

    onThreadClick(t) {
        if (this.state.selectMode) {
            this.toggleSelected(t.id);
        } else {
            this.openThread(t.id);
        }
    }

    async openThread(threadId) {
        // Save the in-progress draft of the thread we're leaving.
        this._saveDraft(this.state.activeThreadId, this.state.composeBody);
        this.state.activeThreadId = threadId;
        this.state.newConvMode = false;
        this.state.linkMode = false;
        this.state.convSearch = "";
        this.state.convSearchMode = false;
        this.state.msgSelectMode = false;
        this.state.selectedMsgIds = [];
        this.state.taskPickerOpen = false;
        this.state.loadingConv = true;
        try {
            const res = await this.orm.call(MODEL, "get_conversation", [threadId], {
                limit: PAGE,
            });
            this.state.activeThread = res.thread;
            this.state.conversation = res.messages;
            this.state.hasMore = res.has_more;
            this.state.composeBody = this._loadDraft(threadId);
            // The thread was just marked read server-side → reflect locally.
            const t = this.state.threads.find((x) => x.id === threadId);
            if (t) {
                t.unread_count = 0;
            }
            this._scrollPending = true;
        } finally {
            this.state.loadingConv = false;
        }
        // Non bloquant : sert au libellé du bouton « → Tâche » et à l'état du suivi.
        this._loadTaskTargets();
    }

    async _loadTaskTargets() {
        try {
            const res = await this.orm.call(MODEL, "messenger_task_targets", [
                this.state.activeThreadId,
            ]);
            this.state.taskTargets = res.tasks || [];
            this.state.autoTaskId = res.auto_post_task_id || false;
            this.state.autoTaskName = res.auto_post_task_name || "";
        } catch {
            this.state.taskTargets = [];
            this.state.autoTaskId = false;
            this.state.autoTaskName = "";
        }
    }

    async loadMore() {
        if (!this.state.conversation.length) {
            return;
        }
        const beforeId = this.state.conversation[0].id;
        const res = await this.orm.call(MODEL, "get_conversation", [this.state.activeThreadId], {
            before_id: beforeId,
            limit: PAGE,
        });
        this.state.conversation = [...res.messages, ...this.state.conversation];
        this.state.hasMore = res.has_more;
    }

    // ── Search / filters ───────────────────────────────────────

    async onSearchInput(ev) {
        this.state.search = ev.target.value;
        await this.loadThreads();
    }

    async toggleArchived() {
        this.state.archived = !this.state.archived;
        this.state.activeThreadId = null;
        this.state.activeThread = null;
        this.state.conversation = [];
        await this.loadThreads();
    }

    async archiveActive() {
        if (!this.state.activeThreadId) {
            return;
        }
        await this.orm.call(MODEL, "messenger_set_archived", [
            this.state.activeThreadId,
            !this.state.archived,
        ]);
        this.notification.add(
            this.state.archived ? "Conversation désarchivée" : "Conversation archivée",
            { type: "success" }
        );
        this.state.activeThreadId = null;
        this.state.activeThread = null;
        this.state.conversation = [];
        await this.loadThreads();
    }

    // ── Multi-select (bulk archive) ────────────────────────────

    toggleSelectMode() {
        this.state.selectMode = !this.state.selectMode;
        this.state.selectedIds = [];
    }

    toggleUiMode() {
        this.state.advanced = !this.state.advanced;
        localStorage.setItem("bf_sms_ui_advanced", this.state.advanced ? "1" : "0");
    }

    toggleSelected(id) {
        const i = this.state.selectedIds.indexOf(id);
        if (i === -1) {
            this.state.selectedIds.push(id);
        } else {
            this.state.selectedIds.splice(i, 1);
        }
    }

    isSelected(id) {
        return this.state.selectedIds.includes(id);
    }

    async bulkArchive() {
        if (!this.state.selectedIds.length) {
            return;
        }
        await this.orm.call(MODEL, "messenger_bulk_archive", [
            this.state.selectedIds,
            !this.state.archived,
        ]);
        const n = this.state.selectedIds.length;
        this.notification.add(
            `${n} conversation(s) ${this.state.archived ? "désarchivée(s)" : "archivée(s)"}`,
            { type: "success" }
        );
        this.state.selectMode = false;
        this.state.selectedIds = [];
        await this.loadThreads();
    }

    // ── Poster sur une tâche ───────────────────────────────────
    // Le sorcier existe depuis toujours, mais son seul point d'entrée était le menu
    // Actions de la vue liste des messages : il fallait quitter la Messagerie, ouvrir le
    // fil en vue liste, cocher, puis passer par le menu. Tout ce bloc sert à faire le même
    // geste sans sortir de la conversation.

    toggleMsgSelectMode() {
        this.state.msgSelectMode = !this.state.msgSelectMode;
        this.state.selectedMsgIds = [];
    }

    toggleMsgSelected(id) {
        const i = this.state.selectedMsgIds.indexOf(id);
        if (i === -1) {
            this.state.selectedMsgIds.push(id);
        } else {
            this.state.selectedMsgIds.splice(i, 1);
        }
    }

    isMsgSelected(id) {
        return this.state.selectedMsgIds.includes(id);
    }

    /** Surlignage : uniquement pendant la sélection, pour ne pas marquer le fil au repos. */
    isMsgHighlighted(id) {
        return this.state.msgSelectMode && this.isMsgSelected(id);
    }

    rowClass(msg) {
        const side = msg.direction === "out" ? "o_sms_out" : "o_sms_in";
        return this.state.msgSelectMode ? `${side} o_selectable` : side;
    }

    onBubbleClick(msg) {
        if (this.state.msgSelectMode) {
            this.toggleMsgSelected(msg.id);
        }
    }

    get selectedMsgCount() {
        return this.state.selectedMsgIds.length;
    }

    /** Ids à poster : la sélection si elle existe, sinon toute la conversation chargée. */
    get postScopeIds() {
        if (this.state.selectedMsgIds.length) {
            return this.state.selectedMsgIds.slice();
        }
        return this.state.conversation.map((m) => m.id);
    }

    _rpcError(e) {
        // La charge d'erreur d'un RPC Owl est dans e.data.message, pas e.message.
        return (e && e.data && e.data.message) || (e && e.message) || "Erreur inattendue";
    }

    async openTaskPicker() {
        if (!this.state.activeThreadId) {
            return;
        }
        try {
            const res = await this.orm.call(MODEL, "messenger_task_targets", [
                this.state.activeThreadId,
            ]);
            this.state.taskTargets = res.tasks || [];
            this.state.autoTaskId = res.auto_post_task_id || false;
            this.state.autoTaskName = res.auto_post_task_name || "";
            // Une seule tâche déjà rattachée : c'est de très loin le cas courant, on poste
            // sans rien demander. Le sélecteur ne sert qu'aux cas ambigus.
            if (this.state.taskTargets.length === 1) {
                await this.postToTask(this.state.taskTargets[0].id);
                return;
            }
            this.state.taskSearch = "";
            this.state.taskResults = [];
            this.state.taskPickerOpen = true;
            if (!this.state.taskTargets.length) {
                await this._searchTasks("");
            }
        } catch (e) {
            this.notification.add(this._rpcError(e), { type: "danger" });
        }
    }

    closeTaskPicker() {
        this.state.taskPickerOpen = false;
    }

    async onTaskSearchInput(ev) {
        this.state.taskSearch = ev.target.value;
        await this._searchTasks(this.state.taskSearch);
    }

    async _searchTasks(term) {
        try {
            this.state.taskResults = await this.orm.call(MODEL, "messenger_search_tasks", [
                term || "",
            ]);
        } catch (e) {
            this.state.taskResults = [];
            this.notification.add(this._rpcError(e), { type: "danger" });
        }
    }

    async postToTask(taskId) {
        if (!this.state.activeThreadId || this.state.taskPosting) {
            return;
        }
        this.state.taskPosting = true;
        try {
            const res = await this.orm.call(
                MODEL,
                "messenger_post_to_task",
                [this.state.activeThreadId, taskId],
                { message_ids: this.postScopeIds }
            );
            this.notification.add(
                `${res.count} message(s) posté(s) sur « ${res.task_name} »`,
                { type: "success" }
            );
            this.state.taskPickerOpen = false;
            this.state.msgSelectMode = false;
            this.state.selectedMsgIds = [];
        } catch (e) {
            this.notification.add(this._rpcError(e), { type: "danger" });
        } finally {
            this.state.taskPosting = false;
        }
    }

    /** Sorcier complet : aperçu, choix libre de la fiche, suivi automatique.
     *  Le sélecteur rapide ci-dessus ne connaît que les tâches ; le sorcier,
     *  lui, atteint n'importe quelle fiche dotée d'un chatter. */
    openTaskWizard() {
        const ids = this.postScopeIds;
        if (!ids.length) {
            return;
        }
        this.state.taskPickerOpen = false;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Poster sur une fiche",
            res_model: "sms.archive.post.to.task.wizard",
            view_mode: "form",
            views: [[false, "form"]],
            target: "new",
            context: { default_message_ids: [[6, 0, ids]] },
        });
    }

    async toggleAutoTask() {
        if (!this.state.activeThreadId) {
            return;
        }
        try {
            const next = this.state.autoTaskId
                ? false
                : (this.state.taskTargets[0] && this.state.taskTargets[0].id);
            if (!next && !this.state.autoTaskId) {
                this.notification.add(
                    "Rattachez d'abord la conversation à une tâche.",
                    { type: "warning" }
                );
                return;
            }
            const res = await this.orm.call(MODEL, "messenger_set_auto_task", [
                this.state.activeThreadId,
                next,
            ]);
            this.state.autoTaskId = res.auto_post_task_id || false;
            this.state.autoTaskName = res.auto_post_task_name || "";
            this.notification.add(
                this.state.autoTaskId
                    ? `Les prochains messages iront dans « ${this.state.autoTaskName} »`
                    : "Suivi automatique désactivé",
                { type: "success" }
            );
        } catch (e) {
            this.notification.add(this._rpcError(e), { type: "danger" });
        }
    }

    // ── Contact linking / opening ──────────────────────────────

    openPartner(partnerId) {
        if (!partnerId) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "res.partner",
            res_id: partnerId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    toggleLinkMode() {
        this.state.linkMode = !this.state.linkMode;
        this.state.linkSearch = "";
        this.state.linkResults = [];
    }

    async onLinkSearch(ev) {
        const term = ev.target.value;
        this.state.linkSearch = term;
        if (!term || term.length < 2) {
            this.state.linkResults = [];
            return;
        }
        const res = await this.orm.call("res.partner", "name_search", [], {
            name: term,
            limit: 8,
        });
        this.state.linkResults = res.map((r) => ({ id: r[0], name: r[1] }));
    }

    async linkPartner(partnerId) {
        const updated = await this.orm.call(MODEL, "messenger_link_partner", [
            this.state.activeThreadId,
            partnerId,
        ]);
        this.state.activeThread = updated;
        this.state.linkMode = false;
        this.state.linkResults = [];
        this.state.linkSearch = "";
        await this.loadThreads();
    }

    avatarUrl(t) {
        return t && t.partner_id ? `/web/image/res.partner/${t.partner_id}/avatar_128` : null;
    }

    async createPartner() {
        const updated = await this.orm.call(MODEL, "messenger_create_partner", [
            this.state.activeThreadId,
            this.state.linkSearch || "",
        ]);
        this.state.activeThread = updated;
        this.state.linkMode = false;
        this.state.linkSearch = "";
        this.state.linkResults = [];
        await this.loadThreads();
    }

    // ── Read state / pin ───────────────────────────────────────

    async markAllRead() {
        const summary = await this.orm.call(MODEL, "mark_all_read", []);
        await this.loadThreads();
        this.notification.add(`${summary.total === 0 ? "Tout est lu" : ""}`.trim() || "Marqué comme lu", {
            type: "success",
        });
    }

    async markActiveUnread() {
        if (!this.state.activeThreadId) {
            return;
        }
        await this.orm.call(MODEL, "messenger_set_thread_read", [this.state.activeThreadId, false]);
        await this.loadThreads();
        this.notification.add("Conversation marquée non lue", { type: "info" });
    }

    async togglePin() {
        if (!this.state.activeThreadId) {
            return;
        }
        const pinned = await this.orm.call(MODEL, "messenger_toggle_pin", [this.state.activeThreadId]);
        if (this.state.activeThread) {
            this.state.activeThread.is_pinned = pinned;
        }
        await this.loadThreads();
    }

    // ── Line filter + in-conversation search ───────────────────

    async onLineFilterChange(ev) {
        const v = ev.target.value;
        this.state.lineFilter = v ? parseInt(v, 10) : false;
        await this.loadThreads();
    }

    toggleConvSearch() {
        this.state.convSearchMode = !this.state.convSearchMode;
        this.state.convSearch = "";
    }

    onConvSearchInput(ev) {
        this.state.convSearch = ev.target.value;
    }

    get visibleMessages() {
        const q = (this.state.convSearch || "").trim().toLowerCase();
        if (!q) {
            return this.state.conversation;
        }
        return this.state.conversation.filter((m) => (m.body || "").toLowerCase().includes(q));
    }

    // ── Drafts (persisted per thread) ──────────────────────────

    _draftKey(id) {
        return "bf_sms_draft_" + id;
    }

    _saveDraft(id, text) {
        if (!id) {
            return;
        }
        try {
            if (text) {
                localStorage.setItem(this._draftKey(id), text);
            } else {
                localStorage.removeItem(this._draftKey(id));
            }
        } catch {
            // localStorage indisponible (mode privé) — on ignore
        }
    }

    _loadDraft(id) {
        try {
            return localStorage.getItem(this._draftKey(id)) || "";
        } catch {
            return "";
        }
    }

    _clearDraft(id) {
        this._saveDraft(id, "");
    }

    onComposeInput(ev) {
        // t-model met déjà à jour state.composeBody ; ici on persiste le brouillon.
        this._saveDraft(this.state.activeThreadId, ev.target.value);
    }

    // ── Sound ──────────────────────────────────────────────────

    toggleSound() {
        this.state.soundOn = !this.state.soundOn;
        try {
            localStorage.setItem("bf_sms_sound", this.state.soundOn ? "1" : "0");
        } catch {
            // ignore
        }
    }

    _beep() {
        if (!this.state.soundOn) {
            return;
        }
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) {
                return;
            }
            this._audioCtx = this._audioCtx || new Ctx();
            const ctx = this._audioCtx;
            if (ctx.state === "suspended") {
                ctx.resume();
            }
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sine";
            osc.frequency.value = 880;
            gain.gain.setValueAtTime(0.0001, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.15, ctx.currentTime + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.26);
        } catch {
            // audio bloqué (autoplay policy) — silencieux
        }
    }

    // ── Compose / send ─────────────────────────────────────────

    startNewConversation() {
        this.state.newConvMode = true;
        this.state.activeThreadId = null;
        this.state.activeThread = null;
        this.state.conversation = [];
        this.state.newConvPhone = "";
        this.state.newConvResults = [];
        this.state.newConvPartnerId = false;
        this.state.composeBody = "";
        this.state.attachments = [];
    }

    onComposeInput(ev) {
        this.state.composeBody = ev.target.value;
    }

    async onNewConvInput(ev) {
        const term = ev.target.value;
        this.state.newConvPhone = term;
        this.state.newConvPartnerId = false;  // typing clears any prior pick
        if (!term || term.trim().length < 2) {
            this.state.newConvResults = [];
            return;
        }
        const t = term.trim();
        const rows = await this.orm.searchRead(
            "res.partner",
            ["|", "|", ["name", "ilike", t], ["phone", "ilike", t], ["mobile", "ilike", t]],
            ["name", "phone", "mobile"],
            { limit: 8 }
        );
        this.state.newConvResults = rows
            .map((r) => ({ id: r.id, name: r.name, phone: r.mobile || r.phone || "" }))
            .filter((r) => r.phone);  // SMS needs a number
    }

    pickNewConvContact(r) {
        this.state.newConvPhone = r.phone;
        this.state.newConvPartnerId = r.id;
        this.state.newConvResults = [];
    }

    onLineChange(ev) {
        this.state.selectedLineId = parseInt(ev.target.value, 10);
    }

    triggerFilePicker() {
        if (this.fileInputRef.el) {
            this.fileInputRef.el.click();
        }
    }

    async onFileChange(ev) {
        const files = Array.from(ev.target.files || []);
        for (const file of files) {
            if (this.state.attachments.length >= MAX_MEDIA) {
                this.notification.add(`Maximum ${MAX_MEDIA} pièces jointes.`, { type: "warning" });
                break;
            }
            const media = await this._readFile(file);
            this.state.attachments.push(media);
        }
        ev.target.value = "";
    }

    removeAttachment(idx) {
        this.state.attachments.splice(idx, 1);
    }

    get isUnicodeBody() {
        // Un seul caractère hors GSM-7 (œ, ç, ’, …, À/È, emoji) bascule tout
        // le message en UCS-2 → enveloppe de 70 caractères par SMS.
        const body = this.state.composeBody || "";
        for (const c of body) {
            if (!GSM7_BASIC.has(c) && !GSM7_EXT.has(c)) {
                return true;
            }
        }
        return false;
    }

    get segmentCount() {
        const body = this.state.composeBody || "";
        if (!body) {
            return 1;
        }
        if (this.isUnicodeBody) {
            return Math.max(1, Math.ceil([...body].length / SMS_UCS2_LEN));
        }
        let septets = 0;
        for (const c of body) {
            septets += GSM7_EXT.has(c) ? 2 : 1;
        }
        return Math.max(1, Math.ceil(septets / SMS_GSM7_LEN));
    }

    get willSendAsMms() {
        // Miroir de l'escalade serveur : un texte multi-segments part en un
        // seul MMS si la ligne le permet et qu'aucune pièce jointe n'est déjà
        // présente (auquel cas c'est déjà un MMS).
        if (this.state.attachments.length) {
            return false;
        }
        const line = this.state.lines.find(
            (l) => l.id === this.state.selectedLineId
        );
        return this.segmentCount >= 2 && !!(line && line.mms_enabled);
    }

    get canSend() {
        const hasTarget = this.state.activeThread || this.state.newConvPhone.trim();
        const hasContent = this.state.composeBody.trim() || this.state.attachments.length;
        return !this.state.sending && this.state.selectedLineId && hasTarget && hasContent;
    }

    async send() {
        if (!this.canSend) {
            return;
        }
        this.state.sending = true;
        try {
            let threadId = this.state.activeThreadId;
            let dst = this.state.activeThread ? this.state.activeThread.phone : null;
            if (this.state.newConvMode) {
                dst = this.state.newConvPhone.trim();
                threadId = await this.orm.call(MODEL, "start_conversation", [
                    this.state.selectedLineId,
                    dst,
                ]);
                if (this.state.newConvPartnerId) {
                    await this.orm.call(MODEL, "messenger_link_partner", [
                        threadId, this.state.newConvPartnerId,
                    ]);
                }
            }
            const media = this.state.attachments.map((a) => ({
                filename: a.filename,
                content_type: a.content_type,
                data_b64: a.data_b64,
            }));
            await this.orm.call("sms.archive.message", "action_send", [
                this.state.selectedLineId,
                dst,
                this.state.composeBody,
                media.length ? media : false,
            ]);
            this.state.composeBody = "";
            this._clearDraft(threadId);
            this.state.attachments = [];
            this.state.newConvMode = false;
            this.state.newConvResults = [];
            this.state.newConvPartnerId = false;
            await this.loadThreads();
            if (threadId) {
                await this.openThread(threadId);
            }
        } catch (e) {
            this.notification.add("Échec de l'envoi : " + (e.message || e), { type: "danger" });
        } finally {
            this.state.sending = false;
        }
    }

    onComposerKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.send();
        }
    }

    // ── Real-time ──────────────────────────────────────────────

    async _onBusMessage(payload) {
        if (payload.kind === "new") {
            this._beep();
        }
        // Refresh the left list (unread bump + reorder) unless searching archived.
        if (!this.state.archived) {
            await this.loadThreads();
        }
        if (payload.thread_id === this.state.activeThreadId) {
            // Append to the open conversation.
            await this.openThread(this.state.activeThreadId);
        } else if (payload.kind === "new") {
            this._notifyInbound(payload);
        }
    }

    _notifyInbound(payload) {
        const title = payload.contact || "Nouveau SMS";
        this.notification.add(payload.preview || "Nouveau message", {
            title,
            type: "info",
        });
        if (window.Notification && Notification.permission === "granted") {
            try {
                new Notification(title, { body: payload.preview || "" });
            } catch {
                // ignore (some browsers require a service worker)
            }
        }
    }

    _requestNotifyPermission() {
        if (window.Notification && Notification.permission === "default") {
            Notification.requestPermission().catch(() => {});
        }
    }

    // ── Web Push (notifications mobiles fiables) ───────────────────────
    // Enregistre le service worker, s'abonne au push du navigateur et pousse
    // l'abonnement au serveur. Contrairement à window.Notification (premier plan
    // seulement), le push est délivré onglet fermé → notif OS sur le téléphone.
    async _setupPush({ prompt = false } = {}) {
        try {
            if (!this.state.pushSupported || !this._vapidKey) {
                return;
            }
            // Ne pas harceler : si l'autorisation n'est pas encore accordée, on
            // n'abonne qu'à la demande explicite (bouton « Activer »).
            if (Notification.permission === "denied") {
                return;
            }
            if (Notification.permission === "default") {
                if (!prompt) {
                    return;
                }
                const perm = await Notification.requestPermission();
                if (perm !== "granted") {
                    return;
                }
            }
            const reg = await navigator.serviceWorker.register(
                "/bf_sms_archive/push-sw.js",
                { scope: "/bf_sms_archive/" },
            );
            await navigator.serviceWorker.ready;
            let sub = await reg.pushManager.getSubscription();
            if (!sub) {
                sub = await reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: this._urlB64ToUint8(this._vapidKey),
                });
            }
            const raw = sub.toJSON();
            const ok = await this.orm.call(MODEL, "push_subscribe", [], {
                endpoint: raw.endpoint,
                p256dh: raw.keys && raw.keys.p256dh,
                auth: raw.keys && raw.keys.auth,
                ua: (navigator.userAgent || "").slice(0, 120),
            });
            this.state.pushReady = !!ok;
        } catch (e) {
            // Jamais fatal : la SPA reste utilisable sans push.
            console.warn("[bf_sms] configuration Web Push échouée", e);
        }
    }

    // Handler du bouton « Activer les notifications » (déclenche le prompt).
    async enablePush() {
        await this._setupPush({ prompt: true });
        if (this.state.pushReady) {
            this.notification.add("Notifications activées sur cet appareil.", {
                type: "success",
            });
        } else if (window.Notification && Notification.permission === "denied") {
            this.notification.add(
                "Notifications bloquées par le navigateur — à réautoriser dans ses réglages.",
                { type: "warning" },
            );
        }
    }

    // base64url (clé serveur VAPID) → Uint8Array (applicationServerKey).
    _urlB64ToUint8(base64) {
        const padding = "=".repeat((4 - (base64.length % 4)) % 4);
        const b64 = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
        const rawData = atob(b64);
        const out = new Uint8Array(rawData.length);
        for (let i = 0; i < rawData.length; i++) {
            out[i] = rawData.charCodeAt(i);
        }
        return out;
    }

    // ── Helpers ────────────────────────────────────────────────

    _readFile(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const result = reader.result;
                resolve({
                    filename: file.name,
                    content_type: file.type || "application/octet-stream",
                    data_b64: String(result).split(",")[1] || "",
                    dataUrl: result,
                    is_image: (file.type || "").startsWith("image/"),
                });
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    _scrollToBottom() {
        requestAnimationFrame(() => {
            const el = this.messagesRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        });
    }

    initials(name, phone) {
        const src = (name || phone || "?").trim();
        const parts = src.split(/\s+/).filter(Boolean);
        if (parts.length >= 2) {
            return (parts[0][0] + parts[1][0]).toUpperCase();
        }
        return src.slice(0, 2).toUpperCase();
    }

    threadTitle(t) {
        return t.contact_name || t.partner_name || t.phone || "?";
    }

    formatTime(ms) {
        if (!ms) {
            return "";
        }
        return new Date(ms).toLocaleTimeString("fr-CA", {
            hour: "2-digit", minute: "2-digit", timeZone: this.tz || undefined,
        });
    }

    formatDay(ms) {
        if (!ms) {
            return "";
        }
        return new Date(ms).toLocaleDateString("fr-CA", {
            weekday: "short", day: "numeric", month: "short", timeZone: this.tz || undefined,
        });
    }

    formatRelative(ms) {
        if (!ms) {
            return "";
        }
        const d = new Date(ms);
        const today = new Date();
        if (d.toDateString() === today.toDateString()) {
            return this.formatTime(ms);
        }
        return d.toLocaleDateString("fr-CA", {
            day: "numeric", month: "short", timeZone: this.tz || undefined,
        });
    }

    // Date separator: true when this message starts a new calendar day vs the previous.
    showDaySep(idx) {
        if (idx === 0) {
            return true;
        }
        const list = this.visibleMessages;
        const prev = list[idx - 1];
        const cur = list[idx];
        if (!prev || !cur || !prev.date_ms || !cur.date_ms) {
            return false;
        }
        return new Date(prev.date_ms).toDateString() !== new Date(cur.date_ms).toDateString();
    }

    // ── Origine du message (numéro d'envoi) ────────────────────
    //
    // Dès que l'instance compte plus d'un numéro, chaque bulle rappelle
    // discrètement sur quelle ligne l'échange a eu lieu : c'est ce qui évite de
    // répondre depuis le mauvais numéro. Un seul numéro → aucun bruit.

    get multiLine() {
        if (this.state.lines.length > 1) {
            return true;
        }
        const seen = new Set();
        for (const m of this.state.conversation) {
            if (m.line_id) {
                seen.add(m.line_id);
            }
        }
        return seen.size > 1;
    }

    // Un fil partagé peut contenir des messages tenus sur d'autres lignes, que
    // ce lecteur n'a pas le droit de voir. Le dire vaut mieux que de laisser
    // croire à une conversation complète.
    get hiddenHistoryCount() {
        const t = this.state.activeThread;
        return (t && t.hidden_count) || 0;
    }

    msgOrigin(msg) {
        if (!this.multiLine) {
            return "";
        }
        const parts = [];
        if (msg.line_label) {
            parts.push(msg.line_label);
        } else if (msg.line_did) {
            parts.push(this.formatPhone(msg.line_did));
        }
        if (msg.sent_by) {
            parts.push(msg.sent_by);
        }
        return parts.join(" · ");
    }

    // Ligne du dernier message de la conversation ouverte : sert à prévenir
    // quand on s'apprête à répondre depuis un autre numéro que celui employé.
    get lastConvLineId() {
        for (let i = this.state.conversation.length - 1; i >= 0; i--) {
            if (this.state.conversation[i].line_id) {
                return this.state.conversation[i].line_id;
            }
        }
        return false;
    }

    get lineMismatch() {
        const last = this.lastConvLineId;
        if (!last || !this.state.selectedLineId || this.state.newConvMode) {
            return null;
        }
        if (last === this.state.selectedLineId) {
            return null;
        }
        return this.state.lines.find((l) => l.id === last) || null;
    }

    useConversationLine() {
        const line = this.lineMismatch;
        if (line) {
            this.state.selectedLineId = line.id;
        }
    }

    formatPhone(e164) {
        const d = (e164 || "").replace(/\D/g, "");
        const ten = d.length === 11 && d.startsWith("1") ? d.slice(1) : d;
        if (ten.length !== 10) {
            return e164 || "";
        }
        return `${ten.slice(0, 3)} ${ten.slice(3, 6)}-${ten.slice(6)}`;
    }

    deliveryIcon(msg) {
        if (msg.direction !== "out") {
            return "";
        }
        switch (msg.delivery_state) {
            case "sent":
                return "fa-check";
            case "failed":
                return "fa-exclamation-triangle";
            case "queued":
                return "fa-clock-o";
            default:
                return "";
        }
    }
}

registry.category("actions").add("sms_archive_messenger", SmsMessenger);
