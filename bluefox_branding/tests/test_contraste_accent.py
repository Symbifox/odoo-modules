"""L'accent de marque employé comme FOND doit porter du texte blanc.

Mesuré au navigateur le 2026-09-11 sur une copie de production : le bouton
« Enregistrer », le bouton « Activer » du menu de Discussion et la pastille de
compteur écrivent tous du blanc sur l'accent, et la paire rendait 2,62:1 sur
le bleu clair. Aucune erreur n'est levée par un contraste insuffisant : le seul
contrôle qui le voit est une mesure, donc un test.

Le test se joue sur les SEPT marques réellement présentes dans le parc, pas sur
une valeur d'exemple : trois d'entre elles passent déjà et doivent rester
intactes, ce qu'un assombrissement global casserait sans rien dire.
"""

from odoo.tests import TransactionCase, tagged

from ..models.brand_color_mixin import (
    _contraste,
    _en_rgb,
    assombrir_pour_texte_blanc,
)

BLANC = (255, 255, 255)
SEUIL = 4.5

# Les sept accents réellement en service au 2026-09-11, décrits par ce qu'ils
# sont plutôt que par qui les porte : ce fichier voyage dans l'arbre d'addons de
# chaque locataire, et le nom d'un client n'a rien à y faire.
PARC = [
    ("bleu clair", "#29ABE2", True),
    ("orange", "#e17a4b", True),
    ("bleu moyen", "#1186c0", True),
    ("or", "#C9A24E", True),
    ("vert sourd", "#5c6e5b", False),
    ("bleu franc", "#176CF2", False),
    ("violet d'Odoo", "#714B67", False),
]


@tagged("post_install", "-at_install")
class TestContrasteAccent(TransactionCase):

    def test_chaque_marque_du_parc_porte_du_texte_blanc(self):
        for nom, couleur, _doit_bouger in PARC:
            with self.subTest(accent=nom):
                obtenu = assombrir_pour_texte_blanc(couleur)
                self.assertGreaterEqual(
                    _contraste(_en_rgb(obtenu), BLANC), SEUIL,
                    "%s (%s -> %s) n'atteint pas le seuil AA" % (nom, couleur, obtenu),
                )

    def test_une_marque_deja_lisible_n_est_pas_touchee(self):
        """Un assombrissement global abîmerait trois marques sur sept."""
        for nom, couleur, doit_bouger in PARC:
            with self.subTest(accent=nom):
                obtenu = assombrir_pour_texte_blanc(couleur)
                if doit_bouger:
                    self.assertNotEqual(obtenu.lower(), couleur.lower(),
                                        "%s rendait %.2f:1, elle devait être assombrie"
                                        % (nom, _contraste(_en_rgb(couleur), BLANC)))
                else:
                    self.assertEqual(obtenu, couleur,
                                     "%s passait déjà, elle ne doit pas bouger" % nom)

    def test_on_reste_le_plus_pres_possible_de_la_marque(self):
        """Un pas de plus vers le clair doit repasser SOUS le seuil.

        C'est ce qui distingue « assombri juste assez » d'« assombri ». Sans
        cette assertion, rendre du noir passerait le premier test.
        """
        for nom, couleur, doit_bouger in PARC:
            if not doit_bouger:
                continue
            with self.subTest(accent=nom):
                obtenu = _en_rgb(assombrir_pour_texte_blanc(couleur))
                base = _en_rgb(couleur)
                # Le facteur retenu, puis le facteur immédiatement au-dessus.
                facteur = max(obtenu[i] / base[i] for i in range(3) if base[i])
                plus_clair = tuple(c * (facteur + 0.01) for c in base)
                self.assertLess(
                    _contraste(plus_clair, BLANC), SEUIL,
                    "%s pourrait rester plus proche de sa marque" % nom,
                )

    def test_une_valeur_illisible_ne_leve_rien(self):
        """Une couleur vide ou mal écrite rend la valeur telle quelle.

        Une couleur de marque ne vaut jamais une trace d'exception au
        chargement d'une page.
        """
        for valeur in ("", None, "bleu", "#12", "#1234567"):
            self.assertEqual(assombrir_pour_texte_blanc(valeur), valeur)
        self.assertEqual(assombrir_pour_texte_blanc("#abc"), "#6d7883")

    def test_la_societe_expose_la_variante(self):
        societe = self.env["res.company"].create({
            "name": "Essai contraste",
            "report_brand_primary": "#29ABE2",
        })
        self.assertEqual(societe._brand_primary_on_white(), "#1e7fa7")
        societe.report_brand_primary = "#714B67"
        self.assertEqual(societe._brand_primary_on_white(), "#714B67")

    def test_la_feuille_de_style_pointe_la_variante(self):
        """Les fonds sous du texte blanc lisent `-on-white`, pas l'accent brut.

        Le défaut d'origine n'était pas une couleur fausse, c'était la mauvaise
        variable au bon endroit : un test sur la valeur seule l'aurait manqué.

        ⚠️ Il ne suffit pas de vérifier que `-on-white` apparaît quelque part
        dans le bloc : une première version lisait 400 caractères à partir du
        sélecteur, et le survol, resté correct, suffisait à la faire passer
        pendant que la règle de base avait repris l'accent brut. On lit donc le
        bloc COMPLET, et on exige qu'aucun `var(--brand-primary,` nu n'y peigne
        un fond.
        """
        import pathlib
        import re
        scss = (pathlib.Path(__file__).resolve().parent.parent
                / "static" / "src" / "scss" / "branding.scss").read_text()
        for bloc in (".btn-primary", ".badge-primary", ".progress-bar", ".o-discuss-badge"):
            corps = self._bloc(scss, bloc)
            self.assertIn("--brand-primary-on-white", corps,
                          "%s ne lit pas la variante lisible" % bloc)
            # Toute déclaration qui PEINT un fond dans ce bloc doit passer par
            # la variante. Le repli chaîné `var(--brand-primary-on-white,
            # var(--brand-primary, …))` est conforme : ce qu'on refuse, c'est
            # une déclaration qui ne nomme jamais `-on-white`.
            nus = [l.strip() for l in corps.splitlines()
                   if re.search(r"(background-color|--o-discuss-badge-bg)\s*:", l)
                   and "--brand-primary-on-white" not in l]
            self.assertFalse(nus, "%s peint encore l'accent brut sous du texte "
                                  "blanc : %s" % (bloc, nus))

    @staticmethod
    def _bloc(scss, selecteur):
        """Le corps de la règle, accolades appariées, imbrications comprises."""
        debut = scss.index(selecteur + ",") if (selecteur + ",") in scss else scss.index(selecteur)
        ouvrante = scss.index("{", debut)
        profondeur, i = 0, ouvrante
        while i < len(scss):
            if scss[i] == "{":
                profondeur += 1
            elif scss[i] == "}":
                profondeur -= 1
                if profondeur == 0:
                    return scss[ouvrante:i + 1]
            i += 1
        raise AssertionError("bloc non refermé pour " + selecteur)
