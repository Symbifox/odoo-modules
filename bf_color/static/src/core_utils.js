/** Color helpers shared by the views (same rules as models/color_utils.py). */

const HEX = /^#([0-9a-f]{6})$/i;

export function isHex(value) {
    return typeof value === "string" && HEX.test(value);
}

function toRgb(hex) {
    const digits = hex.slice(1);
    return [0, 2, 4].map((i) => parseInt(digits.slice(i, i + 2), 16));
}

function toHex(rgb) {
    return "#" + rgb.map((c) => c.toString(16).padStart(2, "0")).join("").toUpperCase();
}

/** Blend with white; Odoo's agenda paints its own colors at 55 % white. */
export function mixWhite(hex, whiteShare) {
    return toHex(toRgb(hex).map((c) => Math.round(255 * whiteShare + c * (1 - whiteShare))));
}

export function rgbString(hex) {
    return toRgb(hex).join(", ");
}

function luminance(hex) {
    const [r, g, b] = toRgb(hex).map((c) => {
        c = c / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Black or white, whichever contrasts more (WCAG). */
export function textColor(hex) {
    const l = luminance(hex);
    return (l + 0.05) / 0.05 >= 1.05 / (l + 0.05) ? "#000000" : "#FFFFFF";
}
