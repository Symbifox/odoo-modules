/** @odoo-module **/

// Ce que Gen montre pendant qu'il travaille.
//
// Un tour dure 97 s en médiane au bureau, et dans 311 tours sur 428 Gen ne
// disait rien avant d'avoir fini : l'écran affichait « 212 tokens », un
// compteur qui repart à 50 à chaque bloc de réflexion, et une rangée de
// pastilles « Bash ». On garde les mêmes événements du pont, on change ce
// qu'ils disent : une ligne d'état qui nomme l'étape en cours, un chrono, et
// les étapes repliées en un compteur qu'on déplie.
//
// ⚠️ La réflexion arrive VIDE dans le flux du CLI (seule une estimation de
// jetons sort) : quand aucun outil ne tourne, on ne peut pas dire à quoi Gen
// pense. La ligne tire alors une formule du renard, choix retenu.

import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { prettyToolName } from "@bf_claude_chat/js/claude_stream";

const FOX_PHRASES = 13;

/** Une formule du renard, par son rang. `_t` à l'appel : jamais au chargement. */
function foxPhrase(index) {
    switch (index % FOX_PHRASES) {
        case 0: return _t("Gen is sniffing out the trail");
        case 1: return _t("Gen is on the scent");
        case 2: return _t("Gen is prowling through the records");
        case 3: return _t("Gen is pricking up its ears");
        case 4: return _t("Gen is digging through the den");
        case 5: return _t("Gen is outfoxing the question");
        case 6: return _t("Gen is creeping up on the answer");
        case 7: return _t("Gen is thinking like a fox");
        case 8: return _t("Gen is nosing around");
        case 9: return _t("Gen is keeping watch");
        case 10: return _t("Gen is tracking it down");
        case 11: return _t("Gen is sharpening its nose");
        default: return _t("Gen, deep in vulpine thought");
    }
}

/**
 * Le libellé d'un outil quand sa commande n'a pas (encore) de description.
 *
 * La description d'une commande Bash n'arrive qu'à la fin de son écriture,
 * et 31 appels sur 4 245 n'en portent aucune : ce libellé tient la ligne en
 * attendant, ou pour de bon.
 */
export function toolLabel(rawName) {
    const name = (rawName || "").replace(/^mcp__[a-z0-9-]+__/i, "");
    switch (name) {
        case "Bash": return _t("Running a check");
        case "WebSearch": return _t("Searching the web");
        case "WebFetch": return _t("Reading a web page");
        case "Read":
        case "Glob":
        case "Grep": return _t("Reading files");
        case "Write":
        case "Edit": return _t("Writing a file");
        case "ToolSearch": return _t("Picking the right tools");
        case "Skill": return _t("Following a procedure");
        case "odoo_get_task": return _t("Reading the task");
        case "odoo_list_project_tasks": return _t("Looking through the tasks");
        case "odoo_add_task_comment": return _t("Writing a note on the task");
        case "email_read_message": return _t("Reading an email");
        case "email_create_draft":
        case "odoo_schedule_chatter_email": return _t("Drafting an email");
    }
    const verb = (s) => new RegExp(`^(${s})_`).test(name.replace(/^(odoo|email|nc|pb|zoho|sync)_/, ""));
    if (name.startsWith("email_")) return _t("Searching the mailbox");
    if (name.startsWith("nc_")) {
        return verb("write|create|delete|nc_create")
            ? _t("Writing to Nextcloud") : _t("Looking in Nextcloud");
    }
    if (name.startsWith("pb_")) return _t("Preparing a secure link");
    if (name.startsWith("odoo_") || name.startsWith("zoho_")) {
        if (verb("get|read|export")) return _t("Reading in Odoo");
        if (verb("list|search")) return _t("Searching Odoo");
        return _t("Writing in Odoo");
    }
    return prettyToolName(rawName);
}

/**
 * Tient à jour l'étape en cours d'un message d'assistant, à partir des
 * événements du pont. Appelé par le panneau et par la vue plein écran avant
 * leur propre traitement, pour qu'ils disent la même chose.
 */
export function trackWait(assistant, event, data) {
    switch (event) {
        case "thinking": {
            const tokens = data.tokens || 0;
            // Le compteur repart à zéro à chaque bloc de réflexion : c'est le
            // signe d'une nouvelle pensée, donc d'une nouvelle formule.
            if (assistant.phase !== "thinking" || tokens < (assistant.thinkingTokens || 0)) {
                assistant.foxIndex = (assistant.foxIndex || 0) + 1 + Math.floor(Math.random() * 3);
            }
            assistant.thinkingTokens = tokens;
            assistant.phase = "thinking";
            break;
        }
        case "tool":
            if (data.name) {
                assistant.tools.push({ raw: data.name, label: toolLabel(data.name), detail: "" });
                assistant.phase = "tool";
            }
            break;
        case "tool_detail": {
            const step = [...assistant.tools].reverse().find(
                (s) => s.raw === data.name && !s.detail);
            if (step && data.detail) step.detail = data.detail;
            break;
        }
        case "text":
            assistant.phase = "text";
            break;
    }
}

/** Temps écoulé, lisible d'un coup d'œil : « 42 s », « 1 min 05 s ». */
export function formatElapsed(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    if (total < 60) return `${total} s`;
    const min = Math.floor(total / 60);
    return `${min} min ${String(total % 60).padStart(2, "0")} s`;
}

/** La ligne d'état sous la bulle, tant que le tour n'est pas fini. */
export class GenWaitLine extends Component {
    static template = "bf_claude_chat.GenWaitLine";
    static props = { msg: Object };

    setup() {
        this.clock = useState({ now: Date.now() });
        onMounted(() => {
            this.timer = setInterval(() => { this.clock.now = Date.now(); }, 1000);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    get label() {
        const msg = this.props.msg;
        if (msg.phase === "tool" && msg.tools.length) {
            const step = msg.tools[msg.tools.length - 1];
            return step.detail || step.label;
        }
        if (msg.phase === "text") return _t("Gen is writing");
        return foxPhrase(msg.foxIndex || 0);
    }

    get elapsed() {
        return formatElapsed(this.clock.now - (this.props.msg.startedAt || this.clock.now));
    }
}

/**
 * Les étapes du tour, repliées en un compteur qu'on déplie.
 *
 * Pendant que Gen réfléchit, le résumé porte aussi la dernière étape : sa
 * description n'arrive qu'au moment où la commande part, et la réflexion
 * reprend 0,3 s plus tard (médiane de Bash), si bien que la ligne d'état ne
 * la montrerait qu'un instant. Pendant qu'un outil tourne, la ligne d'état la
 * dit déjà : le parent passe alors `live` à faux, pour ne pas la répéter.
 */
export class GenSteps extends Component {
    static template = "bf_claude_chat.GenSteps";
    static props = { steps: Array, live: { type: Boolean, optional: true } };

    get summary() {
        const n = this.props.steps.length;
        return n === 1 ? _t("1 step") : _t("%s steps", n);
    }

    get latest() {
        const step = this.props.steps[this.props.steps.length - 1];
        return this.props.live && step ? (step.detail || step.label) : "";
    }
}
