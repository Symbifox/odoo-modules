# -*- coding: utf-8 -*-
"""Le plafond des écrans en direct (controllers/viewer_slots.py)."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_claude_chat.controllers import viewer_slots


@tagged("post_install", "-at_install")
class TestPlafondEcrans(TransactionCase):

    def setUp(self):
        super().setUp()
        self.icp = self.env["ir.config_parameter"].sudo()
        self.db = f"{self.env.cr.dbname}-essai-plafond"

    def test_plafond_par_defaut_moitie_des_workers(self):
        self.icp.set_param("bf_claude_chat.max_live_viewers", "")
        with patch.dict(viewer_slots.config.options, {"workers": 10}):
            self.assertEqual(viewer_slots.viewer_cap(self.env), 5)
        with patch.dict(viewer_slots.config.options, {"workers": 1}):
            self.assertEqual(viewer_slots.viewer_cap(self.env), 1)
        with patch.dict(viewer_slots.config.options, {"workers": 0}):
            self.assertEqual(viewer_slots.viewer_cap(self.env), 0)

    def test_parametre_impose_et_illisible(self):
        with patch.dict(viewer_slots.config.options, {"workers": 10}):
            self.icp.set_param("bf_claude_chat.max_live_viewers", "2")
            self.assertEqual(viewer_slots.viewer_cap(self.env), 2)
            self.icp.set_param("bf_claude_chat.max_live_viewers", "beaucoup")
            self.assertEqual(viewer_slots.viewer_cap(self.env), 5)

    def test_places_prises_refusees_puis_rendues(self):
        self.icp.set_param("bf_claude_chat.max_live_viewers", "2")
        a = viewer_slots.acquire(self.env, self.db)
        b = viewer_slots.acquire(self.env, self.db)
        self.assertTrue(a and b)
        self.assertIsNone(viewer_slots.acquire(self.env, self.db))
        a.release()
        a.release()  # deux fois : sans effet
        c = viewer_slots.acquire(self.env, self.db)
        self.assertTrue(c)
        self.assertIsNone(viewer_slots.acquire(self.env, self.db))
        b.release()
        c.release()

    def test_held_rend_la_place_meme_si_le_flux_est_abandonne(self):
        self.icp.set_param("bf_claude_chat.max_live_viewers", "1")
        slot = viewer_slots.acquire(self.env, self.db)
        flux = viewer_slots.held(slot, iter([b"a", b"b"]))
        next(flux)
        self.assertIsNone(viewer_slots.acquire(self.env, self.db))
        flux.close()  # ce que fait werkzeug quand le navigateur part
        again = viewer_slots.acquire(self.env, self.db)
        self.assertTrue(again)
        again.release()

    def test_sans_plafond_toujours_une_place(self):
        self.icp.set_param("bf_claude_chat.max_live_viewers", "0")
        self.assertTrue(all(viewer_slots.acquire(self.env, self.db) for _ in range(20)))
