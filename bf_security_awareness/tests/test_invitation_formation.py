"""« Envoyer l'invitation » et la tâche planifiée des retards.

🔴 Les deux appelaient `activity_schedule` sur une assignation qui n'avait plus
le mixin d'activités depuis son déménagement vers `bf_training` : AttributeError,
transaction annulée, et le courriel d'invitation annulé avec elle.

Ce que ces essais tiennent en plus du plantage :
- une seule relance ouverte par assignation : la tâche planifiée tourne chaque
  jour, et une assignation en retard le reste plusieurs jours ;
- la tâche planifiée n'écrit pas à l'assigné chaque fois qu'elle pose une relance ;
- l'invitation part dans la mise en page de la société, avec le bouton,
  la date et la signature de la relance du registre, sans couleur codée en dur.
"""
import re
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestInvitationFormation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({"name": "Société Invitation", "email": "securite@exemple.test"})
        cls.cours = cls.env["slide.channel"].create({
            "name": "Reconnaître l'hameçonnage",
            "channel_type": "training",
            "enroll": "invite",
            "visibility": "connected",
        })
        cls.operateur = new_test_user(
            cls.env, login="operateur_inv", name="Opératrice Sécurité",
            email="operatrice.inv@exemple.test", notification_type="email",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_user")
        cls.gestionnaire = new_test_user(
            cls.env, login="gestionnaire_inv", name="Gestionnaire Sécurité",
            email="gestionnaire.inv@exemple.test", notification_type="email",
            groups="base.group_user,bf_security_awareness.group_bf_secaware_manager")
        cls.personne = cls.env["res.partner"].create({
            "name": "Alice Cible", "email": "alice.cible@exemple.test"})

    def _assignation(self, **valeurs):
        valeurs = {"partner_id": self.personne.id, "channel_id": self.cours.id,
                   "assigned_by_id": self.operateur.id, **valeurs}
        return self.env["bf.training.assignment"].create(valeurs)

    def _relances(self, assignation):
        return assignation.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref("mail.mail_activity_data_todo"))

    def _inviter(self, assignation, qui=None):
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        assignation.with_user(qui or self.operateur).action_send_invite()
        return self.env["mail.mail"].search([
            ("id", ">", dernier),
            ("model", "=", "bf.training.assignment"),
            ("res_id", "=", assignation.id),
        ])

    # -- le bouton ------------------------------------------------------------
    def test_le_bouton_envoie_et_planifie_une_relance(self):
        """🔴 Le défaut : AttributeError sur activity_schedule, rien ne partait."""
        assignation = self._assignation()
        courriels = self._inviter(assignation)
        self.assertEqual(len(courriels), 1, "l'invitation doit partir")
        self.assertIn(self.personne, courriels.recipient_ids)
        relances = self._relances(assignation)
        self.assertEqual(len(relances), 1)
        self.assertEqual(relances.user_id, self.operateur)
        self.assertIn("Alice Cible", relances.summary)
        self.assertEqual(assignation.reminder_count, 1)
        self.assertTrue(assignation.last_reminder_date)

    def test_un_second_envoi_ne_double_pas_la_relance(self):
        assignation = self._assignation()
        self._inviter(assignation)
        courriels = self._inviter(assignation)
        self.assertEqual(len(courriels), 1, "le second envoi part aussi")
        self.assertEqual(len(self._relances(assignation)), 1)
        self.assertEqual(assignation.reminder_count, 2)

    def test_la_relance_va_a_qui_a_assigne_meme_si_un_autre_envoie(self):
        assignation = self._assignation()
        self._inviter(assignation, qui=self.gestionnaire)
        self.assertEqual(self._relances(assignation).user_id, self.operateur)

    def test_une_assignation_du_registre_recoit_la_relance_du_registre(self):
        """🔴 Ce formulaire s'ouvre aussi depuis le registre : pas d'invitation cyber
        pour une formation de secourisme."""
        categorie = self.env["bf.training.category"].create(
            {"name": "Santé et sécurité", "code": "QA_T26310_REG"})
        activite = self.env["bf.training.activity"].create(
            {"name": "Secourisme en milieu de travail", "category_id": categorie.id})
        assignation = self.env["bf.training.assignment"].create(
            {"partner_id": self.personne.id, "activity_id": activite.id})
        courriels = self._inviter(assignation)
        self.assertEqual(len(courriels), 1)
        self.assertIn("Secourisme en milieu de travail", courriels.subject)
        self.assertNotIn("cybersécurité", courriels.subject.lower())
        self.assertNotIn("cybersécurité", (courriels.body_html or "").lower())
        self.assertFalse(self._relances(assignation))
        self.assertEqual(assignation.reminder_count, 1)

    def test_une_relance_reassignee_compte_encore(self):
        """Réassignée à un collègue, la relance suit toujours la personne : pas de seconde."""
        assignation = self._assignation()
        self._inviter(assignation)
        self._relances(assignation).user_id = self.gestionnaire
        self._inviter(assignation)
        self.assertEqual(len(self._relances(assignation)), 1)

    # -- qui suit la relance -----------------------------------------------------
    def test_une_remediation_ne_relance_jamais_l_utilisateur_public(self):
        """🔴 La remédiation naît de la page publique, en sudo depuis l'utilisateur public."""
        public = self.env.ref("base.public_user")
        assignation = self.env["bf.training.assignment"].with_user(public).sudo().create({
            "partner_id": self.personne.id, "channel_id": self.cours.id,
            "due_date": fields.Date.today() - timedelta(days=2)})
        self.assertEqual(assignation.assigned_by_id, public, "le défaut que la relance doit écarter")
        self.env["bf.training.assignment"]._cron_training_reminders()
        responsable = self._relances(assignation).user_id
        self.assertEqual(len(responsable), 1)
        self.assertNotEqual(responsable, public)
        self.assertTrue(responsable.active and not responsable.share)
        self.assertTrue(responsable.has_group("bf_security_awareness.group_bf_secaware_manager"))

    def test_le_clic_assigne_au_createur_de_la_campagne(self):
        modele = self.env["bf.phishing.template"].with_user(self.operateur).create({
            "name": "Leurre QA", "subject": "Action requise", "difficulty": "easy",
            "landing_mode": "credential", "training_channel_id": self.cours.id})
        campagne = self.env["bf.phishing.campaign"].with_user(self.operateur).create({
            "name": "Campagne QA", "template_id": modele.id, "auto_assign_training": True,
            "recipient_partner_ids": [(6, 0, self.personne.ids)]})
        campagne = campagne.with_user(self.env.user)
        campagne.action_prepare()
        resultat = campagne.result_ids[0]
        resultat.with_user(self.env.ref("base.public_user")).sudo().register_click()
        self.assertEqual(resultat.training_assignment_id.assigned_by_id, self.operateur)

    # -- le corps de l'invitation ----------------------------------------------
    def test_l_invitation_porte_le_bouton_la_date_et_le_lien_du_cours(self):
        echeance = fields.Date.today() + timedelta(days=10)
        assignation = self._assignation(due_date=echeance)
        corps = self._inviter(assignation).body_html or ""
        self.assertNotIn("#1a73e8", corps.lower(), "plus de bleu codé en dur")
        self.assertNotIn("L'équipe de sécurité", corps)
        self.assertNotIn(str(echeance), corps, "la date se lit, elle ne s'affiche pas en ISO")
        self.assertIn(self.cours.website_url, corps, "le bouton mène au cours")
        trouve = re.search(r"background-color:\s*(#[0-9a-fA-F]{3,6});[^\"']*color:\s*#fff;",
                           corps)
        self.assertTrue(trouve, "le bouton a un fond")
        self.assertEqual(trouve.group(1).lower(),
                         (assignation.reminder_button_color or "").lower(),
                         "le bouton prend la couleur de la relance du registre")

    def test_l_invitation_part_dans_la_mise_en_page_de_la_societe(self):
        """🔴 Sans `email_layout_xmlid`, `send_mail` part SANS mise en page."""
        assignation = self._assignation()
        corps = self._inviter(assignation).body_html or ""
        self.assertIn("Société Invitation", corps)
        if self.env.ref("bluefox_branding.bf_mail_layout", raise_if_not_found=False):
            self.env.company.report_brand_dark = "#123A4B"
            corps = self._inviter(assignation).body_html or ""
            self.assertIn("#123A4B", corps, "le bandeau de la mise en page maison")

    # -- la tâche planifiée ------------------------------------------------------
    def test_le_cron_des_retards_pose_une_seule_relance_sans_aviser(self):
        """🔴 Le cron plantait aussi, et sans garde il poserait une relance par jour."""
        assignation = self._assignation(due_date=fields.Date.today() - timedelta(days=3))
        avant = self.env["mail.message"].search_count([
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", self.operateur.partner_id.ids)])
        modele = self.env["bf.training.assignment"]
        modele._cron_training_reminders()
        modele._cron_training_reminders()
        relances = self._relances(assignation)
        self.assertEqual(len(relances), 1)
        self.assertEqual(relances.user_id, self.operateur)
        apres = self.env["mail.message"].search_count([
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", self.operateur.partner_id.ids)])
        self.assertEqual(apres, avant, "la tâche planifiée n'écrit pas à l'assigné")

    def test_le_cron_ne_double_pas_la_relance_de_l_invitation(self):
        assignation = self._assignation(due_date=fields.Date.today() - timedelta(days=1))
        self._inviter(assignation)
        self.env["bf.training.assignment"]._cron_training_reminders()
        self.assertEqual(len(self._relances(assignation)), 1)

    def test_une_relance_faite_laisse_le_cron_en_poser_une_autre(self):
        assignation = self._assignation(due_date=fields.Date.today() - timedelta(days=2))
        modele = self.env["bf.training.assignment"]
        modele._cron_training_reminders()
        self._relances(assignation).action_feedback(feedback="Relancée au téléphone")
        modele._cron_training_reminders()
        self.assertEqual(len(self._relances(assignation)), 1)
