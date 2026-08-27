# -*- coding: utf-8 -*-
"""Un champ calculé STOCKÉ sans @api.depends ne se recalcule jamais.

Deux l'ont été dans la première version, et les deux étaient dans le
chemin de sécurité : ``is_dead`` alimente la garde de pré-vol, et
``confirmed`` porte la garantie « la machine propose, l'humain
confirme ». Ces tests existent pour que ça ne repasse pas."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAuditRecalcul(TransactionCase):

    def setUp(self):
        super().setUp()
        self.cal = self.env["bf.editorial.calendar"].create({
            "name": "Audit", "require_all_langs": "no", "word_floor": 100,
        })
        self.e = self.env["bf.editorial.entry"].create({
            "name": "Entrée d'audit", "calendar_id": self.cal.id,
            "stage_id": self.env.ref("bf_editorial.stage_draft").id,
        })

    def test_A_source_devenue_morte_est_vue(self):
        s = self.env["bf.editorial.source"].create({
            "entry_id": self.e.id, "name": "S", "url": "https://x.test",
            "http_status": 200,
        })
        self.assertFalse(s.is_dead, "une source à 200 ne doit pas être morte")
        s.http_status = 404
        s.flush_recordset()
        self.assertTrue(s.is_dead, "404 doit rendre is_dead vrai")

    def test_B_garde_voit_la_source_morte(self):
        s = self.env["bf.editorial.source"].create({
            "entry_id": self.e.id, "name": "S", "url": "https://x.test",
            "http_status": 200,
        })
        s.http_status = 404
        s.flush_recordset()
        self.e.invalidate_recordset()
        self.assertEqual(self.e.dead_source_count, 1)

    def test_C_confirmed_suit_action_confirm(self):
        c = self.env["bf.editorial.claim"].create({
            "entry_id": self.e.id, "name": "A", "verdict": "ok",
            "is_machine_proposed": True,
        })
        self.assertFalse(c.confirmed)
        c.action_confirm()
        c.flush_recordset()
        self.assertTrue(c.confirmed, "confirmed doit suivre is_machine_proposed")

    def test_D_bascule_is_blocking_vue_par_la_garde(self):
        ck = self.env["bf.editorial.checklist"].create({
            "entry_id": self.e.id, "name": "R", "is_blocking": False,
        })
        self.e.invalidate_recordset()
        self.assertEqual(self.e.open_checklist_count, 0)
        ck.is_blocking = True
        ck.flush_recordset()
        self.e.invalidate_recordset()
        self.assertEqual(self.e.open_checklist_count, 1,
                         "basculer is_blocking doit rouvrir la garde")

    def test_E_archive_url_ressuscite_une_source(self):
        s = self.env["bf.editorial.source"].create({
            "entry_id": self.e.id, "name": "S", "url": "https://x.test",
            "http_status": 404,
        })
        self.assertTrue(s.is_dead)
        s.archive_url = "https://web.archive.org/x"
        s.flush_recordset()
        self.assertFalse(s.is_dead, "une copie archivée annule la mort")
