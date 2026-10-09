/** @odoo-module **/
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { session } from "@web/session";

function impersonateItem(env) {
    return {
        type: "item",
        id: "bf_impersonate",
        description: _t("See Symbifox as someone else"),
        hide: !session.bf_impersonate_can,
        callback: () => env.services.action.doAction("bf_impersonate.action_bf_impersonate_wizard"),
        sequence: 55,
    };
}

registry.category("user_menuitems").add("bf_impersonate", impersonateItem);

// Tous les onglets d'un navigateur partagent la même session. Après un
// changement d'usager (début ou fin d'incarnation), un onglet resté ouvert
// afficherait encore l'ancien compte tout en agissant sous le nouveau. Chaque
// client web annonce l'usager qu'il sert ; un onglet qui en sert un autre se
// recharge. (L'uid se lit sur ``user`` : Odoo 18 le retire de la session.)
const channel =
    window.top === window && typeof window.BroadcastChannel === "function"
        ? new window.BroadcastChannel("bf_impersonate")
        : null;

/** Annoncer aux autres onglets l'usager que la session sert désormais. */
export function announceUser(uid) {
    if (channel && uid) {
        channel.postMessage({ uid });
    }
}

let switching = false;

/**
 * Cet onglet sert l'ancien compte : le couvrir tout de suite (plus aucun clic
 * sous le mauvais usager), puis le recharger après un délai au hasard. Deux
 * onglets qui démarrent dans la même seconde se marchent dessus dans le service
 * des visites guidées d'Odoo (web_tour), dont l'état vit dans le localStorage
 * partagé : l'un lit le nom de la visite pendant que l'autre en efface la
 * configuration, et plante au démarrage (mesuré le 2026-10-08).
 */
function reloadStaleTab() {
    if (switching) {
        return;
    }
    switching = true;
    const veil = document.createElement("div");
    veil.className = "o_bf_impersonate_switching";
    veil.textContent = _t("Changing account, reloading…");
    document.body.appendChild(veil);
    browser.setTimeout(() => browser.location.reload(), 2500 + Math.floor(Math.random() * 1500));
}

if (channel && user.userId) {
    channel.onmessage = (event) => {
        const uid = event.data && event.data.uid;
        if (uid && uid !== user.userId) {
            reloadStaleTab();
        }
    };
    announceUser(user.userId);
}

// Fin de l'assistant : prévenir les autres onglets AVANT de naviguer. Attendre
// que la nouvelle page s'annonce laissait 2 à 3 secondes où un onglet périmé
// interrogeait le serveur sous la personne avec le contexte de l'incarnateur
// (mesuré le 2026-10-08).
registry.category("actions").add("bf_impersonate_switched", (env, action) => {
    announceUser(action.params && action.params.uid);
    browser.location.href = "/odoo";
});
