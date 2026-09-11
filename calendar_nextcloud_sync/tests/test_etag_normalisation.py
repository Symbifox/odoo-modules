# -*- coding: utf-8 -*-
"""L'ETag doit se comparer, quelle que soit la porte par laquelle il entre (2.16.0).

Un serveur CalDAV rend son ETag entre guillemets (RFC 9110 §8.8.3). La passe de
lecture retire ces guillemets en analysant `<d:getetag>`; la passe d'écriture,
elle, recevait l'en-tête `ETag` verbatim et le stockait tel quel. Les deux
écritures du même ETag ne se comparaient donc jamais égales, et l'événement
concerné ratait le saut des événements inchangés à chaque passe.

Le second test est celui qui compte : il échoue sur la 2.15.0.
"""

from odoo.tests import TransactionCase, tagged


@tagged("calendar_nextcloud_sync", "caldav_etag")
class TestNormalisationEtag(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Event = cls.env["calendar.event"]

    def test_formes_de_letag(self):
        """Les quatre écritures du même ETag se ramènent à la même valeur."""
        norm = self.Event._normalize_caldav_etag
        self.assertEqual(norm('"abc123"'), "abc123")
        self.assertEqual(norm("abc123"), "abc123")
        self.assertEqual(norm('W/"abc123"'), "abc123")
        self.assertEqual(norm('  "abc123"  '), "abc123")

    def test_vide_reste_vide(self):
        """Une valeur absente traverse sans se faire transformer en chaîne."""
        norm = self.Event._normalize_caldav_etag
        self.assertFalse(norm(False))
        self.assertFalse(norm(None))
        self.assertEqual(norm(""), "")

    def test_letag_pousse_est_stocke_sans_guillemets(self):
        """🔴 Le test qui échoue sur la version précédente.

        `update_etag_from_nextcloud` est rappelée par n8n après son PUT, avec
        l'en-tête `ETag` verbatim. Stocker cette forme citée condamnait
        l'événement à être réécrit à chaque passe de lecture.
        """
        evenement = self.Event.create({
            "name": "Rencontre d'essai",
            "start": "2026-09-11 14:00:00",
            "stop": "2026-09-11 15:00:00",
        })
        self.Event.update_etag_from_nextcloud(
            evenement.id, '"3144e984648be71b9ca3dbd113a4b861"',
        )
        self.assertEqual(
            evenement.x_caldav_etag, "3144e984648be71b9ca3dbd113a4b861",
            "l'ETag doit être stocké sous sa forme nue, pas sous sa forme HTTP",
        )
