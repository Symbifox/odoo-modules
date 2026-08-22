"""L'avis de nouvelle réponse.

Ce qu'il faut pincer ici, c'est le cas que le reste du module ne couvre pas :
un PROMOTEUR ne déclenche aucune activité et n'apparaît pas dans le digest,
donc si l'avis ne part pas pour lui, la fonctionnalité ne sert à rien tout en
ayant l'air de marcher. Le reste des assertions garde l'interrupteur honnête :
fermé, rien ne bouge.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCxNotifyResponse(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Param = cls.env["ir.config_parameter"].sudo()
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        cls.program.cooldown_days = 0
        cls.Param.set_param("bf_cx.solicitation_cooldown_days", "0")
        cls.partner = cls.env["res.partner"].create(
            {"name": "Client Avis", "email": "avis@example.com"}
        )

    def _answer(self, score, comment="Rien à signaler"):
        wave = self.env["bf.cx.wave"].create({
            "name": "Vague avis",
            "program_id": self.program.id,
            "partner_ids": [(6, 0, self.partner.ids)],
        })
        wave.action_send()
        answer = wave.user_input_ids.filtered(lambda a: not a.test_entry)
        answer._save_lines(self.program.score_question_id, str(score))
        if comment:
            answer._save_lines(self.program.comment_question_id, comment)
        answer._mark_done()
        return self.env["bf.cx.feedback"].search(
            [("survey_user_input_id", "=", answer.id)]
        )

    def _notices(self, feedback):
        """Messages adressés à quelqu'un, c'est-à-dire qui notifient.

        ⚠️ On vérifie ici que le destinataire est bien NOMMÉ sur le message,
        pas que le courriel est parti. Odoo diffère la notification à un
        crochet post-commit : dans une transaction annulée (test, ou
        `odoo shell` terminé par un rollback) `notification_ids` et
        `mail.mail` restent VIDES même quand tout fonctionne. Lire ces
        compteurs sans commiter fait conclure à tort que rien ne part.
        La patte livraison a été prouvée séparément, sur un banc neutralisé
        avec un `env.cr.commit()` explicite : 1 notification de type courriel
        et 1 `mail.mail` adressé au responsable.
        """
        return self.env["mail.message"].search([
            ("model", "=", "bf.cx.feedback"),
            ("res_id", "=", feedback.id),
            ("partner_ids", "!=", False),
        ])

    # ── Interrupteur fermé ───────────────────────────────────────────────

    def test_switch_closed_notifies_nobody(self):
        self.Param.set_param("bf_cx.notify_every_response", "False")
        fb = self._answer(9)
        self.assertTrue(fb, "le feedback doit exister")
        self.assertFalse(self._notices(fb), "aucun avis quand c'est fermé")

    def test_absent_parameter_reads_as_closed(self):
        self.Param.search([("key", "=", "bf_cx.notify_every_response")]).unlink()
        fb = self._answer(9)
        self.assertFalse(self._notices(fb))

    # ── Interrupteur ouvert ──────────────────────────────────────────────

    def test_a_promoter_is_announced(self):
        """Le cas qui justifie la fonctionnalité : sans elle, silence total."""
        self.Param.set_param("bf_cx.notify_every_response", "True")
        fb = self._answer(10, comment="Service impeccable")
        avis = self._notices(fb)
        self.assertEqual(len(avis), 1, "un avis, et un seul")
        corps = avis.body
        self.assertIn("Client Avis", corps)
        self.assertIn("10", corps)
        self.assertIn("Service impeccable", corps, "le verbatim doit y être")
        self.assertNotIn(
            "activité", corps.lower(),
            "un promoteur ne crée aucune activité, ne pas le prétendre",
        )
        self.assertIn(
            self.program.user_id.partner_id, avis.partner_ids,
            "le responsable du programme doit être nommé destinataire",
        )

    def test_a_detractor_is_announced_and_says_so(self):
        self.Param.set_param("bf_cx.notify_every_response", "True")
        fb = self._answer(2, comment="Trop lent")
        avis = self._notices(fb)
        self.assertEqual(len(avis), 1)
        self.assertIn("activité", avis.body.lower(),
                      "l'avis doit mentionner l'activité créée")
        self.assertTrue(
            self.env["mail.activity"].search([
                ("res_model", "=", "bf.cx.feedback"), ("res_id", "=", fb.id)
            ]),
            "la boucle fermée reste indépendante de l'avis",
        )

    def test_verbatim_is_escaped_not_injected(self):
        """Un commentaire client est du texte, jamais du balisage."""
        self.Param.set_param("bf_cx.notify_every_response", "True")
        fb = self._answer(8, comment="<script>alert(1)</script> & <b>gras</b>")
        corps = self._notices(fb).body
        self.assertNotIn("<script>", corps)
        self.assertIn("&lt;script&gt;", corps)

    def test_a_test_entry_never_notifies(self):
        """Une entrée d'essai n'entre pas au registre, donc n'avertit personne."""
        self.Param.set_param("bf_cx.notify_every_response", "True")
        self.Param.set_param("bf_cx.test_partner_id", str(self.partner.id))
        wave = self.env["bf.cx.wave"].create({
            "name": "Vague avis (essai)",
            "program_id": self.program.id,
            "partner_ids": [(6, 0, self.partner.ids)],
        })
        from unittest.mock import patch
        with patch("odoo.addons.mail.models.mail_mail.MailMail.send"):
            wave.action_send_test()
        answer = wave.user_input_ids.filtered("test_entry")
        answer._save_lines(self.program.score_question_id, "10")
        answer._mark_done()
        self.assertFalse(
            self.env["bf.cx.feedback"].search(
                [("survey_user_input_id", "=", answer.id)]
            ),
            "pas de feedback, donc pas d'avis",
        )
