"""Décor commun aux essais du loyer."""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class RentCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.landlord = cls.env["bf.property.organisation"].create(
            {"name": "Loyer inc.", "kind": "landlord"})
        cls.building = cls.env["bf.property.building"].create(
            {"name": "12 Rachel", "organisation_id": cls.landlord.id})
        cls.tenant = cls.env["res.partner"].create({"name": "Dominique Roy"})
        cls.today = fields.Date.context_today(cls.env["bf.rental.lease"])

    def _lease(self, **kw):
        vals = {
            "organisation_id": self.landlord.id,
            "building_id": self.building.id,
            "tenant_ids": [(6, 0, [self.tenant.id])],
            "duration_kind": "fixed",
            "date_start": "2026-07-01",
            "date_end": "2027-06-30",
            "rent": 1000.0,
        }
        vals.update(kw)
        return self.env["bf.rental.lease"].create(vals)

    def _term(self, lease=None, days_ago=0, amount=1000.0, **kw):
        vals = {
            "lease_id": (lease or self._lease()).id,
            "date_due": self.today - relativedelta(days=days_ago),
            "amount_due": amount,
        }
        vals.update(kw)
        return self.env["bf.rental.term"].create(vals)

    def _pay(self, term, amount):
        return self.env["bf.rental.payment"].create({
            "term_id": term.id, "amount": amount, "date": self.today,
        })
