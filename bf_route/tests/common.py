"""Shared set-up: real Worker and Manager accounts (tests run as superuser otherwise,
which hides every missing sudo and every missing rule)."""
from datetime import date

from odoo.tests import TransactionCase, new_test_user

MONDAY = date(2026, 10, 12)


class RouteCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The messages are checked in English: the base may be French only.
        cls.env["res.lang"]._activate_lang("en_US")
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_reset_password=True, lang="en_US"))
        cls.company = cls.env.company
        cls.company.write({"bf_route_position_mode": "off"})
        cls.manager = new_test_user(cls.env, login="route_manager", tz="America/Toronto", lang="en_US",
                                    groups="base.group_user,bf_route.group_route_manager")
        cls.worker = new_test_user(cls.env, login="route_worker", tz="America/Toronto", lang="en_US",
                                   groups="base.group_user,bf_route.group_route_user")
        cls.other = new_test_user(cls.env, login="route_other", tz="America/Toronto", lang="en_US",
                                  groups="base.group_user,bf_route.group_route_user")
        Partner = cls.env["res.partner"]
        cls.depot = Partner.create({"name": "Depot", "partner_latitude": 45.5017,
                                    "partner_longitude": -73.5673})
        cls.alice = Partner.create({"name": "Alice Café", "street": "1 rue A", "city": "Montréal",
                                    "partner_latitude": 45.52, "partner_longitude": -73.58,
                                    "phone": "+1 514 555 0101"})
        cls.bob = Partner.create({"name": "Bob Garage", "partner_latitude": 45.53,
                                  "partner_longitude": -73.60})
        cls.carol = Partner.create({"name": "Carol Clinic", "partner_latitude": 45.49,
                                    "partner_longitude": -73.55})
        brand = cls.env["fleet.vehicle.model.brand"].create({"name": "Brand"})
        model = cls.env["fleet.vehicle.model"].create({"name": "Truck", "brand_id": brand.id})
        cls.truck = cls.env["fleet.vehicle"].create({"model_id": model.id, "license_plate": "ABC 123"})
        cls.route = cls.env["bf.route"].with_user(cls.manager).create({
            "name": "North shore",
            "responsible_id": cls.manager.id,
            "user_id": cls.worker.id,
            "vehicle_id": cls.truck.id,
            "recurrence": "weekly",
            "mon": True, "wed": False,
            "date_start": MONDAY,
            "start_hour": 8.0,
            "start_partner_id": cls.depot.id,
            "end_partner_id": cls.depot.id,
            "days_ahead": 7,
            "stop_ids": [
                (0, 0, {"sequence": 10, "partner_id": cls.alice.id, "window_start": 9.0,
                        "window_end": 11.0, "instructions": "Back door"}),
                (0, 0, {"sequence": 20, "partner_id": cls.bob.id}),
                (0, 0, {"sequence": 30, "partner_id": cls.carol.id}),
            ],
        })

    def make_day(self, day=MONDAY, route=None):
        route = route or self.route
        return self.env["bf.route.day"].with_user(self.manager).with_context(
            bf_route_copy_stops=True).create({"route_id": route.id, "date": day})

    def start(self, day, **kwargs):
        return day.with_user(self.worker).app_start(**kwargs)
