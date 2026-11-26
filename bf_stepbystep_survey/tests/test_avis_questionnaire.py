"""Le questionnaire rempli depuis la page avise le responsable du mandat."""
from unittest.mock import PropertyMock, patch

from odoo.tests import TransactionCase, new_test_user, tagged

from .test_questionnaire import _AvecQuestionnaire


@tagged("post_install", "-at_install")
class TestAvisDuQuestionnaire(TransactionCase, _AvecQuestionnaire):

    def setUp(self):
        super().setUp()
        self.projet, self.colonnes = self._mandat(self.env)
        self.responsable = new_test_user(
            self.env, login="resp_avis_q", name="Responsable Questionnaire",
            email="responsable-q@essai.invalid", notification_type="inbox",
            groups="project.group_project_user")
        self.projet.user_id = self.responsable
        (self.projet.company_id or self.responsable.company_id).write({
            "progression_notify_activity": True,
            "progression_notify_inbox": False,
            "progression_notify_email": True,
        })
        self.sondage = self._questionnaire(self.env)
        self.projet.progression_survey_id = self.sondage
        self.projet.action_progression_portal_publish()
        self.type_activite = self.env.ref(
            "bf_stepbystep_clients.mail_activity_type_progression_client")

    def _traiter(self):
        self.env["bf.progression.notice"]._cron_envoyer()

    def _activites(self):
        return self.env["mail.activity"].sudo().search([
            ("res_model", "=", "project.project"), ("res_id", "=", self.projet.id),
            ("activity_type_id", "=", self.type_activite.id)])

    def _courriels(self):
        return self.env["mail.mail"].sudo().search([
            ("email_to", "ilike", "responsable-q@essai.invalid")])

    def test_le_questionnaire_fini_avise_le_responsable(self):
        reponse = self.projet._progression_survey_ouvrir(partenaire=self.projet.partner_id)
        self.assertTrue(reponse)
        self.assertFalse(self._activites())
        reponse._mark_done()
        # Rien dans la transaction du client : l'avis attend la tâche planifiée.
        self.assertFalse(self._activites())
        self._traiter()
        activite = self._activites()
        self.assertEqual(len(activite), 1)
        self.assertEqual(activite.user_id, self.responsable)
        self.assertEqual(activite.summary, "Questionnaire completed: Diagnostic Export")
        self.assertEqual(len(self._courriels()), 1)
        # Une réponse déjà finie repassée par `_mark_done` n'avise pas de nouveau.
        reponse._mark_done()
        self._traiter()
        self.assertEqual(len(self._courriels()), 1)

    def test_la_limite_de_temps_ne_se_dit_pas_rempli(self):
        reponse = self.projet._progression_survey_ouvrir(partenaire=self.projet.partner_id)
        with patch.object(type(reponse), "survey_time_limit_reached",
                          new_callable=PropertyMock, return_value=True):
            reponse._mark_done()
        self._traiter()
        self.assertIn("time limit", self._activites().summary)

    def test_une_reponse_hors_de_la_page_n_avise_personne(self):
        """Une réponse que le mandat ne porte pas (le même questionnaire rempli
        ailleurs) n'est pas un geste du client sur sa page."""
        ailleurs = self.sondage._create_answer(partner=self.projet.partner_id,
                                               check_attempts=False)
        ailleurs._mark_done()
        self._traiter()
        self.assertFalse(self._activites())
        self.assertFalse(self._courriels())

    def test_la_reponse_et_l_etape_sont_reservees_au_groupe_de_publication(self):
        """Un usager de Projet LIT le mandat, pas ces deux champs.

        ⚠️ Écrire un projet est déjà refusé à un usager de Projet par les droits
        d'Odoo : l'essai mesure donc la garde du CHAMP par la lecture, seule
        porte que l'usager a, et non par une écriture qu'il n'aurait jamais."""
        from odoo.exceptions import AccessError
        self.projet._progression_survey_ouvrir(partenaire=self.projet.partner_id)
        self.projet.progression_survey_stage_id = self.colonnes[1]
        usager = new_test_user(
            self.env, login="usager_projet_q", name="Usager Projet",
            groups="project.group_project_user")
        projet = self.projet.with_user(usager)
        self.assertTrue(projet.read(["name"]))
        for champ in ("progression_survey_answer_id", "progression_survey_stage_id"):
            with self.assertRaises(AccessError, msg=champ):
                projet.read([champ])

    def test_une_reponse_detachee_n_avise_personne(self):
        """Le questionnaire du mandat a changé : l'ancienne réponse ne lui
        appartient plus, même si le lien est resté."""
        reponse = self.projet._progression_survey_ouvrir(partenaire=self.projet.partner_id)
        self.projet.progression_survey_id = self._questionnaire(self.env, title="Autre")
        reponse._mark_done()
        self._traiter()
        self.assertFalse(self._activites())
        self.assertFalse(self._courriels())
