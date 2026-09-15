from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase


class PersonaCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Partner = cls.env["res.partner"]
        cls.Persona = cls.env["contact.persona"]
        cls.Message = cls.env["mail.message"]
        cls.me = cls.env.ref("base.user_admin").partner_id
        cls.comment = cls.env.ref("mail.mt_comment")
        cls.client = cls.Partner.create({"name": "Exemple Inc.", "is_company": True})
        cls.eli = cls.Partner.create({
            "name": "Ada Exemple", "email": "ada@exemple.example", "parent_id": cls.client.id,
        })
        cls.jul = cls.Partner.create({
            "name": "Bruno Exemple", "email": "bruno@exemple.example", "parent_id": cls.client.id,
        })
        cls.hm = cls.Partner.create({"name": "Clara Exemple", "email": "clara@exemple.example"})

    def days_ago(self, n):
        return fields.Datetime.now() - timedelta(days=n)

    def sent(self, to, body="<p>x</p>", days=1, cc=None):
        """A message we wrote from the composer."""
        vals = {
            "model": "res.partner", "res_id": to[0].id,
            "message_type": "comment", "subtype_id": self.comment.id,
            "author_id": self.me.id, "body": body,
            "partner_ids": [(6, 0, [p.id for p in to])],
            "date": self.days_ago(days),
        }
        if cc:
            vals["recipient_cc_ids"] = [(6, 0, [p.id for p in cc])]
        return self.Message.create(vals)

    def received(self, author, body="<p>x</p>", days=1):
        return self.Message.create({
            "model": "res.partner", "res_id": author.id,
            "message_type": "email", "author_id": author.id, "body": body,
            "date": self.days_ago(days),
        })

    def composer(self, to=(), cc=(), bcc=(), body="<p>x</p>", model="res.partner", res_id=None):
        res_id = res_id or (to[0].id if to else self.client.id)
        return self.env["mail.compose.message"].create({
            "composition_mode": "comment",
            "model": model,
            "res_ids": str([res_id]),
            "partner_ids": [(6, 0, [p.id for p in to])],
            "partner_cc_ids": [(6, 0, [p.id for p in cc])],
            "partner_bcc_ids": [(6, 0, [p.id for p in bcc])],
            "body": body,
        })
