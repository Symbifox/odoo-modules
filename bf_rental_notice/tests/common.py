"""Décor commun aux essais des avis."""
from odoo.tests.common import TransactionCase


class NoticeCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.landlord = cls.env["bf.property.organisation"].create(
            {"name": "Avis inc.", "kind": "landlord"})
        cls.building = cls.env["bf.property.building"].create(
            {"name": "77 Saint-Denis", "organisation_id": cls.landlord.id})
        cls.tenant = cls.env["res.partner"].create({"name": "Locataire Avis"})

    def _lease(self, **kw):
        vals = {
            "organisation_id": self.landlord.id,
            "building_id": self.building.id,
            "tenant_ids": [(6, 0, [self.tenant.id])],
            "duration_kind": "fixed",
            "date_start": "2026-07-01",
            "date_end": "2027-06-30",
            "rent": 1100.0,
        }
        vals.update(kw)
        return self.env["bf.rental.lease"].create(vals)

    def _notice(self, lease=None, **kw):
        vals = {
            "lease_id": (lease or self._lease()).id,
            "kind": "modification",
            "date_given": "2027-02-01",
            "target_date": "2027-06-30",
        }
        vals.update(kw)
        return self.env["bf.rental.notice"].create(vals)
