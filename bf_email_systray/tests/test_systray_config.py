"""Le bouton de la barre, ce qu'il sait et ce qu'il montre.

Trois étages se vérifient séparément, parce qu'ils peuvent mentir chacun de
leur côté : ce que le serveur rend, ce que le formulaire de réglages AFFICHE,
et ce que le paquet d'actifs CHARGE. Un modèle juste avec un écran muet est un
défaut invisible en test de modèle.
"""

import os
import re

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSystrayConfig(TransactionCase):

    def _icp(self):
        return self.env["ir.config_parameter"].sudo()

    # ------------------------------------------------------------------
    # Ce que le serveur rend au bouton
    # ------------------------------------------------------------------

    def test_defaults_when_nothing_is_configured(self):
        """Une base qui n'a jamais ouvert les réglages ouvre en panneau."""
        self._icp().search([("key", "like", "bf_email_systray.%")]).unlink()
        cfg = self.env["bf.email"].systray_config()
        self.assertEqual(cfg, {"mode": "panneau",
                               "width_pct": 85, "height_pct": 85})

    def test_a_stored_choice_is_returned(self):
        self._icp().set_param("bf_email_systray.mode", "page")
        self._icp().set_param("bf_email_systray.width_pct", "60")
        cfg = self.env["bf.email"].systray_config()
        self.assertEqual(cfg["mode"], "page")
        self.assertEqual(cfg["width_pct"], 60)

    def test_an_impossible_mode_falls_back_instead_of_breaking(self):
        # Une valeur écrite à la main en base ne doit pas rendre le bouton
        # inerte : le JS ne connaît que deux modes.
        self._icp().set_param("bf_email_systray.mode", "plein-ecran")
        self.assertEqual(self.env["bf.email"].systray_config()["mode"],
                         "panneau")

    def test_sizes_are_clamped_both_ways(self):
        for stored, expected in (("5", 40), ("400", 100), ("", 85),
                                 ("quatre-vingts", 85), ("70", 70)):
            self._icp().set_param("bf_email_systray.width_pct", stored)
            self.assertEqual(
                self.env["bf.email"].systray_config()["width_pct"], expected,
                "largeur stockée %r" % stored)

    # ------------------------------------------------------------------
    # L'aller-retour par le formulaire de réglages
    # ------------------------------------------------------------------

    def test_settings_round_trip(self):
        settings = self.env["res.config.settings"].create({
            "bf_email_systray_mode": "page",
            "bf_email_systray_width_pct": 55,
            "bf_email_systray_height_pct": 90,
        })
        settings.set_values()
        cfg = self.env["bf.email"].systray_config()
        self.assertEqual(cfg, {"mode": "page",
                               "width_pct": 55, "height_pct": 90})
        # Et ce qui a été enregistré se relit tel quel dans le formulaire.
        values = self.env["res.config.settings"].get_values()
        self.assertEqual(values["bf_email_systray_mode"], "page")
        self.assertEqual(values["bf_email_systray_width_pct"], 55)

    def test_an_absurd_size_is_bounded_at_write_time(self):
        """Borner à la lecture ne suffit pas : le formulaire réafficherait
        la valeur aberrante telle quelle."""
        settings = self.env["res.config.settings"].create({
            "bf_email_systray_mode": "panneau",
            "bf_email_systray_width_pct": 5,
            "bf_email_systray_height_pct": 999,
        })
        settings.set_values()
        self.assertEqual(self._icp().get_param("bf_email_systray.width_pct"),
                         "40")
        self.assertEqual(self._icp().get_param("bf_email_systray.height_pct"),
                         "100")

    # ------------------------------------------------------------------
    # Ce que l'écran montre vraiment
    # ------------------------------------------------------------------

    def test_the_settings_form_actually_shows_the_fields(self):
        arch = self.env["res.config.settings"].get_view(
            self.env.ref(
                "bf_email_management.res_config_settings_view_form_bf_email"
            ).id, "form")["arch"]
        for field in ("bf_email_systray_mode",
                      "bf_email_systray_width_pct",
                      "bf_email_systray_height_pct"):
            self.assertIn(field, arch,
                          "%s absent du formulaire de réglages" % field)

    def test_the_asset_bundle_carries_the_panel(self):
        bundle = self.env["ir.qweb"]._get_asset_bundle(
            "web.assets_backend", assets_params={})
        urls = [f.url for f in bundle.javascripts] + \
               [f.url for f in bundle.stylesheets]
        self.assertTrue(
            any("bf_email_systray/static/src/js/bf_email_panel.js" in u
                for u in urls),
            "le panneau n'est pas dans le paquet d'actifs")
        self.assertTrue(
            any("bf_email_systray/static/src/scss/bf_email_systray.scss" in u
                or "bf_email_systray" in u and u.endswith(".css")
                for u in urls),
            "la feuille de style n'est pas dans le paquet d'actifs")

    # ------------------------------------------------------------------
    # La recopie du domaine, épinglée ici AUSSI
    # ------------------------------------------------------------------

    def test_the_badge_domain_is_still_the_python_one(self):
        """`bf_email_management` porte déjà ce contrôle, mais il se passe de
        ce module : sur une base où il n'est pas installé, le test s'y saute.
        Ici il ne peut pas se sauter."""
        path = os.path.join(
            get_module_path("bf_email_systray"),
            "static", "src", "js", "bf_email_systray.js")
        with open(path, encoding="utf-8") as fh:
            blob = re.sub(r"\s+", "", fh.read())
        for leaf in ('["is_handled","=",false]',
                     '["imap_in_inbox","=",true]',
                     '["source","in",["chatter","gateway"]]',
                     '["imap_folder","=",false]',
                     '["user_id","=",user.userId]'):
            self.assertIn(leaf, blob,
                          "le badge a divergé de bf.email._inbox_domain() : "
                          "il lui manque %s" % leaf)
