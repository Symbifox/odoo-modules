# -*- coding: utf-8 -*-
from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPontEvenements(TransactionCase):

    def _evenement(self, nom, dans_jours):
        debut = fields.Datetime.add(fields.Datetime.now(), days=dans_jours)
        return self.env["event.event"].create({
            "name": nom, "date_begin": debut,
            "date_end": fields.Datetime.add(debut, hours=2)})

    def _carte(self, evenement):
        return self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "event.event"), ("source_res_id", "=", evenement.id)])

    def test_un_evenement_a_venir_paraît_et_tombe_le_lendemain(self):
        evenement = self._evenement("Rencontre d'équipe", 7)
        carte = self._carte(evenement)
        self.assertEqual(len(carte), 1)
        self.assertEqual(carte.type_publication, "evenement")
        jour = fields.Datetime.context_timestamp(evenement, evenement.date_begin).date()
        self.assertEqual(carte.date_echeance, fields.Date.add(jour, days=1))

    def test_un_evenement_passe_ne_paraît_pas(self):
        evenement = self._evenement("Rencontre de l'an dernier", -30)
        self.assertFalse(self._carte(evenement))

    def test_pas_de_doublon_si_le_pont_repasse(self):
        evenement = self._evenement("Formation SST", 3)
        evenement._babillard_suivre()
        evenement._babillard_suivre()
        self.assertEqual(len(self._carte(evenement)), 1)

    def test_un_nouveau_nom_et_une_nouvelle_date_suivent(self):
        """🔴 La carte ne suivait que la création : un report laissait la vieille date au fil."""
        evenement = self._evenement("Rencontre d'équipe", 7)
        debut = fields.Datetime.add(fields.Datetime.now(), days=10)
        evenement.write({"name": "Rencontre d'équipe, reportée", "date_begin": debut,
                         "date_end": fields.Datetime.add(debut, hours=2)})
        carte = self._carte(evenement)
        self.assertEqual(len(carte), 1)
        self.assertIn("reportée", carte.name)
        jour = fields.Datetime.context_timestamp(evenement, debut).date()
        self.assertEqual(carte.date_echeance, fields.Date.add(jour, days=1))

    def test_un_evenement_archive_quitte_le_fil(self):
        """🔴 Et il n'y revient pas : le pont annonce, il ne défait pas une décision."""
        evenement = self._evenement("Dîner d'équipe", 5)
        evenement.write({"active": False})
        self.assertEqual(self._carte(evenement).state, "echue")
        evenement.write({"active": True})
        self.assertEqual(self._carte(evenement).state, "echue")

    def test_une_carte_retiree_a_la_main_ne_revient_pas(self):
        evenement = self._evenement("Caucus", 4)
        carte = self._carte(evenement)
        carte.action_retirer()
        evenement.write({"name": "Caucus déplacé"})
        self.assertEqual(carte.state, "echue")
        self.assertNotIn("déplacé", carte.name)

    def test_une_carte_archivee_ne_bloque_pas_l_evenement(self):
        """🔴 La contrainte d'unicité voit les cartes archivées, pas la recherche."""
        evenement = self._evenement("Journée portes ouvertes", 6)
        self._carte(evenement).write({"active": False})
        evenement.write({"name": "Journée portes ouvertes, revue"})
        cartes = self.env["bf.babillard.post"].sudo().with_context(
            active_test=False).search([("source_model", "=", "event.event"),
                                       ("source_res_id", "=", evenement.id)])
        self.assertEqual(len(cartes), 1)

    def test_un_evenement_termine_ou_annule_quitte_le_fil(self):
        fin = self.env["event.stage"].search([("pipe_end", "=", True)], limit=1)
        if not fin:
            self.skipTest("aucune étape de fin sur cette base")
        evenement = self._evenement("Atelier", 5)
        evenement.write({"stage_id": fin.id})
        self.assertEqual(self._carte(evenement).state, "echue")

    def test_un_evenement_supprime_quitte_le_fil(self):
        evenement = self._evenement("Formation retirée", 5)
        carte = self._carte(evenement)
        evenement.unlink()
        self.assertEqual(carte.state, "echue")

    def test_la_carte_suit_la_societe_de_l_evenement(self):
        evenement = self._evenement("Rencontre", 5)
        self.assertEqual(self._carte(evenement).company_id, evenement.company_id)
