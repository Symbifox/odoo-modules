"""Ce qu'un agent RH lit, et ce qu'il ne lit pas.

🔴 Le seuil ne vaut que s'il n'y a pas d'autre porte. Avant le correctif, un
agent RH lisait le score brut par RPC sur un département d'une seule
personne, et le jeton d'invitation lui permettait de répondre à sa place.
"""

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from .common import PulseCase


@tagged("post_install", "-at_install")
class TestPulseDroits(PulseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = new_test_user(
            cls.env, login="pulse_agent_rh", groups="hr.group_hr_user",
            company_id=cls.company.id, company_ids=[(6, 0, [cls.company.id])],
        )
        cls.gestionnaire = new_test_user(
            cls.env, login="pulse_gestionnaire_rh",
            groups="hr.group_hr_manager",
            company_id=cls.company.id, company_ids=[(6, 0, [cls.company.id])],
        )

    def _vague_a_une_personne(self):
        """Une seule réponse dans un département : le segment d'une personne."""
        campaign = self._campaign(segment_mode="department")
        campaign.action_open()
        invitation = campaign.invitation_ids.filtered(
            lambda i: i.employee_id.department_id == self.dept_b)[:1]
        self._repondre(invitation, note=2, enps=3)
        campaign.action_close()
        scores = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("segment_key", "=", "dept-%s" % self.dept_b.id),
        ])
        self.assertTrue(scores)
        self.assertFalse(any(scores.mapped("is_displayable")))
        return campaign, scores

    def test_l_agent_ne_lit_pas_le_score_brut_sous_le_seuil(self):
        _campaign, scores = self._vague_a_une_personne()
        vus = scores.with_user(self.agent)
        for champ in ("score", "enps"):
            with self.assertRaises(AccessError, msg=champ):
                vus.read([champ])
        lus = vus.read(["score_publie", "enps_publie", "display_score"])
        for ligne in lus:
            self.assertEqual(ligne["score_publie"], 0.0)
            self.assertEqual(ligne["enps_publie"], 0.0)
            self.assertEqual(ligne["display_score"], "Pas assez de réponses")

    def test_ni_le_gestionnaire_rh(self):
        _campaign, scores = self._vague_a_une_personne()
        with self.assertRaises(AccessError):
            scores.with_user(self.gestionnaire).read(["score"])

    def test_aucun_detour_par_la_recherche_ou_le_regroupement(self):
        _campaign, scores = self._vague_a_une_personne()
        Score = self.env["bf.ex.pulse.score"].with_user(self.agent)
        with self.assertRaises(AccessError):
            Score.search([("score", "<=", 2)])
        with self.assertRaises(AccessError):
            Score.search([], order="score")
        with self.assertRaises(AccessError):
            Score.read_group([], ["score:avg"], ["segment_key"])
        # Le regroupement permis ne rend que la valeur publiée, muette ici.
        groupes = Score.read_group(
            [("id", "in", scores.ids)], ["score_publie:avg"], ["segment_key"])
        self.assertEqual([g["score_publie"] for g in groupes], [0.0])

    def test_au_dessus_du_seuil_la_valeur_publiee_sort(self):
        campaign = self._campaign(score_threshold=5, text_threshold=5)
        campaign.action_open()
        for invitation in campaign.invitation_ids[:6]:
            self._repondre(invitation, note=8)
        campaign.action_close()
        score = self.env["bf.ex.pulse.score"].with_user(self.agent).search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ], limit=1)
        self.assertEqual(score.score_publie, 8.0)
        self.assertEqual(score.display_score, "8.0 / 10")

    def test_le_jeton_ne_se_lit_pas_hors_administration(self):
        campaign = self._campaign()
        campaign.action_open()
        invitation = campaign.invitation_ids[:1]
        for usager in (self.agent, self.gestionnaire):
            with self.assertRaises(AccessError, msg=usager.login):
                invitation.with_user(usager).read(["token"])
            with self.assertRaises(AccessError, msg=usager.login):
                self.env["bf.ex.pulse.invitation"].with_user(usager).search(
                    [("token", "=", invitation.token)])
        # Le registre lui-même reste lisible : qui a répondu, sans le jeton.
        lus = invitation.with_user(self.agent).read(["employee_id", "used"])
        self.assertEqual(lus[0]["used"], False)

    def test_l_agent_envoie_encore_les_invitations(self):
        campaign = self._campaign()
        campaign.with_user(self.agent).action_open()
        envoyes = campaign.with_user(self.agent).action_send_invitations()
        self.assertEqual(envoyes, len(self.employees))
        invitation = campaign.invitation_ids[:1]
        courriel = self.env["mail.mail"].search([
            ("email_to", "=", invitation.employee_id.work_email),
        ], order="id desc", limit=1)
        self.assertIn("/pulse/%s" % invitation.token, courriel.body_html)
