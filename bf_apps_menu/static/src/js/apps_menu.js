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

        // 🔴 La cinquième porte, celle qu'aucun geste ne franchit : tourner le
        // téléphone. Le passage petit ↔ grand écran détruit le tiroir sans
        // passer par aucune des trois méthodes qui vident la requête, et Odoo,
        // lui, garde `isAppMenuSidebarOpened` vrai. Au retour, le tiroir se
        // rouvrait donc tout seul sur une liste filtrée que personne n'avait
        // demandée (mesuré : 2 applications sur 74).
        useEffect(
            () => {
                this.appsSearch.query = "";
            },
            () => [this.ui.isSmall]
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
        // Rien à ouvrir sans requête : le champ a déjà le curseur, et sur un
        // téléphone la touche « aller » est sous le pouce — un effleurement
        // ouvrait la première application de la liste.
        // Et pendant une composition (claviers CJK, validation d'une
        // prédiction sur Gboard), Entrée appartient au clavier, pas à nous.
        if (ev.isComposing || ev.keyCode === 229) {
            return;
        }
        if (ev.key === "Enter") {
            const first = this.appsSearch.query.trim() ? this.appsMenuApps[0] : null;
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

    // ── Le tiroir mobile ────────────────────────────────────────────────────
    //
    // Sur téléphone, Odoo ne monte pas le panneau ci-dessus : il rend son
    // tiroir (`AppsMenu.Sidebar`), qui a deux listes — les menus de
    // l'application ouverte, et « Toutes les applications ». Le gabarit pose un
    // champ au-dessus de la seconde et filtre sa boucle par `appsMenuApps` ; le
    // reste est ici.
    //
    // La requête est la même que celle du bureau : une seule des deux surfaces
    // est montée à la fois (`ui.isSmall` tranche), donc rien ne se mélange. Ce
    // qui compte, c'est qu'elle ne SURVIVE pas au tiroir : une requête laissée
    // derrière rouvrirait une liste filtrée, qu'on lirait comme une liste
    // amputée. D'où le vidage sur chacune des trois portes.

    /**
     * Poser le curseur dans le champ du tiroir.
     *
     * Même parade que sur le bureau, et pour la même raison : le tiroir est
     * porté dans le `body` (`t-portal`), donc on ne traverse pas ce portail
     * avec un `t-ref` — une requête DOM ne peut pas échouer en silence. Les
     * deux frames passent après le rendu d'Owl, qui n'est pas synchrone.
     *
     * ⚠️ Android lève le clavier ; iOS ne le lève que sur un geste direct, et
     * un focus posé après un rendu n'en est plus un. Sur iPhone, le curseur est
     * donc dans le champ et le clavier monte au premier toucher.
     */
    focusAppsMenuSidebarSearch() {
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                const el = document.querySelector(
                    ".o_app_menu_sidebar .o_apps_menu_search_input"
                );
                if (el && document.activeElement !== el) {
                    el.focus();
                }
            });
        });
    },

    /**
     * « Toutes les applications » : champ net, et le curseur dedans.
     *
     * Le curseur ne se pose QUE sur ce geste-ci, jamais à la simple ouverture
     * du tiroir : là, on vient souvent chercher un menu de l'application
     * ouverte, et un clavier mangerait la moitié de l'écran pour rien.
     *
     * On lit l'état AVANT d'appeler le cœur, parce que ce bouton est une
     * bascule : il sert aussi à revenir aux menus de l'application.
     */
    onAllAppsBtnClick() {
        this.appsSearch.query = "";
        const ouvreLesApps = !this.state.isAllAppsMenuOpened;
        const res = super.onAllAppsBtnClick(...arguments);
        if (ouvreLesApps) {
            this.focusAppsMenuSidebarSearch();
        }
        return res;
    },

    /** Le tiroir s'ouvre ou se referme par l'icône : la recherche repart à neuf. */
    _openAppMenuSidebar() {
        this.appsSearch.query = "";
        return super._openAppMenuSidebar(...arguments);
    },

    /** Fermé par le X, par le fond, ou par un balayage : même remise à neuf. */
    _closeAppMenuSidebar() {
        this.appsSearch.query = "";
        return super._closeAppMenuSidebar(...arguments);
    },

    /**
     * T2-1 🔴 Le balayage de fermeture ne doit pas manger les glissements DANS
     * le champ.
     *
     * Odoo écoute `touchstart`/`touchend` sur la racine du tiroir avec `.stop` :
     * ce modificateur arrête ce qui MONTE depuis ce nœud, il n'arrête rien de
     * ce qui monte de l'intérieur. Un glissement horizontal de plus de 100 px
     * (`SWIPE_ACTIVATION_THRESHOLD`) commencé dans le champ fermait donc le
     * tiroir et emportait la requête — or c'est le geste de sélection de texte
     * sur iOS, et le champ est le seul endroit du tiroir où un glissement
     * horizontal est légitime.
     *
     * On neutralise par la cible du `touchstart`, pas par `preventDefault` : la
     * spécification dispatche TOUS les événements d'un même toucher sur la cible
     * du `touchstart`, donc le doigt peut sortir du champ en chemin sans que la
     * garde lâche. Et empêcher le défaut tuerait la sélection de texte, c'est-à-dire
     * précisément ce qu'on vient protéger.
     */
    _onSwipeStart(ev) {
        if (ev.target?.closest?.(".o_apps_menu_search")) {
            // `_onSwipeEnd` d'Odoo sort tout de suite sans `swipeStartX`, et
            // notre garde ci-dessous ne compte pas sur ce détail.
            this.swipeStartX = null;
            this.appsSearchSwipe = true;
            return;
        }
        this.appsSearchSwipe = false;
        return super._onSwipeStart(...arguments);
    },

    /** Le `touchend` du même toucher : s'il est né dans le champ, il n'est pas un balayage. */
    _onSwipeEnd(ev) {
        if (this.appsSearchSwipe || ev.target?.closest?.(".o_apps_menu_search")) {
            this.appsSearchSwipe = false;
            this.swipeStartX = null;
            return;
        }
        return super._onSwipeEnd(...arguments);
    },

    /**
     * Entrée (la touche « aller » du clavier mobile) ouvre la première
     * correspondance et referme le tiroir — l'ordre qu'emploie le gabarit
     * d'Odoo quand on tape une ligne. Échap vide le champ.
     *
     * Sur un champ vide, elle ne fait rien : sans requête il n'y a pas de
     * « correspondance », et la touche est sous le pouce.
     */
    onAppsMenuSidebarSearchKeydown(ev) {
        // Rien à ouvrir sans requête : le champ a déjà le curseur, et sur un
        // téléphone la touche « aller » est sous le pouce — un effleurement
        // ouvrait la première application de la liste.
        // Et pendant une composition (claviers CJK, validation d'une
        // prédiction sur Gboard), Entrée appartient au clavier, pas à nous.
        if (ev.isComposing || ev.keyCode === 229) {
            return;
        }
        if (ev.key === "Enter") {
            const first = this.appsSearch.query.trim() ? this.appsMenuApps[0] : null;
            if (first) {
                ev.preventDefault();
                ev.stopPropagation();
                this.onNavBarDropdownItemSelection(first);
                this._closeAppMenuSidebar();
                // A7 : sans ça, le focus tombe sur le `body` quand le portail
                // disparaît, et un lecteur d'écran repart du haut de la page.
                document.querySelector("a.o_menu_toggle")?.focus();
            }
        } else if (ev.key === "Escape" && this.appsSearch.query) {
            ev.stopPropagation();
            this.appsSearch.query = "";
        }
    },
});
