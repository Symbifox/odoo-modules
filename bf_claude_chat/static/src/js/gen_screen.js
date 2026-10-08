/** @odoo-module **/

// Ce que le panneau latéral et le plein écran font de la même
// façon. Le panneau avait pris du retard sur le plein écran (pas d'archivage
// depuis la liste, pas de date, pas de question pendant un tour, pas de lu en
// fin de tour) parce que chaque écran portait sa copie du code.
//
// 🔴 La roue qui tourne se perdait : un écran ne suivait qu'UN tour, tenu par
// des drapeaux globaux. Ouvrir une autre conversation pendant un tour, puis
// revenir, laissait une bulle figée ; le bouton Arrêter de l'autre
// conversation arrêtait le premier tour. Les tours sont maintenant suivis PAR
// CONVERSATION (`this._turns`), et l'écran ne fait que choisir lequel il montre.

import { toRaw } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import { followTurn, pendingToStreaming, stopTurn } from "@bf_claude_chat/js/gen_turn";

//: Attente de la frappe avant de chercher.
const SEARCH_DEBOUNCE_MS = 300;

/** « 12,3 k », « 1,2 M » : les jetons en chiffres lisibles. */
export function compactTokens(n) {
    const v = Number(n) || 0;
    if (v >= 1e6) return `${(v / 1e6).toLocaleString("fr-CA", { maximumFractionDigits: 1 })} M`;
    if (v >= 1e3) return `${(v / 1e3).toLocaleString("fr-CA", { maximumFractionDigits: 1 })} k`;
    return v.toLocaleString("fr-CA");
}

/** Une date Odoo (UTC, « AAAA-MM-JJ HH:MM:SS ») ou ISO, en Date. */
export function parseServerDate(value) {
    if (!value) return null;
    const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value.replace(" ", "T")}Z`;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Étiquette de consommation d'un tour, dans le vocabulaire commun au Cockpit
 * et à Comms.
 *
 * ⚠️ On affiche les jetons NEUFS (entrée + mise en cache + sortie), PAS le
 * total : le total additionne le contexte relu, qui vaut ~93 % du volume et
 * n'est pas du travail neuf. C'est ce qui faisait lire « 50 000 jetons » pour
 * un bonjour — vrai, mais incompréhensible. Le contexte relu part dans
 * l'infobulle, où il informe sans écraser.
 */
export function usageLabel(u) {
    if (!u) return "";
    const parts = [];
    // Calculé ici à défaut : un pont plus ancien n'envoie pas `net_tokens`,
    // et l'étiquette doit rester juste plutôt que de disparaître.
    const neufs = u.net_tokens
        || (u.input_tokens || 0) + (u.cache_write_tokens || 0) + (u.output_tokens || 0);
    if (neufs) {
        parts.push(neufs >= 1000
            ? `${(neufs / 1000).toFixed(1)} k jetons`
            : `${neufs} jetons`);
    }
    if (u.cost_usd) parts.push(`${u.cost_usd.toFixed(3)} $`);
    if (u.duration_ms) parts.push(`${(u.duration_ms / 1000).toFixed(1)} s`);
    return parts.join(" · ");
}

export function usageTitle(u) {
    if (!u) return "";
    const relu = u.cache_read_tokens || 0;
    const lignes = [
        `Jetons neufs : ${((u.net_tokens
            || (u.input_tokens || 0) + (u.cache_write_tokens || 0)
               + (u.output_tokens || 0))).toLocaleString("fr-CA")}`,
        `Contexte relu : ${relu.toLocaleString("fr-CA")} (dix fois moins cher)`,
        `Total traité : ${((u.total_tokens
            || (u.input_tokens || 0) + (u.cache_write_tokens || 0)
               + (u.output_tokens || 0) + relu)).toLocaleString("fr-CA")}`,
        "Coût équivalent API — forfait Max, rien n'est facturé au jeton.",
    ];
    return lignes.join("\n");
}

export const screenMixin = {
    _usageLabels() {
        return { usageLabel, usageTitle };
    },

    screenState() {
        return {
            query: "",
            archivedView: false,     // liste des archivées (hors fiche)
            activeArchived: false,   // la conversation affichée est archivée
            plan: null,              // {windows, measured_at, error} ou null
            totals: null,            // consommation de la conversation affichée
            refreshingUsage: false,
            apiKeyBilling: false,    // le locataire paie au jeton : pas de forfait
            busyIds: {},             // conversations dont CET écran suit un tour
        };
    },

    // ── Les tours, par conversation ─────────────────────────────────────

    _turnMap() {
        if (!this._turns) this._turns = new Map();
        return this._turns;
    },

    /** La clé d'une conversation : son id, ou « new » avant que le serveur
     *  ne l'ait créée. */
    _turnKey(sessionId) {
        return sessionId > 0 ? sessionId : "new";
    },

    /** L'écran reflète le tour de la conversation AFFICHÉE, et lui seul.
     *
     *  Le chemin sans flux (`/claude-chat/send`) n'a pas de suiveur : il tient
     *  `_bufferedBusy`, sans quoi changer de conversation rendait la saisie et
     *  permettait un second `/send` en parallèle sur la même conversation. */
    _syncThinking() {
        const entry = this._turnMap().get(this._turnKey(this.state.activeSessionId));
        this.state.isThinking = Boolean(entry) || Boolean(this._bufferedBusy);
        this.state.streamingActive = Boolean(entry);
        this._streamAbort = entry ? entry.controller : null;
        this._streamAssistant = entry ? entry.assistant : null;
        const busy = {};
        for (const key of this._turnMap().keys()) {
            if (key !== "new") busy[key] = true;
        }
        this.state.busyIds = busy;
    },

    /** Vider l'écran de la conversation affichée : tout ce qui la décrit part
     *  avec elle (le total et l'avis « archivée » d'une
     *  fiche passaient à la suivante). */
    _clearConversation(activeSessionId = null) {
        this.state.activeSessionId = activeSessionId;
        this.state.messages = [];
        this.state.closure = null;
        this.state.totals = null;
        this.state.activeArchived = false;
        this._syncThinking();
    },

    /** La personne écrit dans la conversation affichée : une archivée est
     *  rouverte par le serveur, l'avis n'a plus lieu d'être. */
    _onWrite() {
        this.state.activeArchived = false;
    },

    /** Gen travaille-t-il dans cette conversation (vu d'ici ou du serveur) ? */
    isSessionBusy(session) {
        return Boolean(session.busy || this.state.busyIds[session.id]);
    },

    async _followTurn(assistant, { start = null, turnId = null, onBusy = null }) {
        const turns = this._turnMap();
        const sessionId = start ? (start.session_id || null) : this.state.activeSessionId;
        const entry = {
            assistant, controller: new AbortController(), key: this._turnKey(sessionId),
        };
        turns.set(entry.key, entry);
        this._syncThinking();
        this.scrollToBottom();
        const shown = () => this.state.messages.includes(assistant);
        let outcome;
        try {
            outcome = await followTurn({
                onSessionId: (id) => {
                    // La conversation neuve vient de naître côté serveur.
                    if (entry.key === "new") {
                        turns.delete("new");
                        entry.key = id;
                        turns.set(id, entry);
                    }
                    // Seulement si la personne la regarde encore : sinon un tour
                    // parti d'ici volait l'écran à la conversation ouverte depuis.
                    if (this.state.activeSessionId === -1 && shown()) {
                        this.state.activeSessionId = id;
                    }
                    this._syncThinking();
                },
                scrollToBottom: () => { if (shown()) this.scrollToBottom(); },
                onBusy,
                onQueuedSeen: (texte) => this._onQueuedSeen(texte),
                onQueuedLost: (texte) => this._onQueuedLost(texte),
            }, assistant, {
                start, turnId, signal: entry.controller.signal, labels: this._usageLabels(),
            });
        } finally {
            if (outcome === "stopped") assistant.interrupted = true;
            assistant.streaming = false;
            assistant.reconnecting = false;
            if (turns.get(entry.key) === entry) turns.delete(entry.key);
            this._syncThinking();
        }
        if (shown()) {
            if (assistant.closure) this.applyClosure(assistant.closure);
            this._addTurnToTotals(assistant.usage);
            if (outcome === "final") await this._markSeen();
        }
        return outcome;
    },

    /** Une conversation chargée : rattacher le tour que l'écran suit déjà, ou
     *  reprendre celui que le serveur dit en cours (page rechargée, tour lancé
     *  ailleurs : plein écran, téléphone, « Envoyer vers Gen », autre onglet). */
    async _resumePending(sessionId = this.state.activeSessionId) {
        // Une réponse arrivée après qu'on a ouvert une autre conversation ne
        // rattache rien (le tour de X passait sous Y).
        if (sessionId !== this.state.activeSessionId) return;
        const messages = this.state.messages;
        const last = messages[messages.length - 1];
        const entry = this._turnMap().get(this._turnKey(sessionId));
        if (entry) {
            const live = toRaw(entry.assistant);
            const index = messages.findIndex((m) => m.role === "assistant"
                && m.id && (m.id === live.turnId || m.id === live.id));
            let list = messages.map(toRaw);
            if (index >= 0) {
                list[index] = live;
            } else if (!list.includes(live)) {
                // La bulle « en cours » relue du serveur et la bulle suivie sont
                // le même tour : n'en garder qu'une.
                list = list.filter((m) => !(m.role === "assistant" && m.state === "pending"));
                list.push(live);
            }
            this.state.messages = list;
            this._syncThinking();
            return;
        }
        this._syncThinking();
        if (!last || last.role !== "assistant") return;
        if (last.state === "error") last.interrupted = true;
        if (last.state !== "pending") return;
        pendingToStreaming(last);
        await this._followTurn(last, { turnId: last.id });
        await this._refreshList();
    },

    onStop() {
        const entry = this._turnMap().get(this._turnKey(this.state.activeSessionId));
        if (!entry) return;
        stopTurn(entry.assistant);
        entry.controller.abort();
    },

    /** Le topo automatique d'une page quittée s'arrête avec elle. */
    _internalBriefInFlight() {
        for (const entry of this._turnMap().values()) {
            const a = entry.assistant;
            if (a && a.internalBrief && a.turnId && !a.finalized) return a;
        }
        return null;
    },

    // ── Une question pendant un tour ───────────────────────────

    _queue() {
        if (!this._enFile) this._enFile = new Map();
        return this._enFile;
    },

    /** Glisser une question dans le tour en cours. Gen la lit à sa prochaine
     *  respiration ; s'il a fini avant, le pont le dit et on la rend. */
    async _sayInTurn(message, textarea) {
        const sessionId = this.state.activeSessionId === -1 ? null : this.state.activeSessionId;
        if (!sessionId) return;
        const bulle = {
            id: `q-${Date.now()}`, role: "user", content: message,
            queued: true, create_date: new Date().toISOString(),
        };
        this.state.messages.push(bulle);
        textarea.value = "";
        if (this.autoResize) this.autoResize(textarea);
        this.scrollToBottom();
        let reponse;
        try {
            reponse = await rpc("/claude-chat/say", { session_id: sessionId, message });
        } catch {
            reponse = { status: "over" };
        }
        if (!reponse || reponse.status !== "queued") {
            this._rendreQuestion(bulle, message);
            this.notification.add(
                _t("Gen had already finished. Send your question again."),
                { type: "info" });
            return;
        }
        this._queue().set(message, bulle);
    },

    _rendreQuestion(bulle, message) {
        // Rendue à la saisie seulement si sa conversation est à l'écran : sinon
        // elle tomberait dans une autre.
        const visible = this.state.messages.some((m) => m.id === bulle.id);
        this.state.messages = this.state.messages.filter((m) => m.id !== bulle.id);
        this._queue().delete(message);
        const input = this.inputRef.el;
        if (visible && input && !input.value) {
            input.value = message;
            if (this.autoResize) this.autoResize(input);
        }
    },

    _onQueuedSeen(texte) {
        const bulle = this._queue().get(texte);
        if (bulle) {
            bulle.queued = false;      // Gen l'a lue : bulle normale
            this._queue().delete(texte);
        }
    },

    _onQueuedLost(texte) {
        const bulle = this._queue().get(texte);
        if (bulle) this._rendreQuestion(bulle, texte);
        this.notification.add(
            _t("Gen finished before reading your question. Here it is again."),
            { type: "info" });
    },

    /** Un tour suivi à l'écran jusqu'au bout vaut lecture. */
    async _markSeen() {
        const sessionId = this.state.activeSessionId;
        if (!(sessionId > 0)) return;
        try {
            await rpc("/claude-chat/seen", { session_id: sessionId });
        } catch {
            // La conversation restera en gras au téléphone, sans plus.
        }
    },

    // ── La liste : recherche et archives ────────────────────────────────

    /** Les paramètres de liste communs aux deux écrans. */
    _listParams() {
        const params = {};
        if (this.state.toFollow) params.to_follow = true;
        if (this.state.archivedView) params.archived = true;
        if (this.state.query.trim()) params.query = this.state.query.trim();
        return params;
    },

    onSearchInput(ev) {
        this.state.query = ev.target.value;
        clearTimeout(this._searchDebounce);
        this._searchDebounce = setTimeout(() => this._refreshList(), SEARCH_DEBOUNCE_MS);
    },

    onClearSearch() {
        this.state.query = "";
        this._refreshList();
    },

    async onToggleArchivedView() {
        this.state.archivedView = !this.state.archivedView;
        if (this.state.archivedView) this.state.toFollow = false;
        await this._refreshList();
    },

    archivedTitle(session) {
        const d = parseServerDate(session.archive_date);
        return d
            ? _t("Archived on %s. Writing here reopens it.", d.toLocaleDateString())
            : _t("Archived. Writing here reopens it.");
    },

    formatDate(dateStr) {
        const d = parseServerDate(dateStr);
        if (!d) return "";
        const diff = Date.now() - d.getTime();
        if (diff < 86400000) {
            return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
        if (diff < 604800000) {
            return d.toLocaleDateString([], { weekday: "short" });
        }
        return d.toLocaleDateString([], { month: "short", day: "numeric" });
    },

    /** Ce que `/messages` dit de la conversation, au-delà de ses messages. */
    applyConversation(result) {
        this.state.activeArchived = result.active === false;
        this.state.totals = result.totals || null;
        this.state.messages = (result.messages || []).map((m) => {
            const row = m.state === "error" ? { ...m, interrupted: true } : { ...m };
            // L'étiquette de consommation survit au rechargement.
            if (row.role === "assistant" && row.duration_ms) {
                row.usageLabel = this._usageLabels().usageLabel(row);
                row.usageTitle = this._usageLabels().usageTitle(row);
            }
            return row;
        });
    },

    // ── Compteurs : forfait et conversation ─────────────────────────────

    async loadPlan(fresh = false) {
        try {
            const result = await rpc("/claude-chat/usage", {
                fresh,
                session_id: this.state.activeSessionId > 0 ? this.state.activeSessionId : false,
            });
            this.state.plan = result && result.plan ? result : null;
            this.state.apiKeyBilling = Boolean(result && result.api_key);
            if (result && result.totals && result.session_id === this.state.activeSessionId) {
                this.state.totals = result.totals;
            }
        } catch {
            // Sans relevé, pas de compteur : rien à dire de plus.
        }
    },

    async onRefreshUsage() {
        if (this.state.refreshingUsage) return;
        this.state.refreshingUsage = true;
        try {
            await this.loadPlan(true);
        } finally {
            this.state.refreshingUsage = false;
        }
    },

    _addTurnToTotals(usage) {
        if (!usage || !this.state.totals) return;
        const t = this.state.totals;
        const neufs = usage.net_tokens
            || (usage.input_tokens || 0) + (usage.cache_write_tokens || 0) + (usage.output_tokens || 0);
        t.net_tokens = (t.net_tokens || 0) + neufs;
        t.total_tokens = (t.total_tokens || 0) + (usage.total_tokens
            || neufs + (usage.cache_read_tokens || 0));
        t.duration_ms = (t.duration_ms || 0) + (usage.duration_ms || 0);
        t.cost_usd = (t.cost_usd || 0) + (usage.cost_usd || 0);
        t.turns = (t.turns || 0) + 1;
    },

    /** Les fenêtres du forfait, prêtes à afficher. */
    planWindows() {
        const plan = this.state.plan;
        if (!plan || !plan.windows) return [];
        const noms = { five_hour: _t("Session"), seven_day: _t("Week") };
        return plan.windows.map((w) => {
            const remise = parseServerDate(w.resets_at);
            const pct = Math.round(w.utilization);
            return {
                key: w.key,
                label: `${noms[w.key] || w.key} ${pct} %`,
                level: pct >= 95 ? "danger" : pct >= 80 ? "warning" : "",
                title: remise
                    ? _t("Resets %s", remise.toLocaleString([], {
                        weekday: "short", hour: "2-digit", minute: "2-digit" }))
                    : "",
            };
        });
    },

    planTitle() {
        const d = this.state.plan && parseServerDate(this.state.plan.measured_at);
        return d
            ? _t("Claude plan, read at %s. Click ↻ for a fresh reading.",
                 d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))
            : _t("Claude plan. Click ↻ for a fresh reading.");
    },

    totalsLabel() {
        const t = this.state.totals;
        if (!t || !t.turns) return "";
        // ⚠️ `_t` hors du gabarit de chaîne : l'extracteur ne lit pas un
        // `_t(...)` écrit dans un `${...}`, et le mot restait anglais.
        const mot = _t("tokens");
        const parts = [`${compactTokens(t.net_tokens)} ${mot}`];
        if (t.cost_usd) {
            parts.push(`${t.cost_usd.toLocaleString("fr-CA", {
                minimumFractionDigits: 2, maximumFractionDigits: 2 })} $`);
        }
        return parts.join(" · ");
    },

    totalsTitle() {
        const t = this.state.totals;
        if (!t || !t.turns) return "";
        return [
            _t("This conversation: %s turns", t.turns),
            _t("New tokens: %s", (t.net_tokens || 0).toLocaleString("fr-CA")),
            _t("Processed, re-read context included: %s",
               (t.total_tokens || 0).toLocaleString("fr-CA")),
            this.state.apiKeyBilling
                ? _t("Cost at public API rates.")
                : _t("API-equivalent cost: Max plan, nothing is billed per token."),
        ].join("\n");
    },
};
