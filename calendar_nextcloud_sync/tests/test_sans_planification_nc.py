# -*- coding: utf-8 -*-
"""Nextcloud ne parle plus aux invités d'une rencontre poussée par Odoo (2.24.0).

Constaté en production : une rencontre créée dans Odoo est partie deux fois
chez son invitée. L'invitation d'Odoo d'abord, puis, une seconde plus
tard, celle du greffon de planification de Nextcloud, déclenchée par notre
propre ``PUT``. Les deux écritures (``PUT`` et ``DELETE``) portent désormais
``x-nc-scheduling: false``, que ``Schedule\\Plugin`` de Nextcloud lit avant
tout traitement.

⚠️ L'en-tête coupe le courriel, pas la liste d'invités : la copie Nextcloud
doit toujours dire qui est attendu. Un essai le vérifie, parce que « retirer
les ATTENDEE » est l'autre façon de faire taire Nextcloud, celle qu'on a déjà
prise pour les réservations, et qu'elle appauvrit l'agenda.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

REQUESTS = "odoo.addons.calendar_nextcloud_sync.models.caldav_backend.requests"


def _reponse(code=201):
    return SimpleNamespace(
        status_code=code,
        headers={"ETag": '"e1"'},
        raise_for_status=lambda: None,
    )


@tagged("post_install", "-at_install", "calendar_nextcloud_sync")
class TestSansPlanificationNextcloud(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, no_mail_to_attendees=True))
        cls.config = cls.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda essai",
            "backend_type": "nextcloud",
            "nextcloud_base_url": "https://nc.example.test",
            "caldav_path": "/remote.php/dav/calendars/alice/perso/",
            "nextcloud_user": "alice",
            "nextcloud_app_password": "mdp-essai",
            "sync_direction": "both",
        })
        cls.invitee = cls.env["res.partner"].create({
            "name": "Invitée essai",
            "email": "invitee.essai@example.invalid",
        })

    def _event(self):
        now = fields.Datetime.now()
        event = self.env["calendar.event"].with_context(
            skip_nc_sync=True).create({
                "name": "Rencontre essai",
                "start": now + timedelta(days=1),
                "stop": now + timedelta(days=1, hours=1),
                "partner_ids": [Command.set(self.invitee.ids)],
            })
        event.with_context(skip_nc_sync=True).x_nc_uid = (
            "essai-%d@test.invalid" % event.id)
        return event

    def test_le_put_coupe_la_planification(self):
        event = self._event()
        with patch(REQUESTS + ".put", return_value=_reponse()) as put:
            self.env["calendar.caldav.backend"].push(self.config, event)
        en_tetes = put.call_args.kwargs["headers"]
        self.assertEqual(en_tetes.get("x-nc-scheduling"), "false")
        self.assertEqual(
            en_tetes.get("Content-Type"), "text/calendar; charset=utf-8")

    def test_la_liste_d_invites_reste_dans_le_vevent(self):
        event = self._event()
        with patch(REQUESTS + ".put", return_value=_reponse()) as put:
            self.env["calendar.caldav.backend"].push(self.config, event)
        # Déplier d'abord : une ligne de plus de 75 octets est repliée
        # (RFC 5545 §3.1) et l'adresse y est coupée en deux.
        corps = put.call_args.kwargs["data"].decode("utf-8").replace(
            "\r\n ", "")
        self.assertIn("invitee.essai@example.invalid", corps)
        self.assertIn("ATTENDEE", corps)

    def test_le_delete_coupe_la_planification(self):
        url = "https://nc.example.test/remote.php/dav/calendars/alice/perso/x.ics"
        with patch(REQUESTS + ".delete", return_value=_reponse(204)) as delete:
            self.env["calendar.caldav.backend"].push_delete(
                self.config, url=url)
        self.assertEqual(
            delete.call_args.kwargs["headers"].get("x-nc-scheduling"), "false")

    def test_la_creation_dans_odoo_pousse_sans_planification(self):
        """Le chemin réel : créer la rencontre dans Odoo déclenche le PUT."""
        now = fields.Datetime.now()
        with patch(REQUESTS + ".put", return_value=_reponse()) as put:
            self.env["calendar.event"].create({
                "name": "Rencontre essai, chemin réel",
                "start": now + timedelta(days=2),
                "stop": now + timedelta(days=2, hours=1),
                "partner_ids": [Command.set(self.invitee.ids)],
                "x_nc_calendar_id": self.config.id,
            })
        self.assertTrue(put.called, "la création n'a rien poussé")
        for appel in put.call_args_list:
            self.assertEqual(
                appel.kwargs["headers"].get("x-nc-scheduling"), "false")
