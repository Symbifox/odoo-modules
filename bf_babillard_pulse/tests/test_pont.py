# -*- coding: utf-8 -*-
from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPontPulse(TransactionCase):
    """Éprouvé sur des scores réels, pas sur le texte du code source.

    ⚠️ La première version lisait `inspect.getsource` pour vérifier que le
    domaine portait bien `is_displayable`. Un essai qui lit le source ne prouve
    rien : il passe si l'appel est mort, et il rougit si on reformate.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.axe = cls.env["bf.ex.pulse.metric"].search([], limit=1) or \
            cls.env["bf.ex.pulse.metric"].create({"name": "Charge de travail",
                                                  "code": "charge"})
        cls.autre_axe = cls.env["bf.ex.pulse.metric"].search([], limit=1, order="id desc")

    def _vague(self, nom="Vague d'essai"):
        return self.env["bf.ex.pulse.campaign"].create({
            "name": nom, "state": "open"})

    def _score(self, vague, axe, affichable, segment=False, score=7.5):
        return self.env["bf.ex.pulse.score"].sudo().create({
            "campaign_id": vague.id,
            "company_id": self.env.company.id,
            "metric_id": axe.id,
            "segment_key": segment,
            "period_start": fields.Date.subtract(
                fields.Date.context_today(self.env.user), days=90),
            "period_end": fields.Date.context_today(self.env.user),
            "respondent_count": 4 if affichable else 1,
            "score": score,
            "is_displayable": affichable,
        })

    def _carte(self, vague):
        return self.env["bf.babillard.post"].sudo().search([
            ("source_model", "=", "bf.ex.pulse.campaign"),
            ("source_res_id", "=", vague.id)])

    def test_un_score_affichable_paraît(self):
        vague = self._vague()
        self._score(vague, self.axe, affichable=True)
        vague.action_close()
        carte = self._carte(vague)
        self.assertEqual(len(carte), 1)
        self.assertIn(self.axe.name, carte.corps_html)

    def test_un_score_sous_le_seuil_ne_paraît_pas(self):
        """Le seuil du pulse reste la loi : sous le seuil, aucune carte."""
        vague = self._vague("Vague sans assez de réponses")
        self._score(vague, self.axe, affichable=False)
        vague.action_close()
        self.assertFalse(self._carte(vague))

    def test_aucun_segment_d_equipe_ne_paraît(self):
        """Un score par équipe se désanonymise par soustraction."""
        vague = self._vague("Vague par équipe")
        self._score(vague, self.axe, affichable=True, segment="atelier")
        vague.action_close()
        self.assertFalse(self._carte(vague))

    def test_la_carte_ne_porte_pas_le_score_brut_sous_le_seuil(self):
        vague = self._vague("Vague mixte")
        self._score(vague, self.axe, affichable=True, score=8.0)
        self._score(vague, self.axe, affichable=False, score=2.0, segment=False)
        vague.action_close()
        carte = self._carte(vague)
        self.assertIn("8.0", carte.corps_html)
        self.assertNotIn("2.0", carte.corps_html)
