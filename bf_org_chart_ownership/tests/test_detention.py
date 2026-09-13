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
        groupe = self.filiale._org_chart_groupe()
        self.assertEqual(set(groupe.ids),
                         {self.holding.id, self.filiale.id, self.petite.id})

    def test_plafond_de_boites(self):
        """Au-delà du plafond, on refuse de dessiner plutôt que de figer Odoo."""
        from unittest.mock import patch

        from odoo.exceptions import UserError
        self._lien(self.holding, self.filiale, 50)
        self._lien(self.fiducie, self.filiale, 50)
        with patch.object(type(self.filiale), "PLAFOND_BOITES", 2):
            with self.assertRaises(UserError):
                self.filiale._org_chart_plan("detention")
