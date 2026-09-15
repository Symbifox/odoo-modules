/** @odoo-module **/

// Suivre un tour de Gen jusqu'à sa réponse enregistrée, quoi qu'il arrive au
// flux.
//
// 🔴 Relevé du 2026-09-14 : près d'une question sur trois posée au bureau
// restait sans réponse enregistrée. Une lecture de flux qui levait après le
// premier mot finissait sur « (connexion interrompue) », et une coupure avant
// le premier mot renvoyait la question par /claude-chat/send, donc un tour
// entier refait. Le serveur possède maintenant le tour (voir
// controllers/turns.py) : l'écran n'est qu'un spectateur qui se rattache.
//
// Partagé par le panneau et la vue plein écran, pour qu'ils se comportent de
// la même façon.

import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { streamChat } from "@bf_claude_chat/js/claude_stream";
import { toolLabel, trackWait } from "@bf_claude_chat/js/gen_wait";

// Attente avant de se rattacher, puis plafond de patience d'un écran.
const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];
const GIVE_UP_MS = 100 * 60 * 1000;

/** Un jeton tiré au départ : une question renvoyée retrouve son tour. */
export function newClientToken() {
    if (window.crypto && window.crypto.randomUUID) {
        return window.crypto.randomUUID().replace(/-/g, "");
    }
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

/** Les champs d'une bulle d'assistant pendant qu'un tour s'écrit. */
export function streamingFields() {
    return {
        content: "",
        prefix: "",
        streaming: true,
        tools: [],
        thinkingTokens: 0,
        phase: "start",
        foxIndex: Math.floor(Math.random() * 13),
        startedAt: Date.now(),
        interrupted: false,
        reconnecting: false,
        notice: "",
        turnId: null,
        finalized: false,
    };
}

function sleep(ms, signal) {
    return new Promise((resolve) => {
        const timer = setTimeout(resolve, ms);
        if (signal) {
            signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
        }
    });
}

function applyEvent(host, assistant, event, data, labels) {
    trackWait(assistant, event, data);
    switch (event) {
        case "session":
            if (data.odoo_session_id) host.onSessionId(data.odoo_session_id);
            break;
        case "gen_turn":
            if (data.turn_id) assistant.turnId = data.turn_id;
            break;
        case "busy":
            assistant.notice = _t("Gen is still working on your previous request. Here is where it stands.");
            if (host.onBusy) host.onBusy(assistant);
            break;
        case "notice":
            assistant.notice = data.text || "";
            break;
        case "snapshot":
            // Relecture du tour depuis le début : ce que les reprises
            // précédentes ont écrit, puis le tour en cours rejoué.
            assistant.prefix = data.text || "";
            assistant.content = assistant.prefix;
            assistant.tools = (data.tools || []).map((t) => ({
                raw: t.name, label: toolLabel(t.name), detail: t.detail || "",
            }));
            assistant.reconnecting = false;
            break;
        case "resume": {
            const done = (assistant.content || "").trimEnd();
            assistant.prefix = done ? `${done}\n\n` : "";
            assistant.content = assistant.prefix;
            assistant.notice = data.max
                ? _t("Gen picks up where it left off (%s of %s)", data.attempt, data.max)
                : _t("Gen picks up where it left off");
            break;
        }
        case "text":
            assistant.content += data.delta || "";
            assistant.reconnecting = false;
            host.scrollToBottom();
            break;
        case "tool":
            host.scrollToBottom();
            break;
        case "done":
            if (data.response) assistant.content = (assistant.prefix || "") + data.response;
            if (labels && data.usage) {
                assistant.usageLabel = labels.usageLabel(data.usage);
                assistant.usageTitle = labels.usageTitle(data.usage);
            }
            break;
        case "error":
            if (data.reason === "disabled" && assistant.turnId) {
                // Gen désactivée pendant un tour déjà parti : on s'arrête là,
                // sans renvoyer la question par le chemin tamponné.
                assistant.streaming = false;
                assistant.interrupted = true;
                assistant.finalized = true;
            } else if (data.reason === "disabled") {
                assistant.disabled = true;
            } else if (data.reason === "not_found") {
                assistant.notFound = true;
            } else if (!assistant.turnId && data.response) {
                // Refusé avant tout tour (message vide, trop de requêtes) :
                // rien à suivre, la raison est la réponse.
                assistant.content = data.response;
                assistant.streaming = false;
                assistant.finalized = true;
            }
            break;
        case "final":
            // Ce que le serveur a enregistré : fait foi.
            if (typeof data.content === "string" && data.content) assistant.content = data.content;
            assistant.interrupted = data.state === "error";
            assistant.endReason = data.end_reason || "";
            if (data.message_id) assistant.id = data.message_id;
            if (labels && data.usage && data.usage.duration_ms) {
                assistant.usageLabel = labels.usageLabel(data.usage);
                assistant.usageTitle = labels.usageTitle(data.usage);
            }
            assistant.streaming = false;
            assistant.reconnecting = false;
            assistant.finalized = true;
            break;
        case "saved":
            if (data.message_id) assistant.id = data.message_id;
            break;
    }
}

/**
 * Suivre un tour jusqu'à son événement « final », en se rattachant après
 * toute coupure. Rend "final", "stopped", "disabled" ou "lost".
 *
 * @param {Object} host        {onSessionId, scrollToBottom, onBusy?}
 * @param {Object} assistant   la bulle (proxy réactif)
 * @param {Object} opts        {start?: body de /claude-chat/stream, turnId?, signal, labels?}
 */
export async function followTurn(host, assistant, { start = null, turnId = null, signal, labels }) {
    const until = Date.now() + GIVE_UP_MS;
    let request = start
        ? { url: "/claude-chat/stream", body: start }
        : { url: "/claude-chat/attach", body: { turn_id: turnId } };
    if (turnId) assistant.turnId = turnId;
    let tries = 0;
    let resent = false;
    for (;;) {
        let broken = false;
        try {
            await streamChat({
                url: request.url,
                body: request.body,
                signal,
                onEvent: (event, data) => applyEvent(host, assistant, event, data, labels),
            });
        } catch {
            if (signal && signal.aborted) return "stopped";
            broken = true;
        }
        if (signal && signal.aborted) return "stopped";
        if (assistant.finalized) return "final";
        if (assistant.disabled) return "disabled";
        if (assistant.notFound) {
            assistant.notFound = false;
            if (start && !assistant.turnId && !resent) {
                // Le serveur n'a jamais reçu la question : la renvoyer, sous le
                // même jeton, ne peut pas lancer un second tour.
                resent = true;
                request = { url: "/claude-chat/stream", body: start };
                continue;
            }
            assistant.streaming = false;
            assistant.interrupted = true;
            return "lost";
        }
        if (Date.now() > until) {
            assistant.streaming = false;
            assistant.interrupted = true;
            return "lost";
        }
        // Pas de réponse enregistrée : revenir au tour, sans rien relancer.
        request = assistant.turnId
            ? { url: "/claude-chat/attach", body: { turn_id: assistant.turnId } }
            : { url: "/claude-chat/attach", body: { client_token: start && start.client_token } };
        if (broken) assistant.reconnecting = true;
        await sleep(BACKOFF_MS[Math.min(tries, BACKOFF_MS.length - 1)], signal);
        tries += 1;
    }
}

/** Le bouton Arrêter : quitter le flux ne suffit plus à arrêter le tour. */
export async function stopTurn(assistant) {
    if (!assistant || !assistant.turnId) return;
    try {
        await rpc("/claude-chat/stop", { turn_id: assistant.turnId });
    } catch {
        // Le flux est coupé de toute façon ; le serveur finira le tour.
    }
}

/** Une bulle chargée « en cours » (page rechargée pendant un tour). */
export function pendingToStreaming(msg) {
    const content = msg.content === "…" ? "" : (msg.content || "");
    Object.assign(msg, streamingFields(), { content, prefix: "", turnId: msg.id });
    return msg;
}
