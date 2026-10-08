# -*- coding: utf-8 -*-
"""The home screen speaks its reader's language.

The source is English since 18.0.2.1.0 and the French lives in fr_CA.po. Four
things did not follow the reader's language even then: the dashboard actions
named in a plain dict, the waiting tasks' state labels read from the raw
selection, the backup state label read the same way, and the meeting time
written in the French form ("9 h 05") for everyone.
"""

from datetime import datetime, time, timedelta

import pytz

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestHomeLanguage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(["bf_home"], ["fr_CA"], overwrite=True)
        cls.lectrice = cls.env["res.users"].create({
            "name": "Lectrice Accueil", "login": "lectrice.accueil@example.test",
            "email": "lectrice.accueil@example.test", "lang": "fr_CA", "tz": "America/Toronto",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def _home(self, lang):
        # Always an explicit language: without one, _() guesses the user's (fr_CA here).
        return self.env["bf.home"].with_user(self.lectrice).with_context(
            lang=lang, tz="America/Toronto")

    def test_headline_follows_the_reader(self):
        self.assertEqual(self._home("fr_CA")._headline([]),
                         "Rien ne réclame votre attention ce matin.")
        self.assertEqual(self._home("en_US")._headline([]),
                         "Nothing needs your attention this morning.")

    def test_meeting_time_reads_in_each_language(self):
        if not self.env["bf.home"]._has("calendar.event", "start", "partner_ids"):
            self.skipTest("calendar absent")
        tz = pytz.timezone("America/Toronto")
        jour = fields.Date.context_today(self._home("fr_CA"))
        debut = tz.localize(datetime.combine(jour, time(13, 5))).astimezone(pytz.utc)
        self.env["calendar.event"].create({
            "name": "Language meeting", "start": debut.replace(tzinfo=None),
            "stop": (debut + timedelta(hours=1)).replace(tzinfo=None),
            "partner_ids": [(6, 0, self.lectrice.partner_id.ids)],
        })
        fr = self._home("fr_CA")._c_meetings()
        en = self._home("en_US")._c_meetings()
        self.assertTrue(fr and en, "the meeting must show up in both languages")
        self.assertIn("Aujourd'hui à 13 h 05", fr[0]["sub"])
        self.assertIn("Today at 13:05", en[0]["sub"])

    def test_waiting_state_label_is_translated(self):
        Task = self.env["project.task"]
        etats = dict(Task._fields["state"].selection or [])
        if "05_waiting_client" not in etats:
            self.skipTest("no waiting states on this database")
        fr_libelles = dict(Task.with_context(lang="fr_CA")._fields["state"]
                           ._description_selection(Task.with_context(lang="fr_CA").env))
        if fr_libelles["05_waiting_client"] == etats["05_waiting_client"]:
            self.skipTest("the waiting label reads the same in both languages here")
        projet = self.env["project.project"].create({"name": "Waiting language project"})
        Task.create({"name": "Waiting on the client", "project_id": projet.id,
                     "state": "05_waiting_client"})
        # Detail rows end with the state label; the overflow row has no res_id.
        details = [l for l in self._home("fr_CA").sudo()._c_waiting() if l["res_id"]]
        self.assertTrue(details)
        attente = ("05_waiting_client", "06_waiting_external")
        for ligne in details:
            libelle = ligne["sub"].split(" · ")[-1]
            self.assertIn(libelle, [fr_libelles[e] for e in attente if e in fr_libelles])
            self.assertNotEqual(libelle, etats["05_waiting_client"])

    def test_dashboard_actions_are_named_in_the_reader_language(self):
        if self.env.get("account.move") is None:
            self.skipTest("accounting absent")
        Tableau = self.env["bf.dashboard"]
        self.assertEqual(Tableau.with_context(lang="fr_CA").action_view_draft_invoices()["name"],
                         "Factures brouillon")
        self.assertEqual(Tableau.with_context(lang="en_US").action_view_draft_invoices()["name"],
                         "Draft invoices")
