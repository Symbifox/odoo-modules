/** @odoo-module **/
/**
 * Service de la calculatrice : ce que le panneau doit savoir de la page
 * autour de lui.
 *
 *  - la fiche ouverte (vue formulaire seulement), cible par défaut du
 *    versement au chatter : même lecture que le bloc-notes, qui a appris que
 *    `context.active_id` traîne des identifiants périmés ;
 *  - le dernier champ numérique où la personne a cliqué, pour « Insérer dans
 *    le champ » : ouvrir le panneau fait perdre le focus, il faut donc s'en
 *    souvenir avant.
 */
import { registry } from "@web/core/registry";
import { localization } from "@web/core/l10n/localization";

const NUMERIC_FIELDS =
    ".o_field_float, .o_field_monetary, .o_field_integer, .o_field_float_time, " +
    ".o_field_percentage, .o_field_float_factor";

/** Valeur brute (« 4500.5 ») → texte saisissable dans un champ, à la façon
 * de la langue, sans séparateur de milliers. */
export function rawToInput(raw, loc = localization) {
    let text = String(raw);
    if (/e/i.test(text)) {
        text = Number(text).toFixed(10).replace(/\.?0+$/, "");
    }
    return text.replace(".", loc.decimalPoint || ".");
}

export const bfCalculatorService = {
    dependencies: ["action"],
    start(env, { action }) {
        let lastNumericInput = null;

        document.addEventListener(
            "focusin",
            (ev) => {
                const el = ev.target;
                if (
                    el instanceof HTMLInputElement &&
                    !el.readOnly &&
                    !el.disabled &&
                    el.closest(NUMERIC_FIELDS) &&
                    !el.closest(".o_bf_calc_popover")
                ) {
                    lastNumericInput = el;
                }
            },
            true
        );

        const insertTarget = () =>
            lastNumericInput && lastNumericInput.isConnected ? lastNumericInput : null;

        return {
            currentRecord() {
                try {
                    const controller = action.currentController;
                    if (!controller) {
                        return {};
                    }
                    const view = controller.view?.type || controller.props?.type;
                    if (view && view !== "form") {
                        return {};
                    }
                    // 🔴 Le pager et l'enregistrement d'une fiche neuve mettent à jour
                    // `currentState`, jamais `props` : lire `props` seul proposait la
                    // fiche d'AVANT (relecture adverse, 2026-09-22).
                    const resModel = controller.props?.resModel;
                    const resId = controller.currentState?.resId ?? controller.props?.resId;
                    if (resModel && typeof resId === "number" && resId) {
                        return { resModel, resId };
                    }
                } catch {
                    // la page n'a pas de contrôleur lisible : pas de fiche courante
                }
                return {};
            },
            canInsert() {
                return Boolean(insertTarget());
            },
            insertLabel() {
                const el = insertTarget();
                const field = el?.closest("[name]");
                const label = field
                    ? document.querySelector(`label[for="${el.id}"]`)?.textContent?.trim()
                    : "";
                return label || field?.getAttribute("name") || "";
            },
            insert(raw) {
                const el = insertTarget();
                if (!el) {
                    return false;
                }
                el.focus();
                el.value = rawToInput(raw);
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
                return true;
            },
        };
    },
};

registry.category("services").add("bf_calculator", bfCalculatorService);
