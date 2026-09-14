# -*- coding: utf-8 -*-
"""The daily digest is written in each recipient's language.

It was assembled in hard-coded French, for everybody, and branded Blue Fox on
every database. It now renders in the recipient's language and carries the
recipient company's name.
"""

from unittest.mock import patch

from odoo.fields import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDigestLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(["daily_todo_digest"], ["fr_CA"], overwrite=True)
        cls.env.company.name = "Digest Language Co"
        cls.francoise = cls.env["res.users"].create({
            "name": "Françoise Digest", "login": "francoise.digest@example.com",
            "email": "francoise.digest@example.com", "lang": "fr_CA",
        })
        cls.jane = cls.env["res.users"].create({
            "name": "Jane Digest", "login": "jane.digest@example.com",
            "email": "jane.digest@example.com", "lang": "en_US",
        })
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Language digest",
            "user_ids": [Command.set([cls.francoise.id, cls.jane.id])],
            # The quote alone is enough to send; the weather would go to the network.
            "include_weather": False, "include_quote": True, "include_meetings": False,
        })

    def _send(self, **kw):
        # Kept in the queue: sent mail is auto-deleted, and there would be nothing to read.
        with patch.object(type(self.env["mail.mail"]), "send", lambda self, *a, **k: True):
            self.config.with_context({})._send_digest(**kw)

    def _mail_for(self, user):
        return self.env["mail.mail"].search([("email_to", "=", user.email)], order="id desc", limit=1)

    def test_each_recipient_reads_their_language(self):
        """🔴 Sent from a context with no language, as the scheduled job does."""
        self._send()
        francais, anglais = self._mail_for(self.francoise), self._mail_for(self.jane)
        self.assertTrue(francais and anglais)
        self.assertIn("Votre journée", francais.subject)
        self.assertIn("Bonjour", francais.body_html)
        self.assertIn("Your day", anglais.subject)
        self.assertIn("Hello", anglais.body_html)
        for mot in ("Bonjour", "Voici", "Aperçu", "journée"):
            self.assertNotIn(mot, anglais.body_html)
        for mot in ("Hello", "Here is", "at a glance"):
            self.assertNotIn(mot, francais.body_html)

    def test_the_footer_names_the_company_not_blue_fox(self):
        self._send(test_user=self.jane)
        corps = self._mail_for(self.jane).body_html
        self.assertIn("Digest Language Co", corps)
        self.assertNotIn("bluefoxconsultant.com", corps)
        self.assertNotIn("service@example.com", corps)
        self.assertNotIn("Blue Fox", corps)
        self.assertIn(">Digest Language Co</strong>", corps)

    def test_a_shipped_quote_reads_in_both_languages(self):
        citation = self.env.ref("daily_todo_digest.quote_01")
        self.assertEqual(citation.with_context(lang="en_US").quote, "Property is theft.")
        self.assertEqual(citation.with_context(lang="fr_CA").quote, "La propriété, c'est le vol.")
