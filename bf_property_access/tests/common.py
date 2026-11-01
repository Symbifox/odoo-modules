from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase, new_test_user


class AccessCase(TransactionCase):
    """Un syndicat, un immeuble, deux fractions, un gestionnaire, un concierge."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Un seul fuseau pour tout le monde : sinon, après 20 h à Montréal, le
        # compte système des essais (Europe ou UTC) est déjà au lendemain et un
        # départ « aujourd'hui » devient un départ futur pour lui.
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="America/Toronto"))
        cls.env.user.tz = "America/Toronto"
        cls.today = fields.Date.context_today(cls.env["bf.property.access.resident"])
        cls.manager = new_test_user(
            cls.env, login="acces_gestion", name="Gestionnaire (banc)",
            groups="base.group_user,bf_property_core.group_bf_property_manager",
            tz="America/Toronto",
        )
        cls.concierge = new_test_user(
            cls.env, login="acces_concierge", name="Concierge (banc)",
            groups="base.group_user,bf_property_core.group_bf_property_user",
            tz="America/Toronto",
        )
        cls.org = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat (banc)", "kind": "syndicat"})
        cls.building = cls.env["bf.property.building"].create({
            "name": "Immeuble (banc)", "organisation_id": cls.org.id,
            "access_user_id": cls.manager.id,
        })
        cls.unit1 = cls.env["bf.property.unit"].create(
            {"name": "101", "building_id": cls.building.id})
        cls.unit2 = cls.env["bf.property.unit"].create(
            {"name": "102", "building_id": cls.building.id})
        cls.alice = cls.env["res.partner"].create({"name": "Alice Exemple"})
        cls.bruno = cls.env["res.partner"].create({"name": "Bruno Exemple"})
        cls.chloe = cls.env["res.partner"].create({"name": "Chloé Exemple"})
        cls.Resident = cls.env["bf.property.access.resident"]
        cls.Point = cls.env["bf.property.access.point"]

    def days(self, n):
        return self.today + relativedelta(days=n)

    def make_point(self, **kw):
        vals = {"name": "Portier nord", "building_id": self.building.id,
                "point_type": "entrance", "user_id": self.concierge.id}
        vals.update(kw)
        return self.Point.create(vals)

    def own(self, partner, unit, start=None, end=None):
        return self.env["bf.property.ownership"].create({
            "partner_id": partner.id, "unit_id": unit.id,
            "date_start": start or self.days(-400), "date_end": end,
        })

    def resident(self, partner, unit=None):
        return self.Resident.search([
            ("partner_id", "=", partner.id),
            ("building_id", "=", self.building.id),
            ("unit_id", "=", unit.id if unit else False),
        ], order="id desc", limit=1)

    def sync(self):
        return self.Resident.sudo()._bf_sync()
