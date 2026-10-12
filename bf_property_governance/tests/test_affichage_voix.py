"""Les voix s'affichent sans zéros de fin : « 585 voix », jamais « 585,0000 voix ».

Relevé par la campagne de captures du 2026-10-10. Les voix gardent quatre
décimales en base (quotes-parts, comparaisons de majorité), mais le widget float
d'Odoo remet les zéros de fin jusqu'à la précision du champ. Le widget
`bf_property_votes` les retire ; cet essai garde qu'aucune vue du module ne
l'oublie sur un champ de voix, y compris un champ ou une vue ajoutés plus tard.
"""
from lxml import etree

from odoo.tests.common import TransactionCase, tagged

WIDGET = "bf_property_votes"


@tagged("post_install", "-at_install")
class TestAffichageVoix(TransactionCase):

    def _champs_a_quatre_decimales(self, model):
        champs = set()
        for name, field in self.env[model]._fields.items():
            if field.type != "float":
                continue
            digits = field.get_digits(self.env)
            if digits and digits[1] == 4:
                champs.add(name)
        return champs

    def test_every_vote_field_in_the_module_views_wears_the_widget(self):
        vues = self.env["ir.ui.view"].search([
            ("model", "like", "bf.property."),
            ("type", "in", ("form", "list")),
        ]).filtered(lambda v: v.xml_id.startswith("bf_property_governance."))
        self.assertTrue(vues, "aucune vue du module trouvée")
        vus, oublis = 0, []
        for vue in vues:
            arch = etree.fromstring(vue.arch_db.encode())
            # Une liste imbriquée (one2many) parle d'un autre modèle que la vue.
            for node in arch.iter("field"):
                modele = vue.model
                parent = node.getparent()
                while parent is not None:
                    if parent.tag == "field" and parent.getparent() is not None:
                        rel = self.env[modele]._fields.get(parent.get("name"))
                        if rel is not None and rel.comodel_name:
                            modele = rel.comodel_name
                        break
                    parent = parent.getparent()
                if node.get("name") not in self._champs_a_quatre_decimales(modele):
                    continue
                vus += 1
                if node.get("widget") != WIDGET:
                    oublis.append("%s : %s" % (vue.xml_id, node.get("name")))
        self.assertGreater(vus, 20, "les champs de voix ne sont plus trouvés dans les vues")
        self.assertFalse(oublis, "champs de voix sans le widget %s : %s" % (WIDGET, oublis))

    def test_the_widget_ships_in_the_backend_assets(self):
        """Un widget nommé dans une vue mais absent des assets casse la vue au
        chargement (« Missing widget »), pas à l'installation."""
        fichiers = self.env["ir.asset"]._get_asset_paths("web.assets_backend", {})
        self.assertTrue(
            any(p[0].endswith("bf_property_governance/static/src/js/votes_field.js") for p in fichiers),
            "votes_field.js absent de web.assets_backend",
        )

    def test_the_formatter_test_ships_in_the_unit_test_assets(self):
        """L'essai hoot du formateur n'est lancé que s'il est déclaré au paquet
        `web.assets_unit_tests` : un fichier d'essai hors du paquet ne tourne
        jamais, et rien ne le signale."""
        fichiers = self.env["ir.asset"]._get_asset_paths("web.assets_unit_tests", {})
        self.assertTrue(
            any(p[0].endswith("bf_property_governance/static/tests/votes_field.test.js")
                for p in fichiers),
            "votes_field.test.js absent de web.assets_unit_tests",
        )
