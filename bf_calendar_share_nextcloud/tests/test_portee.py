# -*- coding: utf-8 -*-
"""Portée d'un lien de partage par agenda Nextcloud."""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "bf_calendar_share_nextcloud")
class TestPortee(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       no_mail_to_attendees=True, skip_nc_sync=True))
        cls.proprio = new_test_user(cls.env, login="proprio_portee", groups="base.group_user")
        Config = cls.env["nextcloud.calendar.sync.config"]
        base = {"backend_type": "nextcloud", "nextcloud_base_url": "https://nc.example.test",
                "nextcloud_user": "o", "nextcloud_app_password": "x", "sync_direction": "both"}
        cls.travail = Config.create(dict(base, name="Travail t", caldav_path="/remote.php/dav/calendars/o/travail/",
                                    bf_share_default=True))
        cls.perso = Config.create(dict(base, name="Perso t", caldav_path="/remote.php/dav/calendars/o/perso/"))
        demain = fields.Datetime.now() + timedelta(days=1)
        E = cls.env["calendar.event"].with_user(cls.proprio)
        cls.ev_travail = E.create({"name": "Rencontre travail", "start": demain, "stop": demain + timedelta(hours=1),
                              "x_nc_calendar_id": cls.travail.id})
        cls.ev_perso = E.create({"name": "Dentiste", "start": demain, "stop": demain + timedelta(hours=1),
                                 "x_nc_calendar_id": cls.perso.id})
        cls.ev_odoo = E.create({"name": "Née dans Odoo", "start": demain, "stop": demain + timedelta(hours=1)})

    def _lien(self, **vals):
        return self.env["bf.calendar.share.link"].with_user(self.proprio).create(
            dict({"name": "Test", "level": "titles"}, **vals))

    def test_defaut_blue_fox_et_odoo(self):
        lien = self._lien()
        self.assertEqual(lien.nc_config_ids, self.travail)
        corps = lien._bf_ics()
        self.assertIn("Rencontre travail", corps)
        self.assertIn("Née dans Odoo", corps)
        self.assertNotIn("Dentiste", corps)

    def test_sans_odoo_seul(self):
        corps = self._lien(include_odoo_only=False)._bf_ics()
        self.assertIn("Rencontre travail", corps)
        self.assertNotIn("Née dans Odoo", corps)

    def test_aucun_agenda_coche_tout(self):
        corps = self._lien(nc_config_ids=[(5, 0, 0)])._bf_ics()
        for nom in ("Rencontre travail", "Dentiste", "Née dans Odoo"):
            self.assertIn(nom, corps)
