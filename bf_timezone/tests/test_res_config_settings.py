# Copyright 2026 Les Services de consultation Blue Fox Inc.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo.tests import TransactionCase, tagged

DEFAULT_TZ_PARAM = "bf_timezone.default_tz"


@tagged("bf_timezone", "bf_timezone_settings")
class TestResConfigSettings(TransactionCase):
    """The General Settings field that drives bf_timezone.default_tz."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Settings = cls.env["res.config.settings"]
        cls.ICP = cls.env["ir.config_parameter"].sudo()

    def test_setting_writes_param(self):
        """Saving the settings field persists the system parameter."""
        settings = self.Settings.create({"bf_default_tz": "Pacific/Auckland"})
        settings.execute()
        self.assertEqual(
            self.ICP.get_param(DEFAULT_TZ_PARAM), "Pacific/Auckland"
        )

    def test_setting_reads_back_param(self):
        """A fresh settings record reflects the stored parameter."""
        self.ICP.set_param(DEFAULT_TZ_PARAM, "Pacific/Auckland")
        settings = self.Settings.create({})
        self.assertEqual(settings.bf_default_tz, "Pacific/Auckland")

    def test_tz_selection_contains_key_zones(self):
        names = [code for code, _label in self.Settings._bf_tz_get()]
        self.assertIn("America/Toronto", names)
        self.assertIn("Pacific/Auckland", names)
