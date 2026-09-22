/** @odoo-module **/

/**
 * Ce que le chronomètre et les notes par sujet partagent, par ordre du jour.
 *
 * Les deux vivent à deux endroits du formulaire (le chronomètre au-dessus des
 * onglets, les notes dans l'onglet « Notes en direct ») : ce sont deux widgets,
 * pas un. Le chronomètre y pose son dernier état ; il y demande aussi un
 * « saut » : amener la personne aux notes d'un sujet, curseur en place.
 */

import { reactive } from "@odoo/owl";

export const magasin = reactive({});

export function entree(resId) {
    if (!magasin[resId]) {
        magasin[resId] = { payload: null, saut: 0, sautVers: null };
    }
    return magasin[resId];
}

/**
 * Poser le curseur à la fin des notes, là où l'on ajoute : après le contexte
 * d'origine pré-rempli, jamais dedans quand un paragraphe le suit.
 *
 * 🔴 Le centrage attend l'éditeur : mesuré au banc, l'éditeur réagit au
 * changement de sélection en défilant au plus court, ce qui laissait la cible
 * collée au bas de l'écran.
 */
export async function poserCurseur(editable, bloc = null) {
    const cible0 = bloc || editable.lastElementChild || editable;
    const cible =
        cible0.classList && cible0.classList.contains("bf-agenda-original")
            ? cible0.lastElementChild || cible0
            : cible0;
    editable.focus({ preventScroll: true });
    const doc = editable.ownerDocument;
    const plage = doc.createRange();
    plage.selectNodeContents(cible);
    plage.collapse(!cible.textContent.trim());
    const selection = doc.getSelection();
    selection.removeAllRanges();
    selection.addRange(plage);
    await new Promise((resolve) => setTimeout(resolve, 60));
    cible.scrollIntoView({ block: "center" });
}
