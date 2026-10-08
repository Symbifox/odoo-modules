# -*- coding: utf-8 -*-
"""La fiche d'un événement s'ouvre au téléphone sans le groupe Rencontres.

Un compte interne lit la rencontre, pas forcément son ordre du jour ni son compte
rendu. Avant la garde, la recherche de l'un ou de l'autre levait un refus d'accès
et c'était la fiche entière qui tombait, ordre du jour ou pas.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestFicheSansRencontres(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "meeting.agenda" not in cls.env:
            return
        interne = cls.env.ref("base.group_user")
        cls.sans = cls.env["res.users"].create({
            "name": "Banc Sans Rencontres",
            "login": "banc.mobile.sans.rencontres",
            "email": "banc.mobile.sans.rencontres@example.org",
            "groups_id": [(6, 0, interne.ids)],
        })
        cls.avec = cls.env["res.users"].create({
            "name": "Banc Avec Rencontres",
            "login": "banc.mobile.avec.rencontres",
            "email": "banc.mobile.avec.rencontres@example.org",
            "groups_id": [(6, 0, (interne | cls.env.ref("bf_meeting.group_meeting_user")).ids)],
        })
        projet = cls.env["project.project"].create({"name": "Projet fiche mobile"})
        # Les deux comptes suivent le projet : la règle des OdJ et des comptes
        # rendus passe par lui, et l'essai mesure le GROUPE, pas la règle.
        projet.message_subscribe(partner_ids=(cls.sans | cls.avec).partner_id.ids)
        cls.event = cls.env["calendar.event"].create({
            "name": "Rencontre préparée",
            "start": "2026-11-10 14:00:00",
            "stop": "2026-11-10 15:00:00",
            "partner_ids": [(6, 0, (cls.sans | cls.avec).partner_id.ids)],
        })
        for modele in ("meeting.agenda", "meeting.record"):
            cls.env[modele].create({
                "project_id": projet.id,
                "date": cls.event.start,
                "calendar_event_id": cls.event.id,
            })

    def setUp(self):
        super().setUp()
        if "meeting.agenda" not in self.env:
            self.skipTest("bf_meeting absent")
        self.assertFalse(self.sans.has_group("bf_meeting.group_meeting_user"))

    def test_la_fiche_s_ouvre_sans_le_groupe(self):
        detail = self.event.with_user(self.sans).mobile_detail()
        self.assertEqual(detail["id"], self.event.id)
        self.assertIsNone(detail["agenda"])
        self.assertIsNone(detail["minutes"])

    def test_le_groupe_rend_toujours_l_odj_et_le_compte_rendu(self):
        """La garde ne doit pas cacher ce que le groupe a le droit de voir."""
        detail = self.event.with_user(self.avec).mobile_detail()
        self.assertTrue(detail["agenda"])
        self.assertTrue(detail["minutes"])
