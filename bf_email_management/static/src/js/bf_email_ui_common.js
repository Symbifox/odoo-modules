/** @odoo-module **/
/*
 * Socle partagé par les deux actions clientes courriel : le navigateur IMAP
 * (`bf_email_browser`) et la boîte de réception Odoo (`bf_email_inbox`).
 *
 * Ce fichier existe parce que les deux doivent se ressembler à l'écran. Tant
 * que les préférences, le format de date, le rendu de l'expéditeur et le
 * gabarit d'aperçu vivaient en double, ils divergeaient à chaque retouche.
 * Les préférences partagent aussi leur clé de stockage : régler la densité
 * d'un côté la règle des deux côtés. Tâche #24628.
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
    // Position de l'aperçu par rapport à la liste. "bottom" est la mise en
    // page d'origine (liste en haut, corps en bas) ; "right" la met côte à
    // côte, ce que réclame un écran large — la liste n'a pas besoin de
    // 1 500 px pour montrer une date et un sujet.
    paneLayout: "bottom",       // "bottom" | "right"
    // Part de la fenêtre laissée à l'APERÇU, en pourcentage, par disposition.
    // Deux valeurs distinctes : la hauteur qu'on veut donner à un corps de
    // courriel n'est pas la largeur qu'on lui donnerait.
    paneSize: { bottom: 50, right: 50 },
    // Colonnes visibles de la liste, par écran. Les deux jeux diffèrent
    // réellement — le navigateur IMAP n'a pas de notion de « dossier » — donc
    // deux clés plutôt qu'une seule qu'il faudrait filtrer des deux côtés.
    // « Sujet » n'y figure pas : c'est la colonne qu'on ne peut pas retirer,
    // une liste de courriels sans objet n'est plus une liste de courriels.
    columnsInbox: {
        date: true, correspondent: true, folder: true,
        category: false, preview: false, state: true,
    },
    columnsBrowser: { date: true, sender: true, state: true },
    // Ruban d'actions de l'aperçu replié en une ligne d'icônes. L'en-tête
    // (objet, De, À, date, dossier, pièces jointes) reste visible dans les
    // deux états : c'est le contexte, pas une option. Tâche #24976.
    ribbonCollapsed: false,
};

// Clés dont la valeur par défaut est un objet : elles se fusionnent en
// profondeur au chargement. Un `{...defaut, ...stocke}` naïf remplacerait
// l'objet entier, donc une préférence enregistrée par une version antérieure
// du schéma ferait disparaître toute clé ajoutée depuis — ici, une colonne
// neuve serait absente au lieu d'être visible.
const NESTED_KEYS = ["paneSize", "columnsInbox", "columnsBrowser"];

// Bornes du séparateur. En deçà, le panneau perdant devient inutilisable ;
// au-delà, c'est l'autre qui l'est.
export const PANE_MIN = 25;
export const PANE_MAX = 75;

function freshDefaults() {
    const out = { ...DEFAULT_SETTINGS };
    for (const key of NESTED_KEYS) {
        out[key] = { ...DEFAULT_SETTINGS[key] };
    }
    return out;
}

export function loadSettings() {
    try {
        const raw = window.localStorage.getItem(SETTINGS_KEY);
        if (!raw) return freshDefaults();
        const parsed = JSON.parse(raw);
        const merged = { ...DEFAULT_SETTINGS, ...parsed };
        for (const key of NESTED_KEYS) {
            merged[key] = { ...DEFAULT_SETTINGS[key], ...(parsed[key] || {}) };
        }
        return merged;
    } catch (e) {
        return freshDefaults();
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
/**
 * Sélection d'une plage au shift+clic, comme un gestionnaire de fichiers.
 *
 * ``selectedMap`` est l'objet réactif { id: true } des deux écrans ; ``ids``
 * l'ordre RÉELLEMENT affiché (donc filtré par la recherche, pas la page
 * brute). Retourne false si l'ancre ou la cible ne sont plus à l'écran —
 * l'appelant retombe alors sur la bascule d'une seule case.
 */
export function selectRange(selectedMap, ids, anchorId, targetId) {
    const from = ids.indexOf(anchorId);
    const to = ids.indexOf(targetId);
    if (from === -1 || to === -1) {
        return false;
    }
    const lo = Math.min(from, to);
    const hi = Math.max(from, to);
    for (let i = lo; i <= hi; i++) {
        selectedMap[ids[i]] = true;
    }
    clearNativeTextSelection();
    return true;
}

/**
 * Un shift+clic surligne au passage tout le texte parcouru depuis le clic
 * précédent : la liste devient bleue et illisible. Le surlignage n'a aucun
 * sens ici, on l'annule.
 */
export function clearNativeTextSelection() {
    try {
        const sel = window.getSelection();
        if (sel && !sel.isCollapsed) {
            sel.removeAllRanges();
        }
    } catch (e) {
        // Rien à rattraper : l'API manque ou refuse, le surlignage reste.
    }
}

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

/**
 * Styles du couple liste / aperçu selon la disposition choisie.
 *
 * Un seul endroit décide de la mise en page des deux actions clientes :
 * tant que chacune portait ses propres `flex: 1 1 50%` en dur, changer l'une
 * ne changeait pas l'autre, et c'est exactement ce qui a fait diverger les
 * deux écrans la dernière fois. Tâche #24649.
 */
export function paneStyles(settings) {
    const layout = settings.paneLayout === "right" ? "right" : "bottom";
    const stored = (settings.paneSize || {})[layout];
    const size = typeof stored === "number"
        ? stored
        : DEFAULT_SETTINGS.paneSize[layout];
    if (layout === "right") {
        return {
            layout,
            wrapClass: "flex-row",
            listStyle: "flex: 1 1 auto; min-width: 0; min-height: 0;",
            paneStyle: `flex: 0 0 ${size}%; min-width: 320px; min-height: 0; overflow: hidden;`,
            splitterStyle:
                "flex: 0 0 5px; cursor: col-resize; background: #dee2e6; z-index: 2;",
        };
    }
    return {
        layout,
        wrapClass: "flex-column",
        listStyle: "flex: 1 1 auto; min-height: 0; min-width: 0;",
        paneStyle: `flex: 0 0 ${size}%; min-height: 160px; min-width: 0; overflow: hidden;`,
        splitterStyle:
            "flex: 0 0 5px; cursor: row-resize; background: #dee2e6; z-index: 2;",
    };
}

/**
 * Glisser du séparateur. ``component`` doit porter ``state.settings`` (dans
 * un ``useState``) et ``state.dragging``.
 *
 * Les préférences ne sont écrites qu'au relâchement : ``localStorage`` est
 * synchrone, et l'écrire à chaque pixel fait saccader le geste.
 */
export function startPaneDrag(ev, component) {
    const wrap = ev.currentTarget && ev.currentTarget.parentElement;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const layout = component.state.settings.paneLayout === "right"
        ? "right" : "bottom";
    component.state.dragging = true;
    const onMove = (e) => {
        const pct = layout === "right"
            ? ((rect.right - e.clientX) / rect.width) * 100
            : ((rect.bottom - e.clientY) / rect.height) * 100;
        component.state.settings.paneSize[layout] = Math.min(
            PANE_MAX, Math.max(PANE_MIN, Math.round(pct))
        );
    };
    const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        component.state.dragging = false;
        persistSettings(component.state.settings);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    ev.preventDefault();
}

/**
 * Largeurs de colonnes de la liste. Compact ou aperçu à droite rétrécissent
 * la liste : garder les largeurs du mode confortable y écraserait la colonne
 * Sujet, qui est justement celle qu'on lit.
 */
export function columnWidths(settings) {
    const narrow = settings.density === "compact"
        || settings.paneLayout === "right";
    return narrow
        ? { check: 28, date: 92, correspondent: 170, folder: 130, state: 62, act: 40 }
        : { check: 32, date: 130, correspondent: 250, folder: 200, state: 72, act: 60 };
}
