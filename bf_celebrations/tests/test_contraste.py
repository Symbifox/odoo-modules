# -*- coding: utf-8 -*-
"""Le contraste des thèmes, mesuré plutôt que jugé à l'œil.

🔴 Ce test existe parce que le ton de marque ne fait pas un ton de texte.
Mesuré le 2026-09-07 sur la première version des palettes : le bleu Blue Fox
#29ABE1 rendait 2,62:1 sur blanc et l'orange #E8632B 3,36:1, là où WCAG AA en
demande 4,5. Rien ne le signalait : la page s'affichait, le texte était là,
simplement trop pâle pour être lu par une partie des gens.

Un thème ajouté plus tard tombera dans le même piège. Ce test le rattrape.
"""

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_celebrations.models.celebration_board import PALETTES

AA_TEXTE = 4.5   # texte courant
AA_OBJET = 3.0   # frontière d'un objet d'interface, et gros titre


def _luminance(hexa):
    hexa = hexa.lstrip("#")
    canaux = [int(hexa[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    canaux = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in canaux
    ]
    return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]


def contraste(avant, arriere):
    a, b = _luminance(avant), _luminance(arriere)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


@tagged("post_install", "-at_install", "bf_celebrations")
class TestContraste(TransactionCase):

    def test_chaque_theme_est_lisible(self):
        echecs = []
        for nom, p in PALETTES.items():
            paires = (
                ("texte sur la carte", p["texte"], p["surface"], AA_TEXTE),
                ("texte sur la page", p["texte"], p["fond"], AA_TEXTE),
                ("titre sur la page", p["entete"], p["fond"], AA_OBJET),
                ("signature sur la carte",
                 p["accent_texte"], p["surface"], AA_TEXTE),
                ("texte du bouton",
                 p["bouton_texte"], p["bouton_fond"], AA_TEXTE),
                ("bouton sur la carte",
                 p["bouton_fond"], p["surface"], AA_OBJET),
                ("bouton sur la page",
                 p["bouton_fond"], p["fond"], AA_OBJET),
            )
            for libelle, avant, arriere, seuil in paires:
                mesure = contraste(avant, arriere)
                if mesure < seuil:
                    echecs.append(
                        "%s / %s : %.2f:1 (il en faut %.1f)"
                        % (nom, libelle, mesure, seuil))
        self.assertFalse(echecs, "Contrastes insuffisants :\n  " +
                         "\n  ".join(echecs))

    def test_toutes_les_cles_sont_la(self):
        """Une palette incomplète rendrait le repli du `var()`, donc la
        couleur d'un AUTRE thème, sans que rien ne le signale."""
        attendues = {
            "fond", "surface", "texte", "accent", "accent_texte",
            "bouton_fond", "bouton_texte", "entete",
        }
        for nom, p in PALETTES.items():
            self.assertEqual(
                attendues - set(p), set(),
                "Le thème « %s » n'a pas toutes ses couleurs." % nom)
