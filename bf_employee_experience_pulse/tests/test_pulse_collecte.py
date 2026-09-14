"""Le cycle de vie d'une vague, et ce que le module refuse de faire."""

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import PulseCase


@tagged("post_install", "-at_install")
class TestPulseCollecte(PulseCase):

    def test_ouvrir_tire_un_jeton_par_personne(self):
        campaign = self._campaign()
        campaign.action_open()
        self.assertEqual(campaign.state, "open")
        self.assertEqual(campaign.invitation_count, len(self.employees))
        jetons = campaign.invitation_ids.mapped("token")
        self.assertEqual(len(set(jetons)), len(jetons), "deux jetons identiques")
        self.assertTrue(all(len(j) >= 32 for j in jetons))

    def test_une_vague_sans_question_ne_s_ouvre_pas(self):
        campaign = self._campaign(question_ids=[(5, 0, 0)])
        with self.assertRaises(UserError):
            campaign.action_open()

    def test_une_vague_ne_s_ouvre_qu_une_fois(self):
        campaign = self._campaign()
        campaign.action_open()
        with self.assertRaises(UserError):
            campaign.action_open()

    def test_une_personne_n_est_invitee_qu_une_fois(self):
        campaign = self._campaign()
        campaign.action_open()
        employes = campaign.invitation_ids.mapped("employee_id")
        self.assertEqual(len(employes), len(set(employes.ids)))

    def test_le_compteur_ne_dit_que_le_nombre(self):
        campaign = self._campaign()
        campaign.action_open()
        self.assertEqual(campaign.response_count, 0)
        for invitation in campaign.invitation_ids[:3]:
            self._repondre(invitation)
        campaign.invalidate_recordset()
        self.assertEqual(campaign.response_count, 3)
        self.assertAlmostEqual(campaign.response_rate, 37.5, places=1)

    def test_fermer_verse_meme_sous_le_seuil(self):
        campaign = self._campaign(text_threshold=5)
        campaign.action_open()
        self._repondre(campaign.invitation_ids[0], note=4)
        self.assertEqual(campaign._flush_staged(), 0)
        campaign.action_close()
        self.assertEqual(campaign.state, "closed")
        self.assertEqual(
            self.env["bf.ex.pulse.staging"].sudo().search_count(
                [("campaign_id", "=", campaign.id)]), 0,
        )
        self.assertTrue(
            self.env["bf.ex.pulse.answer"].sudo().search_count(
                [("campaign_id", "=", campaign.id)]),
        )

    def test_la_date_versee_est_celle_de_la_vague(self):
        campaign = self._campaign()
        campaign.action_open()
        for invitation in campaign.invitation_ids[:5]:
            self._repondre(invitation)
        campaign._flush_staged()
        periodes = set(self.env["bf.ex.pulse.answer"].sudo().search([
            ("campaign_id", "=", campaign.id)]).mapped("period"))
        self.assertEqual(periodes, {campaign.date_open})

    def test_l_axe_est_fige_au_versement(self):
        """Déplacer une question d'axe ne doit pas réécrire le passé."""
        campaign = self._campaign()
        campaign.action_open()
        axe_origine = self.question_scale.metric_id
        for invitation in campaign.invitation_ids[:5]:
            self._repondre(invitation)
        campaign._flush_staged()
        autre_axe = self.env.ref("bf_employee_experience_pulse.metric_peers")
        self.question_scale.metric_id = autre_axe
        reponses = self.env["bf.ex.pulse.answer"].sudo().search([
            ("campaign_id", "=", campaign.id),
            ("question_id", "=", self.question_scale.id),
        ])
        self.assertTrue(reponses)
        self.assertEqual(set(reponses.mapped("metric_id")), {axe_origine})

    def test_la_passe_periodique_verse_et_recalcule(self):
        campaign = self._campaign(score_threshold=3, text_threshold=5)
        campaign.action_open()
        for invitation in campaign.invitation_ids[:6]:
            self._repondre(invitation, note=9)
        self.env["bf.ex.pulse.campaign"]._cron_flush_and_score()
        self.assertEqual(campaign.pending_flush_count, 0)
        score = self.env["bf.ex.pulse.score"].search([
            ("campaign_id", "=", campaign.id),
            ("metric_id", "=", self.question_scale.metric_id.id),
        ], limit=1)
        self.assertTrue(score.is_displayable)
        self.assertEqual(score.respondent_count, 6)

    def test_la_relance_ne_vise_que_les_silencieux(self):
        campaign = self._campaign()
        campaign.action_open()
        for invitation in campaign.invitation_ids[:3]:
            self._repondre(invitation)
        envoyes = campaign.action_send_invitations()
        self.assertEqual(envoyes, len(self.employees) - 3)

    def test_une_vague_fermee_n_envoie_rien(self):
        campaign = self._campaign()
        campaign.action_open()
        campaign.action_close()
        with self.assertRaises(UserError):
            campaign.action_send_invitations()

    def test_le_module_n_ecrit_rien_dans_un_moteur_de_points(self):
        """Une gratification nominative serait la feuille de présence.

        Le contrôle porte sur le REGISTRE, pas sur le texte des fichiers : la
        prose du manifeste a le droit d'expliquer pourquoi on s'en abstient,
        et c'est un champ ou une dépendance qui ferait le mal, pas un mot.
        """
        module = self.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_employee_experience_pulse")], limit=1)
        self.assertTrue(module)
        dependances = module.dependencies_id.mapped("name")
        self.assertNotIn("bf_gamification", dependances)
        self.assertNotIn("gamification", dependances)

        nos_modeles = [
            nom for nom in self.env.registry
            if nom.startswith("bf.ex.pulse.")
        ]
        self.assertTrue(nos_modeles)
        for nom in nos_modeles:
            for champ in self.env[nom]._fields.values():
                if not champ.relational:
                    continue
                self.assertFalse(
                    champ.comodel_name.startswith(("bf.gamification",
                                                   "gamification.")),
                    "%s.%s pointe vers un moteur de points"
                    % (nom, champ.name),
                )

        donnees = self.env["ir.model.data"].sudo().search([
            ("module", "=", "bf_employee_experience_pulse"),
        ])
        for donnee in donnees:
            self.assertFalse(
                donnee.model.startswith(("bf.gamification", "gamification.")),
                "le module sème un enregistrement dans %s" % donnee.model,
            )
