# -*- coding: utf-8 -*-
"""La langue du lecteur, de la source jusqu'au dessin.

La source est en anglais ; le français vient du catalogue fr_CA, qui peut ne
pas exister encore quand ces essais tournent. Ce qui en dépend est donc prouvé
deux fois :

* contre `env._()` dans la même langue, ce qui tient avec ou sans catalogue ;
* contre une traduction posée le temps de l'essai dans le cache des
  traductions de code (`_catalogue_fr`), ce qui prouve que le terme passe bien
  par la langue du lecteur même quand le catalogue manque.

Les mois, eux, viennent de babel : aucun catalogue n'y entre.

⚠️ L'export des termes balaie aussi les essais : aucun `_()` ne porte ici de
texte littéral, sinon il entrerait au catalogue sans français. Les termes
passent par des variables (`_attendu`).
"""
import base64
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from babel.dates import get_month_names

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.translate import code_translations

from ..generateur import geometrie as geo
from ..generateur import svg as gen_svg

# Les termes anglais de la source, et le français qu'on pose quand le
# catalogue manque. Le français est celui de la source française d'origine.
REGROUPEMENT = "Project stage"
COULOIR = "Lane"
REPERE = "%(count)s lines · %(start)s to %(end)s"
AUJOURDHUI = "today"
TRONQUEE = "truncated list"
RACINE = 'This file is not an MSPDI file: the root is "%(root)s", not "Project".'
LUES = "%(count)s lines read from an MS Project file."
TITRE = "File title: %(title)s"
FRANCAIS = {
    REGROUPEMENT: "Étape du projet",
    COULOIR: "Couloir",
    REPERE: "%(count)s lignes · %(start)s au %(end)s",
    AUJOURDHUI: "aujourd'hui",
    TRONQUEE: "liste tronquée",
    RACINE: "Ce fichier n'est pas un MSPDI : la racine est « %(root)s » et non « Project ».",
    LUES: "%(count)s lignes lues dans un fichier MS Project.",
    TITRE: "Titre du fichier : %(title)s",
}


@tagged("post_install", "-at_install", "bf_gantt")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # La base des essais a fr_CA ; en_US peut y être inactive, et un
        # contexte dans une langue inactive lève avant toute traduction.
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.projet = cls.env["project.project"].create({"name": "Projet langue"})
        cls.env["project.task"].create({
            "name": "Tâche de septembre",
            "project_id": cls.projet.id,
            "planned_date_begin": datetime(2026, 9, 7, 9, 0),
            "date_deadline": datetime(2026, 9, 25, 17, 0),
        })

    def _source(self, langue):
        return self.env["bf.gantt.source"].with_context(lang=langue)

    def _attendu(self, langue, terme):
        """Ce que `env._()` rend pour ce terme, dans cette langue, ici."""
        return self.env(context=dict(self.env.context, lang=langue))._(terme)

    @contextmanager
    def _catalogue_fr(self, termes=None):
        """Les traductions Python de bf_gantt en fr_CA, augmentées de `termes`
        le temps du bloc, par-dessus le catalogue s'il existe."""
        cle = ("bf_gantt", "fr_CA")
        traductions = dict(code_translations.get_python_translations(*cle))
        traductions.update(termes or FRANCAIS)
        with patch.dict(code_translations.python_translations,
                        {cle: traductions}):
            yield

    # ------------------------------------------------------ (a) regroupements

    def test_le_libelle_d_un_regroupement_suit_la_langue(self):
        def libelle(langue, genre, cle):
            groupings = self._source(langue).get_portefeuille()["groupings"]
            return next(g["label"] for g in groupings[genre] if g["key"] == cle)

        # Sans rien poser : la traduction de la langue, quelle qu'elle soit.
        self.assertEqual(libelle("en_US", "project", "stage"), REGROUPEMENT)
        self.assertEqual(libelle("fr_CA", "project", "stage"),
                         self._attendu("fr_CA", REGROUPEMENT))
        # Avec le français posé : la preuve que le libellé est traduit pour le
        # lecteur, et non figé au chargement du module.
        with self._catalogue_fr():
            self.assertEqual(libelle("fr_CA", "project", "stage"),
                             FRANCAIS[REGROUPEMENT])
            self.assertEqual(libelle("fr_CA", "plan", "lane"), FRANCAIS[COULOIR])
            self.assertEqual(libelle("en_US", "project", "stage"), REGROUPEMENT)
            self.assertEqual(libelle("en_US", "plan", "lane"), COULOIR)

    # ---------------------------------------------------- (b) textes du dessin

    def test_les_textes_du_dessin_suivent_la_langue(self):
        anglais = self._source("en_US").get_echeancier("project", self.projet.id)
        self.assertEqual(anglais["lang"], "en_US")
        self.assertEqual(anglais["labels"]["today"], AUJOURDHUI)
        self.assertEqual(anglais["labels"]["summary"], REPERE)

        francais = self._source("fr_CA").get_echeancier("project", self.projet.id)
        self.assertEqual(francais["lang"], "fr_CA")
        for cle, terme in (("today", AUJOURDHUI), ("truncated", TRONQUEE),
                           ("summary", REPERE)):
            self.assertEqual(francais["labels"][cle],
                             self._attendu("fr_CA", terme))

        with self._catalogue_fr():
            francais = self._source("fr_CA").get_echeancier(
                "project", self.projet.id)
            self.assertEqual(francais["labels"]["today"], FRANCAIS[AUJOURDHUI])
            self.assertEqual(francais["labels"]["truncated"], FRANCAIS[TRONQUEE])
            # Et le rendu les écrit : le repère du bandeau, dans le SVG.
            dessin = gen_svg.rendre(francais).decode("utf-8")
            self.assertIn("1 lignes · 2026-09-04 au 2026-09-28", dessin)
        dessin = gen_svg.rendre(anglais).decode("utf-8")
        self.assertIn("1 lines · 2026-09-04 to 2026-09-28", dessin)

    def test_sans_libelles_les_rendus_retombent_sur_l_anglais(self):
        """Les générateurs sont aussi appelés sans base : le repli doit tenir."""
        payload = self._source("fr_CA").get_echeancier("project", self.projet.id)
        payload = dict(payload, labels={}, lang=None)
        dessin = gen_svg.rendre(payload).decode("utf-8")
        self.assertIn("1 lines · 2026-09-04 to 2026-09-28", dessin)
        self.assertIn(geo.LIBELLES["approximate"], dessin)

    def test_une_traduction_cassee_ne_fait_pas_tomber_le_rendu(self):
        textes = dict(geo.LIBELLES, summary="%(nombre)s lignes")
        self.assertEqual(
            geo.formater(textes, "summary", count=3, start="a", end="b"),
            "3 lines · a to b")

    # ------------------------------------------------------------ (c) les mois

    def test_les_mois_suivent_la_langue(self):
        def rang_du_haut(langue):
            g = self._source(langue).get_geometrie(
                "project", self.projet.id, echelle="week")
            return [h["texte"] for h in g["graduations"]["haut"]]

        septembre_fr = get_month_names(
            "abbreviated", context="stand-alone", locale="fr_CA")[9]
        septembre_en = get_month_names(
            "abbreviated", context="stand-alone", locale="en_US")[9]
        self.assertNotEqual(septembre_fr, septembre_en)
        self.assertEqual(rang_du_haut("fr_CA"), ["%s 2026" % septembre_fr])
        self.assertEqual(rang_du_haut("en_US"), ["%s 2026" % septembre_en])

    def test_une_langue_inconnue_rend_les_mois_anglais(self):
        self.assertEqual(geo.calendrier("xx_YY")["mois"], geo.MOIS_EN)
        self.assertEqual(geo.calendrier(None)["mois"], geo.MOIS_EN)
        # Lexend ne dessine pas le cyrillique : l'anglais plutôt que des trous.
        self.assertEqual(geo.calendrier("ru_RU")["mois"], geo.MOIS_EN)

    # ----------------------------------------------------- l'assistant d'import

    def _assistant(self, langue, contenu):
        return self.env["bf.gantt.import"].with_context(lang=langue).create({
            "fichier": base64.b64encode(contenu),
            "nom_fichier": "plan.xml",
        })

    def test_le_refus_d_un_fichier_parle_la_langue_de_l_usager(self):
        pas_mspdi = b"<?xml version='1.0'?><autrechose/>"
        with self.assertRaises(UserError) as anglais:
            self._assistant("en_US", pas_mspdi).action_lire()
        self.assertIn('the root is "autrechose"', str(anglais.exception))
        with self._catalogue_fr(), self.assertRaises(UserError) as francais:
            self._assistant("fr_CA", pas_mspdi).action_lire()
        self.assertIn("la racine est « autrechose »", str(francais.exception))

    def test_l_apercu_parle_la_langue_de_l_usager_et_echappe_le_titre(self):
        lignes = [{"name": "A", "start": "2026-09-07", "lane": "", "depends_on": []}]
        with self._catalogue_fr():
            resume = self._assistant("fr_CA", b"x")._resume(
                "Titre <script>", lignes, 1, "mspdi")
        self.assertIn("<b>1</b> lignes lues dans un fichier MS Project.", resume)
        self.assertIn("Titre du fichier : Titre &lt;script&gt;", resume)
        self.assertNotIn("<script>", resume)
