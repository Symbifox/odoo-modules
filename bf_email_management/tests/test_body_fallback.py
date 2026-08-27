"""Le corps d'un courriel qu'Odoo s'est envoyé à lui-même.

Une soumission du formulaire du site, un digest de banque d'heures, un rapport
de matrice : tous partent d'un ``mail.mail`` créé **directement**, sans document
rattaché. ``mail.message.body`` reste alors vide et le texte ne vit que sur
``mail_mail.body_html``. Ces tests ancrent les trois replis et, surtout, le
garde-fou : un recalcul ne doit jamais reprendre un corps déjà capté.
"""

import base64
from email.message import EmailMessage

from odoo import fields
from odoo.tests import TransactionCase


def _rfc822(message_id, body_text):
    msg = EmailMessage()
    msg["Subject"] = "Migration Next Cloud"
    msg["From"] = '"formulaire" <bonjour@test.invalid>'
    msg["To"] = "info@test.invalid"
    msg["Message-ID"] = message_id
    msg.set_content(body_text)
    return msg.as_bytes()


class BodyFallbackCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Propriétaire Boîte",
                "login": "body.owner@test.invalid",
                "email": "info@test.invalid",
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            })
        cls.account = cls.env["bf.email.account"].create({
            "name": "Boîte de test",
            "user_id": cls.owner.id,
            "host": "imap.test.invalid",
            "port": 993,
            "login": "info@test.invalid",
            "password": "x",
            "state": "connected",
        })

    def _make_mail(self, body_html, message_id="<forme-site@test.invalid>"):
        """Un ``mail.mail`` direct : son ``mail.message`` naît SANS corps."""
        mail = self.env["mail.mail"].create({
            "subject": "Migration Next Cloud",
            "email_to": "info@test.invalid",
            "body_html": body_html,
            "message_id": message_id,
        })
        # Le postulat de tout le correctif. S'il tombe, c'est Odoo qui a changé.
        self.assertFalse(
            mail.mail_message_id.body,
            "mail.mail direct : mail.message.body devrait rester vide",
        )
        return mail

    def _row(self, **overrides):
        vals = {
            "subject": "Migration Next Cloud",
            "email_from": '"formulaire" <bonjour@test.invalid>',
            "direction": "in",
            "source": "chatter",
            "date": fields.Datetime.now(),
            "user_id": self.owner.id,
        }
        vals.update(overrides)
        return self.env["bf.email"].create(vals)

    def test_body_falls_back_to_mail_mail(self):
        """Le corps est lu sur le ``mail.mail`` quand le message est vide."""
        mail = self._make_mail("<div>Nous envisageons de migrer vers Next Cloud.</div>")
        row = self._row(mail_message_id=mail.mail_message_id.id)
        self.assertIn("migrer vers Next Cloud", row.body_html)
        self.assertIn("migrer vers Next Cloud", row.body_preview)

    def test_body_falls_back_to_raw_on_a_chatter_row(self):
        """Le repli RFC 2822 n'est plus réservé à ``source == "imap"``."""
        row = self._row(
            source="chatter",
            raw_rfc822=base64.b64encode(
                _rfc822("<brut@test.invalid>", "Le texte est dans la copie brute.")
            ).decode(),
        )
        self.assertIn("copie brute", row.body_html)

    def test_recompute_never_blanks_a_captured_body(self):
        """``auto_delete`` emporte le ``mail.mail`` : le corps doit rester."""
        mail = self._make_mail("<div>Corps à préserver</div>")
        row = self._row(mail_message_id=mail.mail_message_id.id)
        self.assertIn("Corps à préserver", row.body_html)
        row.flush_recordset(["body_html"])

        mail.unlink()  # emporte aussi le mail.message (_inherits)
        self.assertFalse(row.mail_message_id)

        row.invalidate_recordset(["body_html"])
        row._compute_body_html()
        self.assertIn(
            "Corps à préserver", row.body_html,
            "un recalcul a effacé un corps déjà stocké",
        )

    def test_ingest_keeps_raw_when_odoo_copy_has_no_body(self):
        """« L'interne gagne » ne doit pas jeter le seul exemplaire du texte."""
        message_id = "<ingest-sans-corps@test.invalid>"
        self._make_mail("<div>Corps du formulaire</div>", message_id=message_id)
        raw = _rfc822(message_id, "Corps du formulaire, version livrée.")

        created = self.env["bf.email"]._ingest_rfc822(raw, 24449, "INBOX", self.account)
        self.assertTrue(created)

        row = self.env["bf.email"].search([
            ("message_id_header", "=", message_id),
            ("user_id", "=", self.owner.id),
        ])
        self.assertEqual(len(row), 1)
        self.assertTrue(row.raw_rfc822, "la copie brute a été jetée")
        self.assertIn("Corps du formulaire", row.body_html)

    def test_ingest_drops_raw_when_odoo_copy_carries_the_body(self):
        """Le cas courant ne change pas : pas de RFC 2822 en double."""
        message_id = "<ingest-avec-corps@test.invalid>"
        self.env["mail.message"].create({
            "subject": "Note de projet",
            "body": "<p>Odoo a déjà le texte.</p>",
            "message_id": message_id,
            "message_type": "comment",
            "model": "res.partner",
            "res_id": self.env.user.partner_id.id,
        })
        raw = _rfc822(message_id, "Copie livrée.")

        self.env["bf.email"]._ingest_rfc822(raw, 24450, "INBOX", self.account)
        row = self.env["bf.email"].search([
            ("message_id_header", "=", message_id),
            ("user_id", "=", self.owner.id),
        ])
        self.assertEqual(len(row), 1)
        self.assertFalse(
            row.raw_rfc822,
            "stocker le RFC 2822 alors qu'Odoo porte déjà le corps double le "
            "stockage et déplace le régime de rétention",
        )
        self.assertIn("Odoo a déjà le texte", row.body_html)
