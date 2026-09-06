# -*- coding: utf-8 -*-
"""Le battement « vu la dernière fois » ne dispute plus rien à personne (internal report).

Ce qui cassait : chaque appel authentifié réécrivait ``last_seen`` dans la
transaction de la requête, et deux appels simultanés du même téléphone
faisaient échouer le second sous REPEATABLE READ — un 500, un geste annulé.
On ne peut pas jouer deux transactions concurrentes dans un banc Odoo ; on
vérifie donc les deux règles qui rendent le conflit impossible ou inoffensif :
un battement par minute, et un conflit d'écriture qui remonte à Odoo au lieu
de sortir en 500.

⚠️ Tout passe par HTTP : le battement ouvre son propre curseur, et hors d'une
requête de banc ce curseur est une vraie connexion, qui ne voit pas la ligne
d'appareil encore non validée du test. Sous ``HttpCase`` la requête tourne
sur le curseur de test, et le battement avec elle.
"""
from datetime import timedelta
from unittest.mock import patch

from psycopg2 import errors as pg_errors

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

BASE = "/bf_sms_archive/mobile/v1"


class ConflitSimule(pg_errors.SerializationFailure):
    """Ce que PostgreSQL lève quand deux transactions écrivent la même ligne.

    Le code SQLSTATE est en lecture seule sur l'exception réelle ; la
    sous-classe le porte pour que ``service.model.retrying`` le reconnaisse.
    """
    pgcode = "40001"


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestMobileHeartbeatHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login="sms_heartbeat_http",
            groups="bf_sms_archive.group_sms_user",
        )
        cls.device = cls.env["sms.archive.mobile.device"]._issue(
            cls.user.id, name="Appareil HTTP")
        cls.env.cr.flush()

    def _threads(self):
        return self.url_open(
            BASE + "/threads?archived=0",
            headers={"Authorization": "Bearer %s" % self.device.device_token},
            timeout=30,
        )

    def test_un_battement_par_minute(self):
        device = self.device.sudo()
        now = fields.Datetime.now()

        # Jamais vu : le premier appel écrit.
        device.write({"last_seen": False})
        self.env.cr.flush()
        self.assertEqual(self._threads().status_code, 200)
        device.invalidate_recordset(["last_seen"])
        self.assertTrue(device.last_seen)
        self.assertLess(now - device.last_seen, timedelta(seconds=10))

        # Vu il y a trente secondes : rien à réécrire.
        recent = now - timedelta(seconds=30)
        device.write({"last_seen": recent})
        self.env.cr.flush()
        self.assertEqual(self._threads().status_code, 200)
        device.invalidate_recordset(["last_seen"])
        self.assertEqual(device.last_seen, recent)

        # Vu il y a cinq minutes : on réécrit.
        device.write({"last_seen": now - timedelta(minutes=5)})
        self.env.cr.flush()
        self.assertEqual(self._threads().status_code, 200)
        device.invalidate_recordset(["last_seen"])
        self.assertLess(fields.Datetime.now() - device.last_seen, timedelta(seconds=10))

    def test_un_conflit_d_ecriture_est_rejoue_pas_rapporte(self):
        Thread = type(self.env["sms.archive.thread"])
        original = Thread.get_messenger_threads
        appels = []

        def instable(model, *args, **kwargs):
            appels.append(1)
            if len(appels) == 1:
                raise ConflitSimule(
                    "could not serialize access due to concurrent update")
            return original(model, *args, **kwargs)

        with patch.object(Thread, "get_messenger_threads", instable):
            response = self._threads()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(appels), 2, "la requête devait être rejouée une fois")
