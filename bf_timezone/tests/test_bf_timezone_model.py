# Copyright 2026 Les Services de consultation Blue Fox Inc.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from datetime import datetime

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_timezone.tools import tz

DEFAULT_TZ_PARAM = "bf_timezone.default_tz"


@tagged("bf_timezone", "bf_timezone_model")
class TestBfTimezoneModel(TransactionCase):
    """The bf.timezone AbstractModel: env-aware façade adding the configurable
    default. All writes here roll back with the test transaction."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Tz = cls.env["bf.timezone"]
        cls.ICP = cls.env["ir.config_parameter"].sudo()

    def test_default_tz_reads_param(self):
        self.ICP.set_param(DEFAULT_TZ_PARAM, "Pacific/Auckland")
        self.assertEqual(self.Tz.default_tz(), "Pacific/Auckland")

    def test_default_tz_unset_falls_back_to_tools_default(self):
        """No param -> the hard tools default (America/Toronto)."""
        param = self.ICP.search([("key", "=", DEFAULT_TZ_PARAM)])
        param.unlink()
        self.assertEqual(self.Tz.default_tz(), tz.DEFAULT_TZ)
        self.assertEqual(self.Tz.default_tz(), "America/Toronto")

    def test_resolve_uses_configured_default(self):
        """resolve([]) follows the configured default, not the hard default."""
        self.ICP.set_param(DEFAULT_TZ_PARAM, "Pacific/Auckland")
        self.assertEqual(self.Tz.resolve([]), "Pacific/Auckland")
        self.assertEqual(self.Tz.resolve([None, ""]), "Pacific/Auckland")

    def test_resolve_explicit_fallback_overrides_default(self):
        self.ICP.set_param(DEFAULT_TZ_PARAM, "Pacific/Auckland")
        self.assertEqual(self.Tz.resolve([], fallback="UTC"), "UTC")

    def test_resolve_validate_through_model(self):
        self.assertEqual(
            self.Tz.resolve(["Bogus/Zone", "America/Toronto"], validate=True),
            "America/Toronto",
        )

    def test_wrappers_delegate_to_tools(self):
        """Model methods return exactly what the pure helpers return."""
        self.assertEqual(
            self.Tz.normalize_name("Eastern Standard Time"),
            tz.normalize_name("Eastern Standard Time"),
        )
        self.assertEqual(self.Tz.tz_city("America/Toronto"), tz.tz_city("America/Toronto"))
        dt = datetime(2026, 5, 20, 13, 40, 0)
        self.assertEqual(
            self.Tz.to_tz(dt, "Pacific/Auckland"),
            tz.to_tz(dt, "Pacific/Auckland"),
        )
        self.assertEqual(
            self.Tz.format_dual(dt, "America/Toronto", "Pacific/Auckland"),
            tz.format_dual(dt, "America/Toronto", "Pacific/Auckland"),
        )
