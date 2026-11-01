# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Plages sans ordre du jour ni tableau de bord."""

from datetime import datetime, timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_calendar_block.hooks import types_du_module
from odoo.addons.bf_calendar_block_meeting.hooks import post_init_hook


@tagged("post_install", "-at_install")
class TestDispenses(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, no_mail_to_attendees=True, skip_nc_sync=True))
        cls.Event = cls.env["calendar.event"]
        cls.types = types_du_module(cls.env)
        cls.prod = cls.env.ref("bf_calendar_block.block_production")
        cls.debut = fields.Datetime.now().replace(microsecond=0) + timedelta(days=3)

    def _creer(self, **vals):
        base = {"name": "Plage essai", "start": self.debut, "stop": self.debut + timedelta(hours=2)}
        base.update(vals)
        return self.Event.create(base)

    def test_quatre_types_dispenses(self):
        self.assertEqual(len(self.types), 4)
        for t in self.types:
            self.assertTrue(t.bf_skip_agenda and t.bf_skip_dashboard, t.name)

    def test_plage_neuve_dispensee(self):
        for vals in ({"bf_block_type_id": self.prod.id}, {"categ_ids": [Command.set(self.prod.ids)]}):
            plage = self._creer(**vals)
            self.assertTrue(plage.bf_skip_agenda and plage.bf_skip_dashboard, vals)

    def test_rencontre_ordinaire_pas_dispensee(self):
        rencontre = self._creer()
        self.assertFalse(rencontre.bf_skip_agenda or rencontre.bf_skip_dashboard)

    def test_crochet_aligne_le_futur_pas_le_passe(self):
        self.types.write({"bf_skip_agenda": False, "bf_skip_dashboard": False})
        futur = self._creer(bf_block_type_id=self.prod.id)
        passe = self._creer(bf_block_type_id=self.prod.id, start=datetime(2026, 1, 5, 14), stop=datetime(2026, 1, 5, 15))
        ordinaire = self._creer()
        self.assertFalse(futur.bf_skip_agenda)
        post_init_hook(self.env)
        self.assertTrue(all(self.types.mapped("bf_skip_agenda")))
        self.assertTrue(futur.bf_skip_agenda and futur.bf_skip_dashboard)
        self.assertFalse(passe.bf_skip_agenda or passe.bf_skip_dashboard, "le passé reste tel quel")
        self.assertFalse(ordinaire.bf_skip_agenda)

    def test_crochet_ne_decoche_rien(self):
        self.prod.bf_skip_dashboard = False
        self.prod.bf_skip_agenda = True
        post_init_hook(self.env)
        self.assertTrue(self.prod.bf_skip_dashboard)
        self.assertTrue(self.prod.bf_skip_agenda)
