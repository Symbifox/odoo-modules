/** @odoo-module **/
import { registry } from "@web/core/registry";

// Cliquer-pour-appeler : intercepte tout clic sur un lien « tel: » du backend
// (widget phone en lecture, bouton « Appeler » du formulaire, listes, kanbans)
// et compose via le softphone au lieu d'ouvrir le gestionnaire tel: de l'OS.
// Écouteur en phase de CAPTURE pour passer avant la navigation par défaut.
// Dégradation propre : si le téléphone n'est pas dispo, le lien tel: agit normalement.

export const softphoneClickToCall = {
    dependencies: ["bf_softphone"],
    start(env, { bf_softphone }) {
        document.addEventListener("click", (ev) => {
            if (!bf_softphone.state.available) return;
            const a = ev.target.closest && ev.target.closest('a[href^="tel:"]');
            if (!a) return;
            const number = decodeURIComponent(a.getAttribute("href").slice(4)).trim();
            if (!number) return;
            ev.preventDefault();
            ev.stopPropagation();
            bf_softphone.callNumber(number);
        }, true);
    },
};

registry.category("services").add("bf_softphone_click_to_call", softphoneClickToCall);
