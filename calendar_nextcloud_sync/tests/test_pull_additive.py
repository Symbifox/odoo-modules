# -*- coding: utf-8 -*-
"""L'aller-retour Nextcloud ne doit pas perdre les participants (2.15.0).

🔴 En production, une rencontre est sortie de sa création avec un seul
`calendar.attendee` sur quatre. La cause tient en deux lignes qui
ne se lisaient pas ensemble : `_get_sync_payload` pousse volontairement les
événements adossés à une réservation SANS participants — pour que Nextcloud
n'émette pas une seconde invitation iMIP — et `create_from_nextcloud` écrivait
au retour `partner_ids = [(6, 0, …)]`, un remplacement construit à partir de ce
que porte la copie Nextcloud. C'est-à-dire le propriétaire, et personne d'autre.

Le test qui compte est le second : il échoue sur la version précédente.
"""

from datetime import datetime

from unittest.mock import patch

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("calendar_nextcloud_sync", "caldav_ics")
class TestPullAdditive(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="UTC"))
        cls.organisateur = cls.env["res.users"].create({
            "name": "Organisateur Ingestion",
            "login": "ingestion.organisateur@test.invalid",
            "email": "ingestion.organisateur@test.invalid",
            "tz": "UTC",
        })
        cls.config = cls.env["nextcloud.calendar.sync.config"].create({
            "name": "Agenda d'essai",
            "nextcloud_user": "essai",
            "nextcloud_base_url": "https://nc.test.invalid",
            "caldav_path": "/remote.php/dav/calendars/essai/essai/",
            "calendar_owner_id": cls.organisateur.id,
            "sync_direction": "both",
        })
        cls.clients = cls.env["res.partner"].create([
            {"name": "Client A", "email": "a@ingestion.invalid"},
            {"name": "Client B", "email": "b@ingestion.invalid"},
        ])

    def _event(self):
        event = self.env["calendar.event"].with_context(
            skip_nc_sync=True, no_mail_to_attendees=True,
        ).create({
            "name": "Rencontre",
            "start": datetime(2027, 3, 10, 15, 0, 0),
            "stop": datetime(2027, 3, 10, 15, 30, 0),
            "user_id": self.organisateur.id,
            "partner_ids": [Command.set(
                (self.organisateur.partner_id | self.clients).ids)],
        })
        event.write({
            "x_nc_uid": "ingestion-%d@test.invalid" % event.id,
            "x_nc_calendar_id": self.config.id,
        })
        return event

    def _charge_sans_participant(self, event):
        """Ce que Nextcloud rend d'un événement poussé sans participants."""
        return {
            "uid": event.x_nc_uid,
            "summary": event.name,
            "start": "2027-03-10 15:00:00",
            "end": "2027-03-10 15:30:00",
            "attendees": [],
            "etag": "etag-essai",
            "href": "/essai.ics",
        }

    def test_a_normal_event_is_still_replaced_by_nextcloud(self):
        """Hors réservation, Nextcloud reste la source : le remplacement tient."""
        event = self._event()
        self.env["calendar.event"].create_from_nextcloud(
            self._charge_sans_participant(event), self.config.id)
        self.assertEqual(
            event.partner_ids, self.organisateur.partner_id,
            "un événement ordinaire doit suivre ce que dit Nextcloud",
        )

    def test_a_booking_event_keeps_its_attendees(self):
        """⚠️ Le test qui discrimine : il est ROUGE avant 18.0.2.15.0.

        On simule ce que la production fait à chaque réingestion d'un événement
        de réservation : Nextcloud rend une charge sans aucun participant,
        parce que c'est nous qui les avons retirés à la poussée.
        """
        event = self._event()
        attendus = self.organisateur.partner_id | self.clients

        # Le prédicat est piloté par `resource_booking_ids`. Plutôt que de
        # monter une réservation complète (calendrier, ressource, type), on
        # remplace le prédicat sur la classe : le test porte sur la DÉCISION
        # d'être additif, pas sur la façon dont on la prend.
        # ⚠️ `patch.object` et non une affectation directe : poser l'attribut
        # à la main sur la classe du registre le laisse en place, et le
        # démontage d'Odoo le refuse (« Found unexpected attributes on
        # calendar.event »). Trois échecs pour un seul défaut.
        Event = type(self.env["calendar.event"])
        with patch.object(Event, "_bf_odoo_owns_attendees", lambda soi: True):
            self.env["calendar.event"].create_from_nextcloud(
                self._charge_sans_participant(event), self.config.id)

        perdus = attendus - event.partner_ids
        self.assertFalse(
            perdus,
            "l'ingestion a perdu %s : sur une réservation elle doit compléter, "
            "jamais remplacer" % perdus.mapped("name"),
        )
