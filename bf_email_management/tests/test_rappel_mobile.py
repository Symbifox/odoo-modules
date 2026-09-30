# -*- coding: utf-8 -*-
"""Les rappels de calendrier vers Symbifox Mobile.

Report et « vu », d'où qu'ils viennent, effacent la notification du
téléphone ; la fin d'un report y refait sonner le rappel ; l'accusé durable
perd son report à la relance, sinon le filtre le recopiait sur la fiche.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRappelMobile(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.usager = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Rappel Mobile", "login": "rappel.mobile@test.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        debut = fields.Datetime.now() + timedelta(minutes=30)
        cls.evenement = cls.env["calendar.event"].create({
            "name": "Suivi hebdo",
            "start": debut, "stop": debut + timedelta(hours=1),
            "partner_ids": [(6, 0, [cls.usager.partner_id.id])],
        })
        cls.participant = cls.evenement.attendee_ids.filtered(
            lambda a: a.partner_id == cls.usager.partner_id)

    def _capter(self):
        envois = []
        Push = type(self.env["bf.email.unifiedpush"])
        appareil = type("Appareil", (), {"app_version": "3.5.0", "last_seen": fields.Datetime.now()})()
        return envois, patch.multiple(
            Push,
            _devices=lambda self_, owner: [appareil] if owner else [],
            _envoyer_a=lambda self_, appareils, payload: envois.append((self.usager.id, payload)) or 1,
        )

    def test_report_efface_la_notification_du_telephone(self):
        envois, rustine = self._capter()
        with rustine:
            self.env["calendar.attendee"].with_user(self.usager).bf_snooze(
                self.evenement.id, minutes=5)
        types = [p["type"] for _, p in envois]
        self.assertIn("rappel_clear", types)
        self.assertEqual(envois[types.index("rappel_clear")][0], self.usager.id)

    def test_vu_efface_la_notification_du_telephone(self):
        envois, rustine = self._capter()
        with rustine:
            self.env["calendar.attendee"].with_user(self.usager).bf_dismiss(self.evenement.id)
        self.assertIn("rappel_clear", [p["type"] for _, p in envois])

    def test_fin_du_report_resonne_sur_le_telephone_et_vide_l_accuse(self):
        passe = fields.Datetime.now() - timedelta(minutes=1)
        self.participant.write({"bf_snoozed_until": passe})
        self.participant._bf_record_reminder_ack(snoozed_until=passe)
        envois, rustine = self._capter()
        with rustine:
            self.env["calendar.attendee"]._bf_refire_expired_snoozes()
        rappels = [p for _, p in envois if p["type"] == "rappel"]
        self.assertEqual(len(rappels), 1)
        self.assertEqual(rappels[0]["event_id"], self.evenement.id)
        self.assertTrue(rappels[0]["key"])
        accuse = self.env["bf.calendar.reminder.ack"]._bf_find(
            self.usager.partner_id, self.evenement)
        self.assertFalse(accuse.snoozed_until)
        self.assertFalse(self.participant.bf_snoozed_until)

    def test_sans_mobile_rien_n_est_pousse(self):
        envois = []
        Push = type(self.env["bf.email.unifiedpush"])
        with patch.multiple(Push, _devices=lambda s, o: [],
                            _envoyer_a=lambda s, a, p: envois.append(p)):
            envoye = self.participant._bf_push_rappel_mobile(self.evenement)
        self.assertFalse(envoye)
        self.assertEqual(envois, [])

    def test_une_appli_trop_ancienne_garde_ntfy(self):
        """3.4.0 et avant ignorent « rappel » : le rappel ne doit pas leur être
        confié, sinon le téléphone ne recevrait plus rien."""
        envois = []
        Push = type(self.env["bf.email.unifiedpush"])
        ancien = type("Appareil", (), {"app_version": "3.4.0", "last_seen": fields.Datetime.now()})()
        with patch.multiple(Push, _devices=lambda s, o: [ancien],
                            _envoyer_a=lambda s, a, p: envois.append(p)):
            envoye = self.participant._bf_push_rappel_mobile(self.evenement)
        self.assertFalse(envoye)
        self.assertEqual(envois, [])

    def test_envoi_echoue_retombe_sur_ntfy(self):
        """Aucun appareil n'a accepté (panne, 410) : le rappel n'est pas « parti »."""
        Push = type(self.env["bf.email.unifiedpush"])
        a_jour = type("Appareil", (), {"app_version": "3.5.0", "last_seen": fields.Datetime.now()})()
        with patch.multiple(Push, _devices=lambda s, o: [a_jour], _envoyer_a=lambda s, a, p: 0):
            self.assertFalse(self.participant._bf_push_rappel_mobile(self.evenement))

    def test_ancien_telephone_actif_garde_ntfy_aussi(self):
        Push = type(self.env["bf.email.unifiedpush"])
        maintenant = fields.Datetime.now()
        a_jour = type("Appareil", (), {"app_version": "3.5.0", "last_seen": maintenant})()
        ancien = type("Appareil", (), {"app_version": "3.3.0", "last_seen": maintenant})()
        envois = []
        with patch.multiple(Push, _devices=lambda s, o: [a_jour, ancien],
                            _envoyer_a=lambda s, a, p: envois.append(p) or 1):
            envoye = self.participant._bf_push_rappel_mobile(self.evenement)
        self.assertEqual(len(envois), 1)   # le téléphone à jour a son rappel...
        self.assertFalse(envoye)           # ... et ntfy part aussi pour l'ancien.

    def test_vieil_appareil_oublie_ne_bloque_pas(self):
        Push = type(self.env["bf.email.unifiedpush"])
        a_jour = type("Appareil", (), {"app_version": "3.5.0", "last_seen": fields.Datetime.now()})()
        oublie = type("Appareil", (), {"app_version": "2.44.0",
                                       "last_seen": fields.Datetime.now() - timedelta(days=30)})()
        with patch.multiple(Push, _devices=lambda s, o: [a_jour, oublie], _envoyer_a=lambda s, a, p: 1):
            self.assertTrue(self.participant._bf_push_rappel_mobile(self.evenement))
