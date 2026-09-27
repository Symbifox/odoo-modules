"""Qui voit quel immeuble, une fois l'exploitation installée.

🔴 Joué en rôle gestionnaire, sans les règles qui suivent : la liste
« Fractions » levait une erreur d'accès et un bailleur sans équipe d'entretien
ne voyait AUCUN de ses immeubles.

La règle d'Exploitation (« ceux qu'une équipe d'exploitation dessert ») était
écrite en croyant qu'elle se combinerait par OU avec une règle du groupe
Consultation. Cette règle n'existait pas. Or Odoo combine par OU les seules
règles des groupes de la personne : un groupe sans règle n'ouvre rien. Et le
gestionnaire implique l'Exploitation. Tout gestionnaire se retrouvait donc
restreint aux immeubles pourvus d'une équipe.

Les 925 tests tournaient en administrateur, qui traverse les règles : aucun ne
pouvait le voir. Ceux-ci jouent chaque rôle avec un vrai compte.
"""
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged, new_test_user


@tagged("post_install", "-at_install")
class TestBuildingVisibility(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        org = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat de la visibilité", "fraction_base": 1000})
        cls.team = cls.env["maintenance.team"].create({"name": "Équipe du Chabot"})
        cls.served = cls.env["bf.property.building"].create(
            {"name": "Immeuble servi", "organisation_id": org.id,
             "bf_maintenance_team_id": cls.team.id})
        cls.unserved = cls.env["bf.property.building"].create(
            {"name": "Immeuble sans équipe", "organisation_id": org.id})
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "12", "building_id": cls.unserved.id})
        cls.manager = new_test_user(
            cls.env, login="qa-gestionnaire",
            groups="base.group_user,bf_property_core.group_bf_property_manager")
        cls.reader = new_test_user(
            cls.env, login="qa-consultation",
            groups="base.group_user,bf_property_core.group_bf_property_user")
        cls.caretaker = new_test_user(
            cls.env, login="qa-concierge",
            groups="base.group_user,bf_property_operations.group_bf_property_operations")

    def _visible(self, user):
        return self.env["bf.property.building"].with_user(user).search([])

    def test_manager_sees_a_building_without_team(self):
        self.assertIn(self.unserved, self._visible(self.manager))

    def test_manager_opens_the_units_list(self):
        # Le geste qui cassait : la liste lit l'immeuble de chaque fraction.
        units = self.env["bf.property.unit"].with_user(self.manager)
        rows = units.web_search_read(
            [("id", "=", self.unit.id)],
            {"display_name": {}, "building_id": {"fields": {"display_name": {}}}})
        self.assertEqual(rows["length"], 1)

    def test_reader_with_operations_still_sees_everything(self):
        self.reader.groups_id |= self.env.ref(
            "bf_property_operations.group_bf_property_operations")
        self.assertIn(self.unserved, self._visible(self.reader))

    def test_operations_alone_stays_on_served_buildings(self):
        # Le cloisonnement voulu par l'exploitation tient toujours : un concierge sans le
        # groupe Consultation ne voit que ce que son équipe dessert.
        seen = self._visible(self.caretaker)
        self.assertIn(self.served, seen)
        self.assertNotIn(self.unserved, seen)
        with self.assertRaises(AccessError):
            self.unserved.with_user(self.caretaker).read(["name"])
