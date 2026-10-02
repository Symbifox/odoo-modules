/** @odoo-module */
// Thème d'un rapport : la palette catégorielle (ordre fixe), la rampe séquentielle de la carte
// thermique et les deux pôles de la cascade. Le thème « couleurs de la société » commence par
// les couleurs de marque, ajustées en OKLab pour que les marques restent lisibles sur le fond :
// la teinte est gardée, la clarté ramenée dans la bande lisible ; un gris de marque ne
// distingue rien et reste au texte. Les couleurs par défaut trop proches d'une couleur de
// marque sont écartées (ΔE OKLab < 15, le seuil de la vision normale).

export const DEFAULT_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
export const OTHER = "#98a1b0";
export const TOTAL = "#5b6577"; // neutre volontaire : un total n'est pas une catégorie
const MIN_DISTANCE = 15;
const CHROMA_FLOOR = 0.05;
const BAND = [0.45, 0.75];

function toLinear(c) {
    c /= 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}
function fromLinear(c) {
    const v = c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
    return Math.round(Math.max(0, Math.min(1, v)) * 255);
}
export function hexToOklab(hex) {
    const n = parseInt(hex.slice(1), 16);
    const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(toLinear);
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    return [
        0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
    ];
}
export function oklabToHex([L, a, b]) {
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
    const rgb = [
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
    ].map(fromLinear);
    return "#" + rgb.map((c) => c.toString(16).padStart(2, "0")).join("");
}
export function deltaE(h1, h2) {
    const [a, b] = [hexToOklab(h1), hexToOklab(h2)];
    return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) * 100;
}

function brandColors(hexes) {
    const out = [];
    for (const hex of hexes || []) {
        if (!/^#[0-9a-f]{6}$/i.test(hex)) {
            continue;
        }
        const [L, a, b] = hexToOklab(hex.toLowerCase());
        if (Math.hypot(a, b) < CHROMA_FLOOR) {
            continue;
        }
        const snapped = oklabToHex([Math.min(BAND[1], Math.max(BAND[0], L)), a, b]);
        if (out.every((c) => deltaE(c, snapped) >= MIN_DISTANCE)) {
            out.push(snapped);
        }
    }
    return out;
}

function isWarm(hex) {
    // Rouges, orangés et jaunes : angle de teinte OKLab entre -10° et 120°.
    const [, a, b] = hexToOklab(hex);
    const h = (Math.atan2(b, a) * 180) / Math.PI;
    return h > -10 && h < 120;
}

export function buildTheme(theme) {
    const name = (theme && theme.name) || "default";
    let colors = DEFAULT_PALETTE.slice();
    let brand = [];
    if (name === "company") {
        brand = brandColors(theme.company_colors);
        colors = [...brand, ...DEFAULT_PALETTE.filter((d) => brand.every((c) => deltaE(c, d) >= MIN_DISTANCE))].slice(0, 8);
    }
    // Hausse et baisse : deux pôles, sans dire « bien » ou « mal » (une hausse des coûts n'est
    // pas une bonne nouvelle). La baisse est chaude ; la hausse est la première couleur du thème
    // qui ne l'est pas (une marque rouge ne peint pas les hausses en rouge).
    const down = "#eb6834";
    const up = colors.find((c) => !isWarm(c) && deltaE(c, down) >= MIN_DISTANCE) || "#2a78d6";
    return { name, colors, brand, other: OTHER, total: TOTAL, up, down, seq: colors[0] };
}

export function themeStyle(theme) {
    // Les variables CSS du rapport suivent le thème : courbes de tuile, barres de données…
    return theme.colors.map((c, i) => `--bfr-c${i + 1}: ${c}`).join("; ") +
        `; --bfr-up: ${theme.up}; --bfr-down: ${theme.down}; --bfr-total: ${theme.total}`;
}

export function rampColor(theme, t) {
    // Rampe séquentielle d'une seule teinte, du très clair au foncé (clarté monotone).
    const [L, a, b] = hexToOklab(theme.seq);
    const k = Math.max(0, Math.min(1, t));
    const light = [0.97, a * 0.08, b * 0.08];
    const dark = [Math.min(L, 0.42), a, b];
    return oklabToHex(light.map((v, i) => v + (dark[i] - v) * k));
}

function luminance(hex) {
    const n = parseInt(hex.slice(1), 16);
    const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(toLinear);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function inkOn(hex) {
    // Le texte porte une encre de texte, jamais la couleur de la série : noir ou blanc, celui qui
    // contraste le plus. Entre noir et blanc, le meilleur des deux dépasse toujours 4,5:1.
    const L = luminance(hex);
    return 1.05 / (L + 0.05) >= (L + 0.05) / 0.05 ? "#ffffff" : "#000000";
}
