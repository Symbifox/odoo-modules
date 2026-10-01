"""The invitation, poke, change and cancellation emails use the shared mail layout.

Their QWeb bodies used to carry their own shell (background, card, a header with
the document title, a footer naming the calendar, bottom bars), and the composer
path wrapped that again in Odoo's own layout. The bodies now keep only their
content, the title becomes a small eyebrow, and every template points to
`bf_onboarding_base.bf_mail_layout`, which `bluefox_branding` replaces with its
own when installed. Shorthand styles the composer's sanitizer drops
(`background`, `border-left`, `border-right`) are written in their long forms.
The content keeps its original indentation: a multi-line term whose inner
whitespace changed would no longer match its msgid, and a fresh install would
send those paragraphs in English to French-speaking guests.
"""

import re

from odoo.modules.module import get_module_path
from odoo.tools.translate import PoFileReader

from odoo import Command
from odoo.tests import TransactionCase, tagged

LAYOUT = "bf_onboarding_base.bf_mail_layout"
# The shared layout's card (fallback copy and original alike): one card, not two.
CARD = "box-shadow:0 4px 24px"
# Only the old card had these (the shared layout has neither).
OLD_SHELL = ("border-radius:12px 12px 0 0", "border:1px solid #E5E7EB; border-collapse:collapse",
             "sent from the calendar of")
LIGHT = "utm_medium=email"


@tagged("post_install", "-at_install")
class TestMiseEnPage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organiser = cls.env["res.users"].create({
            "name": "Organiser", "login": "bf_layout_organiser",
            "email": "organiser@example.com"})
        cls.guest = cls.env["res.partner"].create({
            "name": "Guest One", "email": "guest.one@example.com"})
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.example.com")

    def _make_event(self):
        return self.env["calendar.event"].create({
            "name": "Project kickoff", "start": "2026-09-10 14:00:00",
            "stop": "2026-09-10 15:00:00", "user_id": self.organiser.id,
            "partner_ids": [Command.set([self.guest.id])]})

    def _mails(self, event):
        return self.env["mail.mail"].sudo().search(
            [("model", "=", "calendar.event"), ("res_id", "=", event.id)])

    def _one_layout(self, body):
        self.assertEqual(body.count(CARD), 1, "one card, the shared one")
        self.assertNotIn(LIGHT, body)
        for trace in OLD_SHELL:
            self.assertNotIn(trace, body)

    def test_every_template_points_to_the_shared_layout(self):
        templates = self.env["mail.template"].search([]).filtered(
            lambda t: t.get_external_id().get(t.id, "").startswith("bf_calendar_invite."))
        self.assertEqual(len(templates), 12)
        for template in templates:
            with self.subTest(template=template.get_external_id()[template.id]):
                self.assertEqual(template.email_layout_xmlid, LAYOUT)

    def test_the_bodies_carry_no_shell_and_no_shorthand(self):
        for xmlid in ("mail_body_calendar_invite", "mail_body_calendar_poke",
                      "mail_body_calendar_cancellation", "mail_body_calendar_change"):
            with self.subTest(view=xmlid):
                arch = self.env.ref("bf_calendar_invite." + xmlid).arch_db or ""
                self.assertNotIn("#F8FAFC", arch)
                self.assertNotIn('width="600"', arch)
                self.assertFalse(re.search(r"(?<![\w-])(background|border-left|border-right):", arch))

    def test_every_french_term_of_the_bodies_still_matches(self):
        """Odoo loads a view's translation by exact term: a msgid no longer in the
        view is silently dropped on a fresh install."""
        field = self.env["ir.ui.view"]._fields["arch_db"]
        path = get_module_path("bf_calendar_invite") + "/i18n/fr_CA.po"
        with open(path, "rb") as po:
            entries = [e for e in PoFileReader(po)
                       if e["type"] == "model_terms" and e["name"] == "ir.ui.view,arch_db"
                       and e["imd_name"].startswith("mail_body_calendar")]
        self.assertTrue(entries)
        terms = {}
        for entry in entries:
            name = entry["imd_name"]
            if name not in terms:
                view = self.env.ref("bf_calendar_invite." + name).with_context(lang="en_US")
                terms[name] = set(field.get_trans_terms(view.arch_db))
            with self.subTest(view=name, msgid=entry["src"][:60]):
                self.assertIn(entry["src"], terms[name])

    def test_the_change_notice_greets_in_french(self):
        """The change notice (6.0.0) was never listed on the shared terms of the
        other three bodies: French guests were greeted with "Hello" and "Thanks"."""
        if not self.env["res.lang"]._lang_get("fr_CA"):
            self.skipTest("fr_CA is not installed")
        field = self.env["ir.ui.view"]._fields["arch_db"]
        view = self.env.ref("bf_calendar_invite.mail_body_calendar_change")
        terms = field.get_trans_terms(view.with_context(lang="fr_CA").arch_db)
        for english in ("Hello", "Hello,", "Thanks,<br/>", "Join the meeting", "Meeting"):
            with self.subTest(term=english):
                self.assertNotIn(english, terms)

    def test_the_cancellation_notice_through_send_mail(self):
        event = self._make_event()
        event._bf_cancel(notify=True)
        mail = self._mails(event)
        self.assertEqual(len(mail), 1)
        self._one_layout(mail.body_html)

    def test_the_invitation_through_the_composer(self):
        event = self._make_event()
        action = event.action_open_composer()
        composer = self.env["mail.compose.message"].with_context(
            **action["context"], mail_notify_force_send=False).create({})
        self.assertEqual(composer.email_layout_xmlid, LAYOUT)
        composer.partner_ids = [Command.set(self.guest.ids)]
        composer.action_send_mail()
        mails = self._mails(event)
        self.assertTrue(mails)
        for body in mails.mapped("body_html"):
            self._one_layout(body)
            self.assertIn(">Invitation</p>", body)
