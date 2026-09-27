"""Le nom d'un quart et la fréquence d'un cédule suivent la personne qui les lit.

🔴 Le nom d'un quart était un calculé STOCKÉ : « tout le
secteur » s'y figeait dans la langue de qui avait créé le quart, et l'heure de
début dans SON fuseau. La fréquence d'un cédule, elle, prenait son unité dans
une constante de module : « Every 6 mois ».
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestReaderLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_property_operations"], ["en_CA"], overwrite=True
        )
        cls.organisation = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat bilingue", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble bilingue", "organisation_id": cls.organisation.id}
        )
        cls.team = cls.env["maintenance.team"].create({"name": "Veilleurs bilingues"})
        cls.building.bf_maintenance_team_id = cls.team
        cls.boiler = cls.env["maintenance.equipment"].create(
            {"name": "Chaudière bilingue", "bf_building_id": cls.building.id}
        )

    def _read(self, record, fname, lang):
        read = record.with_context(lang=lang)
        read.invalidate_recordset([fname])
        return read[fname]

    def test_the_shift_name_follows_the_reader(self):
        for writer, reader, sector in (
            ("en_CA", "fr_CA", "tout le secteur"),
            ("fr_CA", "en_CA", "the whole sector"),
        ):
            env = self.env(context=dict(self.env.context, lang=writer))
            now = fields.Datetime.now()
            shift = env["bf.property.shift"].create({
                "date_start": now,
                "date_stop": now + timedelta(hours=8),
                "maintenance_team_id": self.team.id,
            })
            env.flush_all()
            self.assertIn(sector, self._read(shift, "name", reader))

    def test_a_shift_is_still_found_by_what_its_name_shows(self):
        """Le nom ne se cherche plus en base : l'équipe et l'immeuble, si."""
        now = fields.Datetime.now()
        shift = self.env["bf.property.shift"].create({
            "date_start": now,
            "date_stop": now + timedelta(hours=8),
            "maintenance_team_id": self.team.id,
            "building_id": self.building.id,
        })
        Shift = self.env["bf.property.shift"]
        self.assertIn(shift, Shift.search([("name", "ilike", "Veilleurs bilingues")]))
        self.assertIn(shift, Shift.search([("name", "ilike", "Immeuble bilingue")]))
        self.assertNotIn(shift, Shift.search([("name", "ilike", "Personne d'autre")]))
        found = Shift.name_search("Veilleurs bilingues")
        self.assertIn(shift.id, [row[0] for row in found])

    def test_the_plan_frequency_follows_the_reader(self):
        plan = self.env["bf.property.maintenance.plan"].create({
            "name": "Inspection bilingue",
            "equipment_id": self.boiler.id,
            "interval_count": 6,
            "interval_unit": "month",
            "start_date": fields.Date.today(),
        })
        self.assertEqual(self._read(plan, "frequency_display", "en_CA"), "Every 6 months")
        self.assertEqual(self._read(plan, "frequency_display", "fr_CA"), "Tous les 6 mois")
