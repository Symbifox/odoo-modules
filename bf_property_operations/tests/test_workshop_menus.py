"""Les deux menus d'atelier, et le défaut qui ne confisque pas.

Deux menus de `maintenance` qui viennent de l'atelier et non de
l'immeuble : Overall Equipment Effectiveness et Losses Analysis. Ils mesurent la
performance d'un équipement industriel ; un syndicat n'a aucune production à
mesurer.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestWorkshopMenus(TransactionCase):

    MENUS = ("maintenance.menu_m_reports_oee",
             "maintenance.menu_m_reports_losses")

    def test_the_workshop_reports_are_hidden(self):
        for xmlid in self.MENUS:
            menu = self.env.ref(xmlid)
            self.assertFalse(
                menu.active,
                f"{xmlid} est encore visible : le concierge voit un rapport de "
                f"performance d'équipement industriel dans son menu Entretien.",
            )

    def test_the_building_menus_are_untouched(self):
        """⚠️ Le pendant. Sans lui, couper tout le menu Entretien passerait au
        vert sur le test précédent — et priverait le concierge de ce pour quoi
        le module existe."""
        for xmlid in ("maintenance.menu_m_request",
                      "maintenance.menu_equipment_form",
                      "maintenance.menu_maintenance_title",
                      "maintenance.menu_m_reports"):
            self.assertTrue(self.env.ref(xmlid).active, xmlid)

    def test_no_data_file_rewrites_those_menus(self):
        """🔴 C'est CE test qui tient la réversibilité, et il garde une absence.

        Un `<record>` dans un fichier de données du module réécrirait les deux
        menus à chaque `-u`, et l'administrateur qui les a réactivés les verrait
        disparaître sans comprendre pourquoi. Le geste se fait donc au crochet
        d'installation, qui joue une fois et ne rejoue pas.

        ⚠️ L'autre forme XML ne marche pas davantage : `noupdate="1"` sur le
        xmlid d'un AUTRE module n'écrit jamais rien, l'enregistrement existant
        déjà. Mesuré au banc — le fichier avait l'air juste et ne faisait rien.
        """
        data = self.env["ir.model.data"].search([
            ("module", "=", "bf_property_operations"),
            ("model", "=", "ir.ui.menu"),
        ])
        touched = data.filtered(lambda d: "reports" in d.name)
        self.assertFalse(
            touched,
            "Un fichier de données du module vise les menus d'atelier : il les "
            "réécrirait à chaque mise à jour, et reprendrait à "
            "l'administrateur un choix qu'on lui a laissé.",
        )

    def test_the_module_declares_the_hook(self):
        """Le mécanisme se vérifie, pas seulement son effet du jour."""
        module = self.env["ir.module.module"].search(
            [("name", "=", "bf_property_operations")], limit=1)
        self.assertTrue(module)
        from odoo.modules.module import get_manifest
        self.assertEqual(
            get_manifest("bf_property_operations").get("post_init_hook"),
            "post_init_hook",
        )
