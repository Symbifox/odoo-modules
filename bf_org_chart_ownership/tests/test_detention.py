# -*- coding: utf-8 -*-
import psycopg2

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "bf_org_chart")
class TestDetention(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        P = cls.env["res.partner"]
        cls.holding = P.create({"name": "9012-3456 Québec inc.", "is_company": True})
        cls.fiducie = P.create({"name": "Fiducie familiale", "is_company": True})
        cls.filiale = P.create({"name": "Souci Plastique inc.", "is_company": True})
        cls.petite = P.create({"name": "Immeubles Souci", "is_company": True})
        cls.Lien = cls.env["bf.ownership"]

    def _lien(self, detenteur, detenue, part, **kw):
        return self.Lien.create(dict(
            {"owner_id": detenteur.id, "owned_id": detenue.id, "percent": part}, **kw))

    def test_total_partiel_puis_complet(self):
        self._lien(self.holding, self.filiale, 60)
        self.assertEqual(self.filiale.ownership_state, "partielle")
        self.assertAlmostEqual(self.filiale.ownership_total, 60)
        self._lien(self.fiducie, self.filiale, 40)
        self.filiale.invalidate_recordset()
        self.assertEqual(self.filiale.ownership_state, "complete")

    def test_excedent_est_signale_pas_refuse(self):
        """Un total au-dessus de 100 se voit, mais ne bloque pas la saisie."""
        self._lien(self.holding, self.filiale, 60)
        self._lien(self.fiducie, self.filiale, 60)
        self.filiale.invalidate_recordset()
        self.assertEqual(self.filiale.ownership_state, "excedent")

    def test_structure_inconnue_reste_saisissable(self):
        self._lien(self.holding, self.filiale, 12.5)
        self.assertEqual(self.filiale.ownership_state, "partielle")

    def test_se_detenir_soi_meme_refuse(self):
        with self.assertRaises(ValidationError):
            self._lien(self.filiale, self.filiale, 100)

    def test_boucle_de_detention_refusee(self):
        self._lien(self.holding, self.filiale, 100)
        self._lien(self.filiale, self.petite, 100)
        with self.assertRaises(ValidationError):
            self._lien(self.petite, self.holding, 100)

    @mute_logger("odoo.sql_db")
    def test_pourcentage_hors_bornes_refuse(self):
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.cr.savepoint():
                self._lien(self.holding, self.filiale, 140)

    def test_lien_termine_ne_compte_plus(self):
        self._lien(self.holding, self.filiale, 100, date_fin="2020-01-01")
        self.filiale.invalidate_recordset()
        self.assertEqual(self.filiale.ownership_state, "aucune")

    def test_la_carte_porte_le_pourcentage_sur_l_arete(self):
        self._lien(self.holding, self.filiale, 60)
        self._lien(self.fiducie, self.filiale, 40, share_class="B")
        carte = self.filiale._org_chart_carte("detention")
        etiquettes = sorted(a.etiquette for a in carte.aretes)
        self.assertEqual(etiquettes, ["40 % · B", "60 %"])
        self.assertEqual(len(carte.boites), 3)

    def test_le_dessin_passe_en_couches_quand_il_y_a_deux_detenteurs(self):
        self._lien(self.holding, self.filiale, 60)
        self._lien(self.fiducie, self.filiale, 40)
        plan = self.filiale._org_chart_plan("detention")
        self.assertEqual(plan.mode, "couches")

    def test_sans_droit_de_vote_se_dessine_en_pointille(self):
        self._lien(self.holding, self.filiale, 30, voting=False)
        carte = self.filiale._org_chart_carte("detention")
        self.assertTrue(carte.aretes[0].pointille)

    def test_le_groupe_remonte_et_redescend(self):
        self._lien(self.holding, self.filiale, 100)
        self._lien(self.filiale, self.petite, 75)
        groupe, tronque = self.filiale._org_chart_groupe()
        self.assertEqual(set(groupe.ids),
                         {self.holding.id, self.filiale.id, self.petite.id})
        self.assertFalse(tronque, "trois étages ne tronquent rien")

    def test_plafond_de_boites(self):
        """Au-delà du plafond, on refuse de dessiner plutôt que de figer Odoo."""
        from unittest.mock import patch

        from odoo.exceptions import UserError
        self._lien(self.holding, self.filiale, 50)
        self._lien(self.fiducie, self.filiale, 50)
        with patch.object(type(self.filiale), "PLAFOND_BOITES", 2):
            with self.assertRaises(UserError):
                self.filiale._org_chart_plan("detention")

    def test_chercher_un_lien_par_son_nom_rend_la_main(self):
        """🔴 Avec `_rec_name = "display_name"`, cet appel ne rendait JAMAIS la
        main : le champ calculé renvoyait à name_search, qui rebâtissait le même
        domaine, à 100 % de CPU. L'essai qui l'aurait vu est celui-ci."""
        lien = self._lien(self.holding, self.filiale, 60)
        trouves = self.Lien.name_search("9012-3456")
        self.assertIn(lien.id, [i for i, _nom in trouves],
                      "un lien se cherche par le nom de son détenteur")
        trouves = self.Lien.name_search("Souci Plastique")
        self.assertIn(lien.id, [i for i, _nom in trouves],
                      "un lien se cherche aussi par le nom de la société détenue")
        self.assertEqual(self.Lien.name_search("nom-qui-n-existe-pas"), [])

    def test_une_detention_a_venir_ne_compte_pas_aujourd_hui(self):
        """🔴 `date_effet` n'était lu nulle part : une convention signée pour
        le 1er janvier prochain était dessinée et comptée aujourd'hui."""
        from datetime import date, timedelta
        demain = date.today() + timedelta(days=1)
        self._lien(self.holding, self.filiale, 100, date_effet=demain)
        self.filiale.invalidate_recordset()
        self.assertEqual(self.filiale.ownership_state, "aucune")
        carte = self.filiale._org_chart_carte("detention")
        self.assertEqual(carte.aretes, [], "un lien à venir ne se dessine pas")

    def test_une_succession_historique_ne_totalise_pas_160(self):
        """Le cas qui teintait une structure correcte en rouge : deux lignes
        pour le même couple, l'ancienne fermée, la nouvelle en vigueur."""
        from datetime import date, timedelta
        hier = date.today() - timedelta(days=1)
        self._lien(self.holding, self.filiale, 100,
                   date_effet="2020-01-01", date_fin=hier)
        self._lien(self.holding, self.filiale, 60, date_effet="2024-01-01")
        self._lien(self.fiducie, self.filiale, 40, date_effet="2024-01-01")
        self.filiale.invalidate_recordset()
        self.assertAlmostEqual(self.filiale.ownership_total, 100)
        self.assertEqual(self.filiale.ownership_state, "complete")

    def test_un_doublon_exact_est_refuse(self):
        """🔴 `UNIQUE (owner, owned, share_class, date_effet)` ne contraignait
        rien : les deux dernières colonnes sont facultatives, et PostgreSQL
        tient deux NULL pour distincts. Trois lignes identiques passaient."""
        self._lien(self.holding, self.filiale, 100)
        with self.assertRaises(ValidationError):
            self._lien(self.holding, self.filiale, 100)

    def test_un_doublon_qui_differe_par_la_categorie_est_accepte(self):
        self._lien(self.holding, self.filiale, 60, share_class="A")
        lien = self._lien(self.holding, self.filiale, 40, share_class="B")
        self.assertTrue(lien.id)

    def test_la_troncature_en_profondeur_est_annoncee(self):
        """🔴 Neuf sociétés sur quatorze étaient dessinées, sans un mot."""
        P = self.env["res.partner"]
        chaine = [P.create({"name": "Étage %02d" % i, "is_company": True})
                  for i in range(14)]
        for i in range(13):
            self._lien(chaine[i], chaine[i + 1], 100)
        groupe, tronque = chaine[0]._org_chart_groupe()
        self.assertTrue(tronque, "la structure dépasse le garde-fou")
        plan = chaine[0]._org_chart_plan("detention")
        self.assertTrue(plan.avertissements,
                        "une carte tronquée doit le dire sur la page")
