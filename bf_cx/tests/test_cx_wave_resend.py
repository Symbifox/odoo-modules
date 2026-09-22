"""Le renvoi d'invitation à UN destinataire.

Ce que ce bouton doit protéger n'est pas « le courriel est reparti » -
c'est tout ce qu'il aurait été facile de bousculer en le faisant partir :
un deuxième jeton pour la même personne (deux liens vivants, deux
réponses possibles, un taux de réponse dilué), la quarantaine
anti-sursollicitation repoussée pour un courriel que la personne a
elle-même redemandé, ou le marqueur du rappel COLLECTIF posé, ce qui
priverait silencieusement les autres non-répondants de leur relance.

Chacune de ces régressions passerait inaperçue sur le formulaire de la
vague : elle a donc son assertion ici.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

SEND = "odoo.addons.mail.models.mail_mail.MailMail.send"


def _delivered(mail_self, *args, **kwargs):
    """Livraison simulée FIDÈLE : elle pose l'état que le vrai envoi pose.

    Un patch nu de `send` laisse le courriel en « outgoing », donc un
    contrôle d'état honnête le lirait comme un échec. Simuler le succès
    sans poser `state='sent'`, ce serait écrire un banc plus indulgent
    que la réalité - et rendre inéprouvable le contrôle qu'on tient
    justement à garder.
    """
    mail_self.write({"state": "sent"})


def _refused(mail_self, *args, **kwargs):
    """Échec de livraison tel que le coeur le rapporte : sans lever."""
    mail_self.write({
        "state": "exception",
        "failure_reason": "SMTP a refusé le destinataire (essai)",
    })


def sending(outcome=_delivered):
    return patch(SEND, autospec=True, side_effect=outcome)


@tagged("post_install", "-at_install")
class TestCxWaveResend(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Param = cls.env["ir.config_parameter"].sudo()
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        cls.Param.set_param("bf_cx.solicitation_cooldown_days", "0")
        cls.client = cls.env["res.partner"].create(
            {"name": "Cliente Qui A Supprimé", "email": "supprime@example.com"}
        )
        cls.other = cls.env["res.partner"].create(
            {"name": "Autre Destinataire", "email": "autre@example.com"}
        )

    def _sent_wave(self, **vals):
        """Une vague partie pour de vrai, deux destinataires invités."""
        wave = self.env["bf.cx.wave"].create(dict({
            "name": "Vague avec renvoi",
            "program_id": self.program.id,
            "partner_ids": [(6, 0, (self.client | self.other).ids)],
            "deadline": fields.Datetime.now() + timedelta(days=7),
        }, **vals))
        with sending():
            wave.action_send()
        return wave

    def _answer_of(self, wave, partner):
        return wave.user_input_ids.filtered(lambda i: i.partner_id == partner)

    def _mails_for(self, answer):
        return self.env["mail.mail"].search(
            [("model", "=", "survey.user_input"), ("res_id", "=", answer.id)]
        )

    # ── Le renvoi atteint la bonne personne, sur le même lien ────────────

    def test_resend_reuses_the_same_token(self):
        """Un deuxième jeton, ce serait deux liens vivants pour une personne."""
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        token = answer.access_token
        before = len(wave.user_input_ids)
        with sending():
            answer.action_bf_cx_resend_invite()
        wave.invalidate_recordset()
        self.assertEqual(len(wave.user_input_ids), before,
                         "aucune deuxième réponse n'est créée")
        self.assertEqual(answer.access_token, token, "le lien ne change pas")

    def test_resend_reaches_that_contact_and_nobody_else(self):
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        before = self.env["mail.mail"].search_count([])
        with sending():
            answer.action_bf_cx_resend_invite()
        created = self.env["mail.mail"].search_count([]) - before
        self.assertEqual(created, 1, "un courriel, et un seul")
        self.assertEqual(self._mails_for(answer)[0].recipient_ids, self.client)
        self.assertEqual(
            len(self._mails_for(self._answer_of(wave, self.other))), 1,
            "l'autre destinataire garde son unique invitation",
        )

    def test_resend_uses_the_invitation_template(self):
        """C'est une invitation re-livrée, pas une relance."""
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        with sending():
            answer.action_bf_cx_resend_invite()
        subjects = self._mails_for(answer).mapped("subject")
        self.assertEqual(len(subjects), 2)
        self.assertEqual(subjects[0], subjects[1],
                         "le renvoi porte le sujet de l'invitation initiale")

    # ── Ce que le renvoi ne doit PAS bouger ──────────────────────────────

    def test_resend_does_not_extend_the_quarantine(self):
        """La personne a redemandé son lien : ce n'est pas une 2e demande."""
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        self.client.invalidate_recordset()
        stamped = self.client.bf_cx_last_solicited
        with sending():
            answer.action_bf_cx_resend_invite()
        self.client.invalidate_recordset()
        self.assertEqual(self.client.bf_cx_last_solicited, stamped)

    def test_resend_does_not_consume_the_collective_reminder(self):
        """Poser reminder_date priverait les AUTRES de leur relance."""
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        with sending():
            answer.action_bf_cx_resend_invite()
        wave.invalidate_recordset()
        self.assertFalse(wave.reminder_date)
        # Et le rappel collectif part encore, aux deux non-répondants.
        before = self.env["mail.mail"].search_count([])
        with sending():
            wave.action_remind()
        self.assertEqual(self.env["mail.mail"].search_count([]) - before, 2)

    def test_resend_does_not_move_the_wave_counters(self):
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        wave.invalidate_recordset()
        invited, state, sent_date = wave.invited_count, wave.state, wave.sent_date
        with sending():
            answer.action_bf_cx_resend_invite()
        wave.invalidate_recordset()
        self.assertEqual(wave.invited_count, invited)
        self.assertEqual(wave.state, state)
        self.assertEqual(wave.sent_date, sent_date)

    def test_resend_is_traced_on_the_wave(self):
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        before = len(wave.message_ids)
        with sending():
            answer.action_bf_cx_resend_invite()
        wave.invalidate_recordset()
        self.assertEqual(len(wave.message_ids), before + 1)
        self.assertIn("Cliente Qui A Supprimé", wave.message_ids[0].body)

    # ── Les refus, et leur raison ────────────────────────────────────────

    def test_refuses_when_the_contact_already_answered(self):
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        answer._save_lines(self.program.score_question_id, "9")
        answer._mark_done()
        with self.assertRaises(UserError):
            answer.action_bf_cx_resend_invite()

    def test_refuses_past_the_deadline(self):
        """Renvoyer un lien mort en promettant le contraire."""
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        past = fields.Datetime.now() - timedelta(days=1)
        wave.deadline = past
        answer.deadline = past
        with self.assertRaises(UserError):
            answer.action_bf_cx_resend_invite()

    def test_refuses_on_a_closed_wave(self):
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        wave.action_close()
        with self.assertRaises(UserError):
            answer.action_bf_cx_resend_invite()
        # Et le bouton « Rouvrir » suffit à débloquer le geste.
        wave.action_reopen()
        with sending():
            answer.action_bf_cx_resend_invite()

    def test_refuses_a_blacklisted_contact(self):
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        self.env["mail.blacklist"].sudo()._add(self.client.email)
        with self.assertRaises(UserError):
            answer.action_bf_cx_resend_invite()

    def test_refuses_an_answer_without_a_wave(self):
        answer = self.program.survey_id._create_answer(
            partner=self.client, check_attempts=False
        )
        with self.assertRaises(UserError):
            answer.action_bf_cx_resend_invite()

    def test_refuses_a_contact_without_an_email(self):
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        self.client.email = False
        with self.assertRaises(UserError):
            answer.action_bf_cx_resend_invite()

    # ── La cadence, elle, ne doit PAS bloquer un renvoi ──────────────────

    def test_cooldown_never_blocks_a_resend(self):
        """Le cas réel : la personne vient d'être invitée, donc en quarantaine.

        Si la cadence mordait ici, le bouton serait inutilisable
        exactement quand on en a besoin - juste après l'envoi.
        """
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "365")
        self.program.cooldown_days = 365
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        self.client.invalidate_recordset()
        self.assertTrue(self.client.bf_cx_last_solicited,
                        "la vague a bien mis la personne en quarantaine")
        with sending():
            answer.action_bf_cx_resend_invite()
        self.assertEqual(len(self._mails_for(answer)), 2)

    # ── Droits ───────────────────────────────────────────────────────────

    def _survey_user(self, wave_perm_read=False):
        """Un utilisateur de sondage, avec au plus le droit de LIRE les vagues.

        Les droits de survey.user_input sont ceux du module Sondages,
        bien plus larges que ceux de l'Expérience client : sans garde sur
        la vague, un utilisateur de sondage déclencherait un envoi client
        par `call_kw`.
        """
        user = self.env["res.users"].create({
            "name": "Sondeur Sans CX",
            "login": "sondeur-sans-cx%s@example.com" % int(wave_perm_read),
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("survey.group_survey_user").id,
            ])],
        })
        if wave_perm_read:
            # Lecture sur la vague ET sur le programme : sans les deux, le
            # refus viendrait d'un modèle voisin et non de la garde qu'on
            # veut éprouver - un test qui passe pour la mauvaise raison.
            for model in ("bf.cx.wave", "bf.cx.program"):
                self.env["ir.model.access"].sudo().create({
                    "name": "%s : lecture seule (essai)" % model,
                    "model_id": self.env["ir.model"]._get_id(model),
                    "group_id": self.env.ref("base.group_user").id,
                    "perm_read": True,
                    "perm_write": False,
                    "perm_create": False,
                    "perm_unlink": False,
                })
        return user

    def test_a_survey_user_without_cx_rights_cannot_resend(self):
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        with self.assertRaises(AccessError):
            answer.with_user(
                self._survey_user()
            ).action_bf_cx_resend_invite()

    def test_read_only_on_the_wave_is_not_enough_to_resend(self):
        """Le test qui mord vraiment.

        Sans droit du tout sur les vagues, la simple lecture d'un champ
        suffirait à refuser - le refus serait accidentel. Ce qu'on veut
        établir, c'est que LIRE une vague ne donne pas le droit d'en faire
        partir un courriel : la garde doit porter sur l'ÉCRITURE.
        """
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        user = self._survey_user(wave_perm_read=True)
        # La lecture, elle, passe : c'est bien l'écriture qui tranche.
        self.assertTrue(wave.with_user(user).state)
        with self.assertRaises(AccessError):
            answer.with_user(user).action_bf_cx_resend_invite()

    # ── L'entrée de test se renvoie aussi, et reste une entrée de test ───

    def test_resending_a_test_entry_keeps_it_a_test_entry(self):
        tester = self.env["res.partner"].create(
            {"name": "Contact Essai", "email": "essai-renvoi@example.com"}
        )
        self.Param.set_param("bf_cx.test_partner_id", str(tester.id))
        wave = self._sent_wave()
        with sending():
            wave.action_send_test()
        answer = wave.user_input_ids.filtered("test_entry")
        self.env["mail.blacklist"].sudo()._add(tester.email)
        with sending():
            answer.action_bf_cx_resend_invite()
        self.assertTrue(answer.test_entry)
        wave.invalidate_recordset()
        self.assertEqual(wave.invited_count, 2,
                         "l'essai renvoyé ne rejoint aucun compteur")

    # ── Un échec de livraison ne doit PAS s'annoncer en vert ─────────────

    def test_a_refused_delivery_is_not_announced_as_sent(self):
        """`send()` ne lève pas quand la livraison échoue.

        Il pose `state='exception'` et rend la main : sans contrôle, le
        bouton afficherait « Invitation renvoyée » en vert sur un courriel
        jamais parti, et personne ne chercherait plus.
        """
        wave = self._sent_wave()
        answer = self._answer_of(wave, self.client)
        before = len(wave.message_ids)
        with sending(_refused):
            with self.assertRaises(UserError):
                answer.action_bf_cx_resend_invite()
        wave.invalidate_recordset()
        self.assertEqual(len(wave.message_ids), before,
                         "aucune trace ne prétend qu'un courriel est parti")

    # ── La porte d'entrée existe pour de vrai dans l'interface ───────────

    def test_the_button_is_on_the_wave_form(self):
        """Une méthode sans bouton ne serait pas « via l'interface ».

        Le test vise l'arch RENDUE (celle que le navigateur reçoit), pas
        le fichier source : une vue héritée qui retirerait le bouton
        passerait inaperçue autrement.
        """
        arch = self.env["bf.cx.wave"].get_view(
            self.env.ref("bf_cx.view_cx_wave_form").id, "form"
        )["arch"]
        self.assertIn("action_bf_cx_resend_invite", arch)
