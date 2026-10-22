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
import { user } from "@web/core/user";
import { prettyToolName } from "@bf_claude_chat/js/claude_stream";

// Un répertoire par langue, écrit dans cette langue plutôt que
// traduit mot à mot (on ne « pense » pas pareil d'une langue à l'autre), et
// choisi d'après la CONVERSATION : sous une interface en français, une
// conversation en anglais affichait ses formules en français. Les 13
// formules d'avant se répétaient vite ; un tour de 97 s en tire plusieurs.
const FOX_PHRASES = {
    fr: [
        "Gen flaire la piste",
        "Gen suit la trace",
        "Gen furète dans les dossiers",
        "Gen dresse les oreilles",
        "Gen fouille le terrier",
        "Gen ruse avec la question",
        "Gen s'approche de la réponse à pas de renard",
        "Gen réfléchit en renard",
        "Gen fouine",
        "Gen fait le guet",
        "Gen traque l'information",
        "Gen affûte son flair",
        "Gen, en pleine réflexion vulpine",
        "Gen a le museau au vent",
        "Gen remonte la piste",
        "Gen gratte sous la surface",
        "Gen fait le tour du terrier",
        "Gen renifle les indices",
        "Gen trotte d'une fiche à l'autre",
        "Gen ne lâche pas la piste",
        "Gen médite, la queue en panache",
        "Gen se faufile dans les détails",
        "Gen recoupe les traces",
        "Gen déterre le fil de l'histoire",
        "Gen rumine ça au fond du terrier",
        "Gen a le flair en éveil",
        "Gen jongle avec les indices",
        "Gen tend l'oreille aux détails",
        "Gen pèse ça, l'oreille dressée",
        "Gen fait ses devoirs de renard",
        "Gen guette le bon indice",
    ],
    en: [
        "Gen is sniffing out the trail",
        "Gen is on the scent",
        "Gen is prowling through the records",
        "Gen is pricking up its ears",
        "Gen is digging through the den",
        "Gen is outfoxing the question",
        "Gen is creeping up on the answer",
        "Gen is thinking like a fox",
        "Gen is nosing around",
        "Gen is keeping watch",
        "Gen is tracking it down",
        "Gen is sharpening its nose",
        "Gen, deep in vulpine thought",
        "Gen has its nose to the ground",
        "Gen is following a hunch",
        "Gen is circling back to check",
        "Gen is snuffling through the files",
        "Gen is piecing the trail together",
        "Gen is padding through the paperwork",
        "Gen is chasing down a lead",
        "Gen is turning over every leaf",
        "Gen is mulling it over in the den",
        "Gen is reading the tracks",
        "Gen is scouting ahead",
        "Gen is following its nose",
        "Gen is burrowing into the details",
        "Gen is ears-up and listening",
        "Gen is weighing the evidence, tail high",
        "Gen is on the hunt for facts",
        "Gen is putting two and two together, foxily",
    ],
};

/** Une formule du renard, par son rang, dans la langue de la conversation. */
export function foxPhrase(index, lang) {
    const liste = FOX_PHRASES[lang === "fr" ? "fr" : "en"];
    return liste[Math.floor(Math.abs(index || 0)) % liste.length];
}

/** Le nombre de formules d'une langue : le tirage du départ s'y cale. */
export function foxCount(lang) {
    return FOX_PHRASES[lang === "fr" ? "fr" : "en"].length;
}

// Des mots outils propres à chaque langue (« on », « a », « no » sont
// écartés : ils existent dans les deux).
const MOTS = {
    fr: new Set(("le la les des une est pas pour que qui svp avec dans sur merci " +
        "faire peux ça oui je tu nous du au et mais aussi cette ce voir mettre " +
        "rajouter préparer valider ta tes ton si donc selon").split(" ")),
    en: new Set(("the and is are please can you what why how with for this that " +
        "thanks yes do make we to of it be have should could would").split(" ")),
};

/** "fr", "en", ou null quand le texte ne tranche pas (« go », « OK », « 1 »). */
export function guessLang(text) {
    const mots = String(text || "").toLowerCase().match(/[a-zà-öø-ÿœ']+/g) || [];
    let fr = 0;
    let en = 0;
    for (const mot of mots) {
        if (MOTS.fr.has(mot) || /[àâçéèêëîïôûùœ]/.test(mot)) {
            fr++;
        } else if (MOTS.en.has(mot)) {
            en++;
        }
    }
    if (fr >= 2 && fr > en) return "fr";
    if (en >= 2 && en > fr) return "en";
    return null;
}

/**
 * La langue de la conversation : celle des derniers messages de la personne
 * qui tranchent, puis celle de Gen, puis celle de l'interface. Une consigne
 * interne (« Mets-moi en contexte… », toujours en français) ne compte pas.
 */
export function conversationLang(messages, interfaceLang = user.lang) {
    const lus = (messages || []).filter((m) => m && !m.internal && m.content);
    for (const role of ["user", "assistant"]) {
        const recents = lus.filter((m) => m.role === role).slice(-6).reverse();
        for (const m of recents) {
            const lang = guessLang(m.content);
            if (lang) return lang;
        }
    }
    return String(interfaceLang || "").toLowerCase().startsWith("fr") ? "fr" : "en";
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
                assistant.tools.push({
                    raw: data.name, label: toolLabel(data.name), detail: "", sub: [],
                });
                assistant.phase = "tool";
            }
            break;
        case "tool_detail": {
            const step = [...assistant.tools].reverse().find(
                (s) => s.raw === data.name && !s.detail);
            if (step && data.detail) step.detail = data.detail;
            break;
        }
        case "sub": {
            // Le travail d'un sous-agent, relayé par le pont. Il se
            // range SOUS la pastille de la délégation en cours : une
            // délégation de quatre minutes n'affichait qu'« Agent », muet.
            const hote = [...assistant.tools].reverse().find(
                (s) => s.raw === "Agent" || s.raw === "Task");
            if (!hote) break;
            if (!hote.sub) hote.sub = [];
            const ligne = data.kind === "tool"
                ? (data.detail || toolLabel(data.name || ""))
                : (data.text || "");
            if (ligne) hote.sub.push(ligne);
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
        return foxPhrase(msg.foxIndex || 0, msg.foxLang);
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
        if (!this.props.live || !step) return "";
        // Une délégation en cours parle par son sous-agent : montrer sa
        // dernière étape plutôt que le mot « Agent », qui ne dit rien.
        const sub = step.sub && step.sub.length ? step.sub[step.sub.length - 1] : "";
        if (sub) return _t("Sub-agent: %s", sub);
        return step.detail || step.label;
    }
}
