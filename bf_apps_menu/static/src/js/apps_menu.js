import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";
import { fuzzyLookup } from "@web/core/utils/search";
import { useEffect, useRef, useState } from "@odoo/owl";

/**
 * Menu des applications : une grille cherchable au lieu de la liste brute.
 *
 * Odoo 18 rend le menu des applications comme une suite de `DropdownItem`
 * purement textuels (`web.NavBar.AppsMenu`). Passé une vingtaine
 * d'applications, la liste dépasse l'écran, sans icône et sans moyen de viser
 * vite.
 *
 * On garde le `Dropdown` et les `DropdownItem` — donc la fermeture au choix, la
 * navigation au clavier, les `href` réels et la classe `o_app` sur laquelle
 * s'appuient les tours et les tests d'Odoo — et on ne remplace que la
 * présentation.
 */
patch(NavBar.prototype, {
    setup() {
        super.setup();
        this.appsSearch = useState({ query: "" });
        this.appsSearchRef = useRef("appsMenuSearch");

        // Le curseur dans le champ dès l'ouverture : on clique, on tape.
        //
        // ⚠️ Ni `useAutofocus`, ni `setTimeout(0)`. Le `Dropdown` pose son
        // propre focus par `useNavigation`, et celui-ci passe par
        // `throttleForAnimation` — donc par un `requestAnimationFrame`. Tout
        // focus posé plus tôt (montage, microtâche, `setTimeout(0)`) se fait
        // reprendre à la frame suivante, en silence.
        //
        // Le vrai correctif est dans le gabarit : le champ est enveloppé dans
        // un `.o-navigable`, ce qui en fait le premier élément navigable du
        // menu ; avec `shouldFocusChildInput` (vrai par défaut), la navigation
        // d'Odoo vise alors l'`<input>` qu'il contient et le focus atterrit au
        // bon endroit d'elle-même.
        //
        // Ce double `requestAnimationFrame` reste en filet : il s'exécute
        // APRÈS la frame où le dropdown pose son focus, donc il corrige le cas
        // où la navigation viserait autre chose.
        useEffect(
            (el) => {
                if (!el) {
                    return;
                }
                this.focusAppsMenuSearch();
            },
            () => [this.appsSearchRef.el]
        );
    },

    /**
     * Poser le curseur dans le champ, sans dépendre du `t-ref`.
     *
     * Le contenu du dropdown est rendu dans `.o-overlay-container`, hors du DOM
     * de la barre : une résolution de référence à travers ce portail est le
     * maillon fragile de la chaîne. Une requête DOM, elle, ne peut pas échouer
     * silencieusement.
     *
     * Les deux frames sont nécessaires : la navigation du dropdown pose son
     * focus via `throttleForAnimation`, donc à la frame suivante. On passe
     * après elle.
     */
    focusAppsMenuSearch() {
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                const el = document.querySelector(
                    ".o_apps_menu_panel .o_apps_menu_search_input"
                );
                if (el && document.activeElement !== el) {
                    el.focus();
                    el.select();
                }
            });
        });
    },

    /** Le clic sur l'icône des applications : on ouvre, et on est prêt à taper. */
    onAppsMenuToggle() {
        this.focusAppsMenuSearch();
    },

    /** Les applications visibles, filtrées par la recherche en cours. */
    get appsMenuApps() {
        const apps = this.menuService.getApps();
        const query = this.appsSearch.query.trim();
        if (!query) {
            return apps;
        }
        // `fuzzyLookup` est ce qu'utilise la palette de commandes d'Odoo :
        // tolérant aux lettres manquantes et déjà classé par pertinence.
        return fuzzyLookup(query, apps, (app) => app.name);
    },

    /**
     * Entrée ouvre la première correspondance — c'est tout l'intérêt de taper
     * pour chercher. Échap vide le champ avant que le dropdown ne se ferme.
     */
    onAppsMenuSearchKeydown(ev) {
        if (ev.key === "Enter") {
            const first = this.appsMenuApps[0];
            if (first) {
                ev.preventDefault();
                ev.stopPropagation();
                this.onNavBarDropdownItemSelection(first);
            }
        } else if (ev.key === "Escape" && this.appsSearch.query) {
            ev.stopPropagation();
            this.appsSearch.query = "";
        }
    },

    /**
     * Vider la recherche dès qu'une tuile est choisie : à la réouverture, le
     * menu est complet et le champ est net, donc on clique et on tape sans
     * avoir à effacer d'abord.
     */
    onNavBarDropdownItemSelection(menu) {
        this.appsSearch.query = "";
        return super.onNavBarDropdownItemSelection(menu);
    },
});
