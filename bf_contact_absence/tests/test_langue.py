# -*- coding: utf-8 -*-
"""La langue de ce qui s'affiche et de ce qui s'écrit.

Depuis 18.0.1.1.0, la source est anglaise et le français vient du catalogue.
Odoo ne traduit jamais vers en_US : tant que la source était française, un
usager réglé en anglais lisait le module en français, et personne ne pouvait le
voir sur une base où tout le monde parle français.

Le risque inverse est celui qu'on éprouve ici : un texte ÉCRIT en base par un
travail planifié, qui n'a pas de langue au contexte, sortirait désormais dans la
langue source pour un usager français.
"""

from datetime import date, timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_absence")
class TestAbsenceLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_contact_absence"], ["fr_CA"], overwrite=True)
        cls.Absence = cls.env["bf.partner.absence"]
        cls.francoise = cls.env["res.users"].create({
            "name": "Françoise Essai", "login": "francoise.langue@essai.test",
            "lang": "fr_CA",
        })
        cls.jane = cls.env["res.users"].create({
            "name": "Jane Essai", "login": "jane.langue@essai.test",
            "lang": "en_US",
        })
        cls.contact = cls.env["res.partner"].create({"name": "Contact Langue"})

    def _absence(self, suivi_par, **kw):
        debut = fields.Date.today() + timedelta(days=5)
        vals = {"partner_id": self.contact.id, "date_from": debut,
                "date_to": debut + timedelta(days=4), "nature": "vacation",
                "user_id": suivi_par.id}
        vals.update(kw)
        # Sans langue au contexte : c'est ainsi que tourne un travail planifié.
        return self.Absence.with_context({}).create(vals)

    def test_le_libelle_dune_absence_suit_la_langue_de_qui_le_lit(self):
        absence = self._absence(self.jane, reminder=False)
        en = absence.with_context(lang="en_US").display_name
        fr = absence.with_context(lang="fr_CA").display_name
        self.assertIn("Holiday", en)
        self.assertIn(" from ", en)
        self.assertIn("Vacances", fr)
        self.assertIn(" du ", fr)

    def test_le_rappel_de_reprise_est_redige_dans_la_langue_de_lassigne(self):
        """🔴 Écrit sans langue au contexte, il sortait dans la langue source."""
        pour_francoise = self._absence(self.francoise).reminder_activity_id
        pour_jane = self._absence(
            self.jane, date_from=fields.Date.today() + timedelta(days=30),
            date_to=fields.Date.today() + timedelta(days=34),
        ).reminder_activity_id
        self.assertTrue(pour_francoise and pour_jane)
        self.assertEqual(pour_francoise.summary, "Prendre des nouvelles au retour")
        self.assertIn("jusqu'au", str(pour_francoise.note))
        self.assertEqual(pour_jane.summary, "Check in on their return")
        self.assertIn("away until", str(pour_jane.note))

    def test_la_nature_se_choisit_dans_la_langue_de_lusager(self):
        fr = dict(self.Absence.with_context(lang="fr_CA").fields_get(
            ["nature"])["nature"]["selection"])
        en = dict(self.Absence.with_context(lang="en_US").fields_get(
            ["nature"])["nature"]["selection"])
        self.assertEqual(fr["vacation"], "Vacances")
        self.assertEqual(en["vacation"], "Holiday")
