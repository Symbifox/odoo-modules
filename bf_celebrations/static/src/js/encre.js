/* Célébrations — écrire à la main sur la carte.
 *
 * Un canevas, des événements de pointeur (doigt, stylet, souris), et une
 * liste de traits en coordonnées LOGIQUES 800 × 320, quelle que soit la
 * taille d'écran. C'est cette liste, pas une image, qui part au serveur :
 * il la relit, la borne, et la redessine en SVG dans la couleur du thème.
 *
 * Sans dépendance, comme le diaporama : la page est servie hors du client
 * web, à des gens qui n'ont pas de compte.
 */
(function () {
    "use strict";

    var canevas = document.getElementById("cel-encre");
    var champ = document.getElementById("cel-encre-donnees");
    if (!canevas || !champ) {
        return;
    }

    var LARGEUR = 800, HAUTEUR = 320, EPAISSEUR = 3.2;
    var ctx = canevas.getContext("2d");
    var traits = [];
    var courant = null;
    var dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));

    // Le canevas garde ses coordonnées logiques ; seul le tampon grossit
    // avec la densité d'écran, sinon le trait est flou sur un téléphone.
    canevas.width = LARGEUR * dpr;
    canevas.height = HAUTEUR * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    function couleur() {
        return getComputedStyle(canevas).color || "#2D3031";
    }

    function position(ev) {
        var r = canevas.getBoundingClientRect();
        var x = (ev.clientX - r.left) * LARGEUR / r.width;
        var y = (ev.clientY - r.top) * HAUTEUR / r.height;
        return [
            Math.round(Math.min(Math.max(x, 0), LARGEUR) * 10) / 10,
            Math.round(Math.min(Math.max(y, 0), HAUTEUR) * 10) / 10
        ];
    }

    // Le même lissage que le serveur (quadratiques aux milieux) : ce que
    // la personne voit en traçant est ce que la carte montrera.
    function tracer(points) {
        if (!points.length) {
            return;
        }
        ctx.strokeStyle = couleur();
        ctx.fillStyle = ctx.strokeStyle;
        ctx.lineWidth = EPAISSEUR;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        if (points.length === 1) {
            ctx.beginPath();
            ctx.arc(points[0][0], points[0][1], EPAISSEUR / 2, 0, Math.PI * 2);
            ctx.fill();
            return;
        }
        ctx.beginPath();
        ctx.moveTo(points[0][0], points[0][1]);
        for (var i = 1; i < points.length - 1; i++) {
            var mx = (points[i][0] + points[i + 1][0]) / 2;
            var my = (points[i][1] + points[i + 1][1]) / 2;
            ctx.quadraticCurveTo(points[i][0], points[i][1], mx, my);
        }
        var d = points[points.length - 1];
        ctx.lineTo(d[0], d[1]);
        ctx.stroke();
    }

    function redessiner() {
        ctx.clearRect(0, 0, LARGEUR, HAUTEUR);
        traits.forEach(tracer);
        if (courant) {
            tracer(courant);
        }
        canevas.classList.toggle("cel-encre-vide", !traits.length && !courant);
    }

    function serialiser() {
        champ.value = traits.length ? JSON.stringify(traits) : "";
    }

    canevas.addEventListener("pointerdown", function (ev) {
        if (ev.button !== undefined && ev.button !== 0) {
            return;
        }
        ev.preventDefault();
        try {
            canevas.setPointerCapture(ev.pointerId);
        } catch (e) { /* vieux navigateur : on trace quand même */ }
        courant = [position(ev)];
        redessiner();
    });

    canevas.addEventListener("pointermove", function (ev) {
        if (!courant) {
            return;
        }
        ev.preventDefault();
        var p = position(ev);
        var dernier = courant[courant.length - 1];
        // Sauter les points quasi immobiles allège la charge sans changer
        // le trait.
        if (Math.abs(p[0] - dernier[0]) + Math.abs(p[1] - dernier[1]) < 0.6) {
            return;
        }
        courant.push(p);
        redessiner();
    });

    function terminer(ev) {
        if (!courant) {
            return;
        }
        if (ev) {
            ev.preventDefault();
        }
        traits.push(courant);
        courant = null;
        redessiner();
        serialiser();
    }
    canevas.addEventListener("pointerup", terminer);
    canevas.addEventListener("pointercancel", terminer);

    var effacer = document.getElementById("cel-encre-effacer");
    var annuler = document.getElementById("cel-encre-annuler");
    if (effacer) {
        effacer.addEventListener("click", function () {
            traits = [];
            courant = null;
            redessiner();
            serialiser();
        });
    }
    if (annuler) {
        annuler.addEventListener("click", function () {
            traits.pop();
            redessiner();
            serialiser();
        });
    }

    // Les onglets « Écrire » / « À la main ». Les deux restent dans le
    // formulaire : on peut taper un mot ET tracer une signature.
    var boutons = document.querySelectorAll("[data-cel-onglet]");
    var volets = document.querySelectorAll("[data-cel-volet]");
    boutons.forEach(function (b) {
        b.addEventListener("click", function () {
            var cible = b.getAttribute("data-cel-onglet");
            boutons.forEach(function (x) {
                var actif = x === b;
                x.classList.toggle("cel-onglet-actif", actif);
                x.setAttribute("aria-selected", actif ? "true" : "false");
            });
            volets.forEach(function (v) {
                v.hidden = v.getAttribute("data-cel-volet") !== cible;
            });
            if (cible === "main") {
                redessiner();
            }
        });
    });

    // Le thème peut changer la couleur du texte après le chargement
    // (mode sombre du système) : on redessine avec la couleur du moment.
    if (window.matchMedia) {
        window.matchMedia("(prefers-color-scheme: dark)")
            .addEventListener("change", redessiner);
    }

    redessiner();
}());
