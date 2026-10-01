# -*- coding: utf-8 -*-
"""La signature postée d'un autre site, et le limiteur qui se vidait."""

import time
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.bf_celebrations.controllers import main as ctrl


@tagged("post_install", "-at_install", "bf_celebrations")
class TestSignatureCroisee(HttpCase):
    """Odoo 18 ne pose pas SameSite sur `session_id` : un formulaire posté
    depuis un autre site arrive avec le cookie (Firefox, Safari). Le mot ne
    doit alors PAS être rattaché au compte connecté."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employe = cls.env["hr.employee"].create({
            "name": "Fêtée Croisée", "work_email": "cel_csrf@example.test"})
        cls.tableau = cls.env["bf.celebration.board"].create({
            "name": "Carte croisée",
            "recipient_employee_id": cls.employe.id,
            "delivery_date": datetime.now() + timedelta(days=2),
            "state": "open",
        })
        cls.jeton = cls.tableau.sudo().access_token

    def setUp(self):
        super().setUp()
        with ctrl._bucket_lock:
            ctrl._bucket_data.clear()
        self.authenticate("admin", "admin")

    def _signer(self, entetes):
        self.url_open("/celebration/%s/signer" % self.jeton,
                      data={"author_name": "Quelqu'un", "body": "Bravo"},
                      headers=entetes, timeout=30)
        self.env.invalidate_all()
        return self.tableau.sudo().post_ids[-1:]

    def test_post_venu_d_un_autre_site(self):
        mot = self._signer({"Origin": "https://pirate.example"})
        self.assertTrue(mot, "le mot n'a pas été créé")
        self.assertFalse(mot.author_user_id,
                         "un POST d'un autre site signe au nom du connecté")

    def test_post_sans_origine_ni_referent(self):
        mot = self._signer({})
        self.assertFalse(mot.author_user_id)

    def test_post_depuis_la_page(self):
        mot = self._signer({"Origin": self.base_url()})
        self.assertEqual(mot.author_user_id, self.env.ref("base.user_admin"))


@tagged("post_install", "-at_install", "bf_celebrations")
class TestLimiteurBorne(TransactionCase):
    """Passé le plafond de clés, le limiteur faisait `clear()` : la source
    bloquée repartait à zéro. Rejoué : on bloque une IP, on fait défiler plus
    de clés que le plafond, on réessaie depuis l'IP bloquée."""

    def setUp(self):
        super().setUp()
        with ctrl._bucket_lock:
            ctrl._bucket_data.clear()
        self.addCleanup(ctrl._bucket_data.clear)

    def test_une_ip_bloquee_le_reste_apres_le_defilement(self):
        with patch.object(ctrl, "_ip", return_value="203.0.113.7"):
            for _i in range(ctrl._SIGN_MAX):
                self.assertTrue(ctrl._plafond(
                    "cel_sign", ctrl._SIGN_MAX, ctrl._SIGN_WINDOW))
            self.assertFalse(ctrl._plafond(
                "cel_sign", ctrl._SIGN_MAX, ctrl._SIGN_WINDOW))
        for i in range(ctrl._MAX_TRACKED + 10):
            with patch.object(ctrl, "_ip", return_value="10.%d.%d.1" % (
                    i // 256, i % 256)):
                ctrl._plafond("cel_sign", ctrl._SIGN_MAX, ctrl._SIGN_WINDOW)
        self.assertLessEqual(len(ctrl._bucket_data), ctrl._MAX_TRACKED + 1)
        with patch.object(ctrl, "_ip", return_value="203.0.113.7"):
            self.assertFalse(
                ctrl._plafond("cel_sign", ctrl._SIGN_MAX, ctrl._SIGN_WINDOW),
                "l'IP bloquée repart à zéro après le défilement")

    def test_les_cles_echues_partent_d_abord(self):
        vieux = time.monotonic() - 10 * ctrl._SIGN_WINDOW
        with ctrl._bucket_lock:
            ctrl._bucket_window["cel_sign"] = ctrl._SIGN_WINDOW
            for i in range(ctrl._MAX_TRACKED + 1):
                ctrl._bucket_data[("cel_sign", "v%d" % i)] = [vieux]
        with patch.object(ctrl, "_ip", return_value="203.0.113.8"):
            ctrl._plafond("cel_sign", ctrl._SIGN_MAX, ctrl._SIGN_WINDOW)
        self.assertEqual(len(ctrl._bucket_data), 1)
