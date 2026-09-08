/* Célébrations — l'ouverture de la carte livrée.
 *
 * Une enveloppe, son rabat qui se soulève, la carte qui en sort, puis les
 * mots qui apparaissent un à un sous une pluie de confettis aux couleurs du
 * thème. Tout est en CSS ; ce fichier ne fait qu'orchestrer et semer les
 * confettis. Rien n'est chargé d'ailleurs.
 *
 * Trois retenues, parce qu'une animation qu'on ne peut pas couper est une
 * gêne : `prefers-reduced-motion` la supprime, un clic la saute, et elle
 * ne se joue qu'une fois par navigateur (« Rejouer l'ouverture » la ramène).
 */
(function () {
    "use strict";

    var ouverture = document.getElementById("cel-ouverture");
    var confettis = document.getElementById("cel-confettis");
    var mots = Array.prototype.slice.call(document.querySelectorAll(".cel-mur .cel-mot"));
    if (!ouverture) {
        return;
    }

    var jeton = ouverture.getAttribute("data-token") || "";
    var rejouer = ouverture.getAttribute("data-rejouer") === "1";
    var cle = "cel_ouvert_" + jeton;
    var reduit = window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var dejaVu = false;
    try {
        dejaVu = !!window.localStorage.getItem(cle);
    } catch (e) { /* stockage refusé : on joue */ }

    function memoriser() {
        try {
            window.localStorage.setItem(cle, String(Date.now()));
        } catch (e) { /* tant pis */ }
    }

    function couleurs() {
        var style = getComputedStyle(document.documentElement);
        var liste = [
            style.getPropertyValue("--cel-accent"),
            style.getPropertyValue("--cel-accent-texte"),
            style.getPropertyValue("--cel-entete"),
            "#F2C14E", "#E8632B", "#2E7D5B"
        ];
        return liste.map(function (c) { return c.trim(); })
            .filter(function (c) { return c; });
    }

    function semerConfettis() {
        if (!confettis) {
            return;
        }
        var palette = couleurs();
        var n = 90;
        for (var i = 0; i < n; i++) {
            var piece = document.createElement("span");
            piece.className = "cel-confetti";
            piece.style.setProperty("--x", (Math.random() * 100).toFixed(2) + "vw");
            piece.style.setProperty("--d", (Math.random() * 1.6).toFixed(2) + "s");
            piece.style.setProperty("--t", (2.8 + Math.random() * 2.2).toFixed(2) + "s");
            piece.style.setProperty("--r", (Math.random() * 720 - 360).toFixed(0) + "deg");
            piece.style.setProperty("--c", palette[i % palette.length]);
            piece.style.setProperty("--w", (6 + Math.random() * 6).toFixed(0) + "px");
            confettis.appendChild(piece);
        }
        confettis.hidden = false;
        window.setTimeout(function () {
            confettis.hidden = true;
            confettis.innerHTML = "";
        }, 6500);
    }

    function revelerMots() {
        mots.forEach(function (mot, i) {
            mot.style.setProperty("--i", i);
            mot.classList.add("cel-apparait");
        });
    }

    var fini = false;
    function terminer() {
        // Un clic après l'ouverture ET le minuteur peuvent arriver tous les
        // deux : une seule fin, sinon deux pluies de confettis.
        if (fini) {
            return;
        }
        fini = true;
        ouverture.classList.add("cel-ouverture-finie");
        window.setTimeout(function () {
            ouverture.hidden = true;
        }, 650);
        memoriser();
        semerConfettis();
        revelerMots();
    }

    var ouvert = false;
    function ouvrir() {
        if (ouvert) {
            return;
        }
        ouvert = true;
        ouverture.classList.add("cel-ouvre");
        window.setTimeout(terminer, 1900);
    }

    if (reduit || (dejaVu && !rejouer)) {
        // Pas d'enveloppe, pas de confettis. Les mots sont là, tout de suite.
        ouverture.hidden = true;
        return;
    }

    ouverture.hidden = false;
    mots.forEach(function (mot) { mot.classList.add("cel-attend"); });
    ouverture.addEventListener("click", function () {
        if (!ouvert) {
            ouvrir();
        } else {
            terminer();
        }
    });
    document.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape" || ev.key === " " || ev.key === "Enter") {
            ev.preventDefault();
            ouvert ? terminer() : ouvrir();
        }
    });
    window.setTimeout(ouvrir, 1100);
}());
