from odoo.tests import tagged

from ..hooks import (
    PARAM_HIDDEN_MENUS,
    hide_third_party_menus,
    restore_third_party_menus,
)
from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestThirdPartyMenus(BfBudgetCommon):
    """Le crochet doit se comporter dans les DEUX cas.

    Sur une base où un greffon budgétaire tiers est présent, et sur une base où
    il n'y en a aucun, ce qui est le cas de la quasi-totalité des installations.
    """

    def _fake_third_party_menu(self):
        menu = self.env["ir.ui.menu"].create({"name": "Budgets (greffon tiers)"})
        self.env["ir.model.data"].create(
            {
                "module": "om_account_budget",
                "name": "menu_fake_budget_%s" % menu.id,
                "model": "ir.ui.menu",
                "res_id": menu.id,
            }
        )
        return menu

    def test_no_third_party_module_is_not_an_error(self):
        hidden = hide_third_party_menus(self.env)
        self.assertFalse(hidden)
        self.assertFalse(
            self.env["ir.config_parameter"].sudo().get_param(PARAM_HIDDEN_MENUS)
        )

    def test_third_party_menu_is_hidden_then_restored(self):
        menu = self._fake_third_party_menu()
        self.assertTrue(menu.active)
        hidden = hide_third_party_menus(self.env)
        self.assertIn(menu, hidden)
        self.assertFalse(menu.active)
        restore_third_party_menus(self.env)
        self.assertTrue(menu.active)

    def test_only_our_own_menus_come_back(self):
        """Un menu tiers que quelqu'un d'autre a éteint reste éteint.

        Le crochet ne rallume que ce qu'il a lui-même éteint : sinon la
        désinstallation ferait réapparaître un menu que l'utilisateur avait
        volontairement masqué.
        """
        already_off = self._fake_third_party_menu()
        already_off.active = False
        ours = self._fake_third_party_menu()
        hide_third_party_menus(self.env)
        self.assertFalse(ours.active)
        restore_third_party_menus(self.env)
        self.assertTrue(ours.active)
        self.assertFalse(already_off.active)

    def test_setting_applies_immediately_both_ways(self):
        menu = self._fake_third_party_menu()
        settings = self.env["res.config.settings"].create(
            {"bf_budget_hide_thirdparty_menus": True}
        )
        settings.set_values()
        self.assertFalse(menu.active)
        settings = self.env["res.config.settings"].create(
            {"bf_budget_hide_thirdparty_menus": False}
        )
        settings.set_values()
        self.assertTrue(menu.active)
