/** @odoo-module **/
/*
 * Socle partagé par les deux actions clientes courriel : le navigateur IMAP
 * (`bf_email_browser`) et la boîte de réception Odoo (`bf_email_inbox`).
 *
 * Ce fichier existe parce que les deux doivent se ressembler à l'écran. Tant
 * que les préférences, le format de date, le rendu de l'expéditeur et le
 * gabarit d'aperçu vivaient en double, ils divergeaient à chaque retouche.
 * Les préférences partagent aussi leur clé de stockage : régler la densité
 * d'un côté la règle des deux côtés.
 */

const FRENCH_WEEKDAY = ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."];
const FRENCH_MONTH = [
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juill.", "août", "sept.", "oct.", "nov.", "déc.",
];

export const SETTINGS_KEY = "bf_email_browser_settings_v1";

export const DEFAULT_SETTINGS = {
    dateFormat: "relative",     // "relative" | "absolute"
    senderDisplay: "name",      // "name" | "email" | "both"
    pageSize: 100,              // int
    density: "comfortable",     // "comfortable" | "compact"
    boldUnread: true,
};

export function loadSettings() {
    try {
        const raw = window.localStorage.getItem(SETTINGS_KEY);
        if (!raw) return { ...DEFAULT_SETTINGS };
        const parsed = JSON.parse(raw);
        return { ...DEFAULT_SETTINGS, ...parsed };
    } catch (e) {
        return { ...DEFAULT_SETTINGS };
    }
}

export function persistSettings(settings) {
    try {
        window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    } catch (e) {
        // Quota / navigation privée — les préférences ne survivront pas à la
        // session, ce qui n'empêche rien de fonctionner.
    }
}

/**
 * Date à la Apple Mail, ou "YYYY-MM-DD HH:mm" si l'usager a choisi le format
 * absolu. ``iso`` est au format serveur (UTC sans suffixe).
 */
export function formatRelativeDate(iso, settings = DEFAULT_SETTINGS) {
    if (!iso) return "";
    const d = new Date(iso.replace(" ", "T") + (iso.endsWith("Z") ? "" : "Z"));
    if (Number.isNaN(d.getTime())) return iso;
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    if (settings.dateFormat === "absolute") {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, "0");
        const dd = String(d.getDate()).padStart(2, "0");
        return `${y}-${m}-${dd} ${hm}`;
    }
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    const isYesterday = d.toDateString() === yesterday.toDateString();
    if (sameDay) return `aujourd'hui ${hm}`;
    if (isYesterday) return `hier ${hm}`;
    const ageDays = (now - d) / (24 * 3600 * 1000);
    if (ageDays >= 0 && ageDays < 7) {
        return `${FRENCH_WEEKDAY[d.getDay()]} ${hm}`;
    }
    if (d.getFullYear() === now.getFullYear()) {
        return `${d.getDate()} ${FRENCH_MONTH[d.getMonth()]}`;
    }
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${dd}`;
}

/**
 * Contenu de la colonne « Expéditeur », selon la préférence senderDisplay.
 * ``name`` est le nom déjà résolu (affichage du contact ou nom d'en-tête),
 * ``from`` l'en-tête brut, qui reste dans l'infobulle de la ligne.
 */
export function senderCell(name, from, settings = DEFAULT_SETTINGS) {
    const cleanName = (name || "").trim();
    const cleanFrom = (from || "").trim();
    if (settings.senderDisplay === "email") {
        const match = cleanFrom.match(/<([^>]+)>/);
        return match ? match[1] : cleanFrom;
    }
    if (settings.senderDisplay === "both") {
        const match = cleanFrom.match(/<([^>]+)>/);
        const addr = match ? match[1] : "";
        return cleanName && addr && cleanName !== addr
            ? `${cleanName} <${addr}>`
            : (cleanName || cleanFrom);
    }
    return cleanName || cleanFrom;
}

/**
 * Enveloppe le corps HTML du courriel dans un document minimal, rendu dans
 * une iframe `sandbox="allow-same-origin"` (donc sans scripts). Le CSS des
 * infolettres ne peut pas déborder sur l'interface d'Odoo.
 */
export function buildPreviewSrcdoc(bodyHtml) {
    if (!bodyHtml) {
        return "<!doctype html><html><body></body></html>";
    }
    return `<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<base target="_blank"/>
<style>
  html, body { margin: 0; padding: 12px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 14px; line-height: 1.5; color: #1f2329; word-wrap: break-word; }
  img { max-width: 100%; height: auto; }
  table { max-width: 100%; }
  pre { white-space: pre-wrap; word-break: break-word; }
  blockquote { border-left: 3px solid #d0d7de; margin: 0 0 0 8px; padding: 0 0 0 12px; color: #57606a; }
  a { color: #29ABE1; }
</style>
</head>
<body>${bodyHtml}</body>
</html>`;
}

/**
 * Aplatit une liste parent/enfant en respectant les nœuds repliés.
 * ``items`` porte ``key`` et ``parent`` (clé du parent, ou faux).
 */
export function flattenTree(items, expanded = {}) {
    const children = {};
    const roots = [];
    for (const item of items) {
        if (item.parent) {
            (children[item.parent] = children[item.parent] || []).push(item);
        } else {
            roots.push(item);
        }
    }
    const flat = [];
    const walk = (nodes, depth) => {
        for (const n of nodes) {
            const kids = children[n.key] || [];
            flat.push({ ...n, depth, has_children: kids.length > 0 });
            if (kids.length && expanded[n.key]) {
                walk(kids, depth + 1);
            }
        }
    };
    walk(roots, 0);
    return flat;
}
