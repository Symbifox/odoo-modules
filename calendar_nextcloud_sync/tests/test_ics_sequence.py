# -*- coding: utf-8 -*-
"""La révision du VEVENT poussé vers Nextcloud (2.18.0).

Relevé le 2026-09-21 : `build_ics` n'écrivait aucun `SEQUENCE`,
alors que le `.ics` des courriels en porte un depuis le 2026-09-09. Deux
révisions de la même rencontre arrivaient donc chez le serveur distant sans
qu'aucune ne se déclare plus récente que l'autre, et un client d'agenda qui les
reçoit dans le désordre n'a aucun moyen de dire laquelle garder
(RFC 5545 §3.8.7.4).

⚠️ Le lien vers `bf_calendar_invite` est MOU : le champ `bf_ics_sequence` lui
appartient et ce module tourne aussi chez des locataires qui ne l'ont pas. Les
essais couvrent les deux cas, parce que le repli « on n'écrit rien » est une
décision, pas un accident : un `SEQUENCE:0` posé sur une rencontre déjà révisée
la ferait passer pour l'originale.
"""

from datetime import datetime

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("calendar_nextcloud_sync", "caldav_ics")
class TestIcsSequence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, tz="UTC"))
        cls.backend = cls.env["calendar.caldav.backend"]
        cls.voisin = "bf_ics_sequence" in cls.env["calendar.event"]._fields

    def _exiger_le_voisin(self):
        # ⚠️ Le lien est mou : sur une base sans `bf_calendar_invite` (la CI
        # teste chaque module dans son lot, sans ses voisins facultatifs), il
        # n'y a aucune révision à suivre. Ces deux essais ne prouvent rien
        # sans le champ ; le repli, lui, est couvert plus bas dans les deux cas.
        if not self.voisin:
            self.skipTest("bf_calendar_invite absent de cette base : aucun "
                          "bf_ics_sequence à écrire")

    def _event(self, **vals):
        base = {
            "name": "Rencontre à réviser",
            "start": datetime(2026, 8, 20, 21, 0, 0),
            "stop": datetime(2026, 8, 20, 21, 30, 0),
            "partner_ids": [Command.clear()],
        }
        base.update(vals)
        event = self.env["calendar.event"].create(base)
        event.x_nc_uid = "seq-%d@test.invalid" % event.id
        return event

    def test_le_vevent_porte_une_revision(self):
        self._exiger_le_voisin()
        event = self._event()
        ics = self.backend.build_ics(event)
        self.assertIn("SEQUENCE:", ics)

    def test_la_revision_suit_les_deplacements(self):
        """Ce qui compte n'est pas la présence du champ, c'est qu'il MONTE."""
        self._exiger_le_voisin()
        event = self._event()
        premier = self.backend.build_ics(event)
        event.write({"stop": datetime(2026, 8, 20, 22, 0, 0)})
        second = self.backend.build_ics(event)
        self.assertNotEqual(
            self._sequence(premier), self._sequence(second),
            "deux révisions de la même rencontre partent sous le même numéro",
        )
        self.assertGreater(self._sequence(second), self._sequence(premier))

    def test_sans_le_module_voisin_rien_n_est_ecrit(self):
        """Le repli n'invente pas un zéro.

        Simulé en retirant le champ de la vue que `build_ics` interroge, ce qui
        est exactement ce que voit un locataire sans `bf_calendar_invite`.
        """
        event = self._event()
        if not self.voisin:
            # Le cas réel d'un locataire sans le module voisin : rien à simuler.
            self.assertNotIn("SEQUENCE:", self.backend.build_ics(event))
            return
        champs = dict(event._fields)
        champs.pop("bf_ics_sequence")
        # ⚠️ `self.patch` d'Odoo applique tout de suite et défait au nettoyage :
        # ce n'est PAS un gestionnaire de contexte, et l'écrire en `with` rend
        # « 'NoneType' object does not support the context manager protocol ».
        self.patch(type(event), "_fields", champs)
        ics = self.backend.build_ics(event)
        self.assertNotIn("SEQUENCE:", ics)

    @staticmethod
    def _sequence(ics):
        for ligne in ics.splitlines():
            if ligne.startswith("SEQUENCE:"):
                return int(ligne.split(":", 1)[1])
        return None
