/* Célébrations — le diaporama, sans dépendance.
 *
 * Volontairement en JavaScript simple plutôt qu'en composant OWL : la page
 * est servie hors du client web, à un écran de bureau ou à un appel vidéo,
 * et charger tout le paquet d'actifs pour faire défiler des cartes serait
 * disproportionné.
 */
(function () {
    "use strict";

    var mots = Array.prototype.slice.call(
        document.querySelectorAll(".cel-diapo-mot"));
    if (!mots.length) {
        return;
    }

    var index = 0;
    var enPause = false;
    var DELAI = 7000;

    function montrer(n) {
        mots.forEach(function (mot, i) {
            mot.classList.toggle("cel-actif", i === n);
        });
    }

    function avancer(pas) {
        index = (index + pas + mots.length) % mots.length;
        montrer(index);
    }

    montrer(0);

    var minuteur = window.setInterval(function () {
        if (!enPause) {
            avancer(1);
        }
    }, DELAI);

    document.addEventListener("keydown", function (ev) {
        if (ev.key === " ") {
            ev.preventDefault();
            enPause = !enPause;
        } else if (ev.key === "ArrowRight") {
            avancer(1);
        } else if (ev.key === "ArrowLeft") {
            avancer(-1);
        } else if (ev.key === "Escape") {
            window.clearInterval(minuteur);
        }
    });

    document.addEventListener("click", function () {
        avancer(1);
    });
}());
