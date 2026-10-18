"""L'agenda prend la couleur du calendrier Nextcloud, pas l'index posé à la main."""
from datetime import datetime

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestCouleurCalendrier(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        cls.usager = new_test_user(cls.env, "nccol_usager", groups="base.group_user")
        cls.config = cls.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda personnel",
            "nextcloud_user": "essai",
            "nextcloud_base_url": "https://nc.test.invalid",
            "caldav_path": "/remote.php/dav/calendars/essai/perso/",
            "calendar_owner_id": cls.usager.id,
            "sync_direction": "both",
            # Mauve dans Nextcloud, rouge (index 1) dans Odoo : l'écart que bf_color corrige.
            "calendar_color": "#9500a8",
            "odoo_color": 1,
        })
        Event = cls.env["calendar.event"].with_context(skip_nc_sync=True, no_mail_to_attendees=True)
        cls.event = Event.create({
            "name": "Dans le calendrier",
            "start": datetime(2027, 3, 10, 15, 0), "stop": datetime(2027, 3, 10, 16, 0),
            "partner_ids": [Command.set(cls.usager.partner_id.ids)],
        })
        cls.event.with_context(skip_nc_sync=True).write({"x_nc_calendar_id": cls.config.id, "color": 1})
        cls.native = Event.create({
            "name": "Hors Nextcloud",
            "start": datetime(2027, 3, 11, 15, 0), "stop": datetime(2027, 3, 11, 16, 0),
            "partner_ids": [Command.set(cls.usager.partner_id.ids)],
        })
        # Un événement neuf peut recevoir d'office un calendrier (celui de son
        # organisateur) : on l'en retire pour jouer le cas hors Nextcloud.
        cls.native.with_context(skip_nc_sync=True).write({"color": 4, "x_nc_calendar_id": False})

    def test_le_hex_de_nextcloud_fait_foi(self):
        self.assertEqual(self.config.color_resolved, "#9500A8")
        self.assertEqual(self.event.color_resolved, "#9500A8")
        self.assertEqual(self.event.color_source, "rule")

    def test_la_couleur_de_la_personne_l_emporte(self):
        self.config.with_user(self.usager).bf_color_set_mine("#009E73")
        self.assertEqual(self.event.with_user(self.usager).color_resolved, "#009E73")
        self.assertEqual(self.event.color_resolved, "#9500A8")

    def test_un_evenement_hors_nextcloud_garde_son_index(self):
        self.assertEqual(self.native.color_source, "record")
        self.assertEqual(self.native.color_resolved, "#5794DD")

    def test_la_regle_par_defaut_est_livree(self):
        rule = self.env.ref("calendar_nextcloud_sync.bf_color_rule_event_nc_calendar")
        self.assertEqual(rule.field_id.name, "x_nc_calendar_id")
        self.assertFalse(rule.company_id)
        self.assertTrue(rule.value_colorable)
