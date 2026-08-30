/**
 * Le bascule clair / sombre d'une page de liens.
 *
 * Pourquoi un fichier plutôt qu'un `<script>` en ligne dans le gabarit : la
 * politique de sécurité du site est `script-src 'self'`, SANS `unsafe-inline`.
 * Un script en ligne serait bloqué par le navigateur, en silence pour qui ne
 * regarde pas la console. Servi par le module, il vient de la même origine.
 *
 * Ce fichier entre dans `web.assets_frontend`, donc il est chargé sur TOUTES
 * les pages publiques du site. D'où la sortie immédiate quand la page n'est
 * pas une page de liens : il ne doit rien coûter ailleurs.
 *
 * Le choix du visiteur vit dans SON navigateur et nulle part ailleurs. Rien
 * n'est envoyé au serveur, donc rien n'est à déclarer : ce n'est pas un
 * traceur, c'est une préférence d'affichage locale.
 */
(function () {
    "use strict";

    // Le catalogue de traduction d'Odoo, quand il est là. Ce fichier est
    // chargé sur toutes les pages publiques, y compris avant que le noyau web
    // ne soit disponible : on ne peut donc pas importer `_t` en dur, sous
    // peine de casser le script partout où le module n'est pas concerné.
    function t(texte) {
        try {
            var noyau = window.odoo && window.odoo.loader
                && window.odoo.loader.modules.get("@web/core/l10n/translation");
            return noyau && noyau._t ? noyau._t(texte) : texte;
        } catch (error) {
            return texte;
        }
    }

    var STORAGE_KEY = "bf_linkpage_theme";

    function currentPreference() {
        // Un navigateur en navigation privée, ou réglé pour refuser le
        // stockage, lève au lieu de rendre null. Sans ce filet, l'exception
        // remonte et le bouton n'est jamais affiché.
        try {
            var value = window.localStorage.getItem(STORAGE_KEY);
            return value === "light" || value === "dark" ? value : null;
        } catch (error) {
            return null;
        }
    }

    function remember(value) {
        try {
            window.localStorage.setItem(STORAGE_KEY, value);
        } catch (error) {
            // Le bascule continue de fonctionner pour la visite en cours ; il
            // ne sera simplement pas retenu. Mieux qu'un bouton mort.
        }
    }

    function systemPrefersDark() {
        return (
            window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: dark)").matches
        );
    }

    function effectiveTheme(root) {
        var forced = root.getAttribute("data-bf-theme");
        if (forced === "light" || forced === "dark") {
            return forced;
        }
        if (root.classList.contains("bf-linkpage--light")) {
            return "light";
        }
        if (root.classList.contains("bf-linkpage--dark")) {
            return "dark";
        }
        return systemPrefersDark() ? "dark" : "light";
    }

    function paintButton(button, theme) {
        var icon = button.querySelector("i");
        if (icon) {
            icon.className = theme === "dark" ? "fa fa-sun-o" : "fa fa-moon-o";
        }
        button.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
        button.setAttribute(
            "aria-label",
            theme === "dark" ? t("Passer au thème clair") : t("Passer au thème sombre")
        );
    }

    function start() {
        var root = document.querySelector("[data-bf-linkpage]");
        if (!root) {
            return; // Pas une page de liens : ce script n'a rien à y faire.
        }

        var saved = currentPreference();
        if (saved) {
            root.setAttribute("data-bf-theme", saved);
        }

        var button = root.querySelector("[data-bf-theme-toggle]");
        if (!button) {
            return;
        }

        // Le bouton n'apparaît qu'ici : sans JavaScript, il resterait un
        // bouton qui ne fait rien, ce qui est pire que pas de bouton.
        button.hidden = false;
        paintButton(button, effectiveTheme(root));

        button.addEventListener("click", function () {
            var next = effectiveTheme(root) === "dark" ? "light" : "dark";
            root.setAttribute("data-bf-theme", next);
            remember(next);
            paintButton(button, next);
        });

        // Le visiteur qui n'a jamais touché au bascule doit suivre son
        // système quand celui-ci change en cours de route (coucher du soleil,
        // bascule automatique du système d'exploitation).
        if (window.matchMedia) {
            var query = window.matchMedia("(prefers-color-scheme: dark)");
            var onChange = function () {
                if (!currentPreference()) {
                    root.removeAttribute("data-bf-theme");
                    paintButton(button, effectiveTheme(root));
                }
            };
            if (query.addEventListener) {
                query.addEventListener("change", onChange);
            } else if (query.addListener) {
                query.addListener(onChange);
            }
        }
    }

    // `DOMContentLoaded` a déjà pu passer quand le module est évalué depuis
    // un paquet d'actifs : lire l'état du document plutôt que de supposer.
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
