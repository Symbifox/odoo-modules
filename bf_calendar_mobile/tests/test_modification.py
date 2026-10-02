"""Modifier une rencontre et ses rappels depuis le téléphone.

Les essais portent sur ce qui casserait sans bruit : un rappel inconnu
ignoré, la rencontre d'un collègue modifiée par identifiant deviné, une
récurrence déplacée que la synchro effacerait, un rappel d'office écrasé à la
création quand le téléphone n'a rien demandé.
"""

import json
import uuid
from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

BASE = "/bf_calendar/mobile/v1"


def _alarme(env, minutes, kind="notification"):
    Alarm = env["calendar.alarm"]
    alarm = Alarm.search([("alarm_type", "=", kind),
                          ("duration_minutes", "=", minutes)], limit=1)
    return alarm or Alarm.create({
        "name": "Banc %s %s" % (kind, minutes), "alarm_type": kind,
        "duration": minutes, "interval": "minutes",
    })


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestModification(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = [cls.env.ref("base.group_user").id]
        cls.user = cls.env["res.users"].create({
            "name": "Banc Modif", "login": "banc.modif.26073",
            "email": "banc.modif.26073@example.org", "tz": "America/Toronto",
            "groups_id": [(6, 0, groupes)],
        })
        cls.autre = cls.env["res.users"].create({
            "name": "Banc Modif Autre", "login": "banc.modif.autre.26073",
            "email": "banc.modif.autre.26073@example.org",
            "groups_id": [(6, 0, groupes)],
        })
        cls.Event = cls.env["calendar.event"]
        cls.base = datetime(2026, 10, 5, 14, 0, 0)
        cls.a15 = _alarme(cls.env, 15)
        cls.a60 = _alarme(cls.env, 60)
        cls.courriel = _alarme(cls.env, 1440, "email")

    def _event(self, user=None, **extra):
        user = user or self.user
        vals = {
            "name": "Rencontre à déplacer",
            "start": fields.Datetime.to_string(self.base),
            "stop": fields.Datetime.to_string(self.base + timedelta(hours=1)),
            "partner_ids": [(6, 0, user.partner_id.ids)],
            "user_id": user.id,
        }
        vals.update(extra)
        return self.Event.with_user(user).create(vals)

    def test_deplacer_change_l_heure_et_laisse_une_trace(self):
        event = self._event()
        nouveau = self.base + timedelta(days=1, hours=2)
        res = event.with_user(self.user).mobile_write({
            "start": fields.Datetime.to_string(nouveau),
            "stop": fields.Datetime.to_string(nouveau + timedelta(minutes=30)),
        })
        self.assertEqual(event.start, nouveau)
        self.assertEqual(event.stop, nouveau + timedelta(minutes=30))
        self.assertEqual(res["event"]["start"], "2026-10-06T16:00:00Z")
        self.assertIn("déplacé", event.message_ids[:1].body or "")

    def test_changer_seulement_le_titre_ne_dit_pas_deplace(self):
        event = self._event()
        event.with_user(self.user).mobile_write({"name": "Nouveau titre"})
        self.assertEqual(event.name, "Nouveau titre")
        self.assertNotIn("déplacé", event.message_ids[:1].body or "")

    def test_fin_avant_debut_refusee(self):
        event = self._event()
        with self.assertRaises(UserError):
            event.with_user(self.user).mobile_write({
                "stop": fields.Datetime.to_string(self.base - timedelta(hours=1)),
            })
        self.assertEqual(event.start, self.base)

    def test_champ_hors_liste_blanche_ignore(self):
        event = self._event()
        with self.assertRaises(UserError):
            event.with_user(self.user).mobile_write({"user_id": self.autre.id})
        self.assertEqual(event.user_id, self.user)

    def test_titre_vide_refuse(self):
        event = self._event()
        with self.assertRaises(UserError):
            event.with_user(self.user).mobile_write({"name": "  "})

    def test_rappels_remplaces_courriel_compris(self):
        event = self._event()
        event.with_user(self.user).mobile_write(
            {"alarm_ids": [self.a60.id, self.courriel.id]})
        self.assertEqual(set(event.alarm_ids.ids), {self.a60.id, self.courriel.id})
        detail = event.with_user(self.user).mobile_detail()
        self.assertEqual(set(detail["alarm_ids"]), {self.a60.id, self.courriel.id})
        # La fiche ne montre que les rappels qui sonnent sur l'appareil.
        self.assertEqual([a["minutes"] for a in detail["alarms"]], [60])

    def test_liste_vide_retire_tous_les_rappels(self):
        event = self._event(alarm_ids=[(6, 0, self.a15.ids)])
        event.with_user(self.user).mobile_write({"alarm_ids": []})
        self.assertFalse(event.alarm_ids)

    def test_rappel_inconnu_refuse_le_geste_entier(self):
        event = self._event(alarm_ids=[(6, 0, self.a15.ids)])
        with self.assertRaises(UserError):
            event.with_user(self.user).mobile_write({
                "name": "Ne doit pas passer",
                "alarm_ids": [self.a60.id, 999999999],
            })
        self.assertEqual(event.alarm_ids, self.a15)
        self.assertEqual(event.name, "Rencontre à déplacer")

    def test_recurrence_refusee(self):
        event = self._event(recurrency=True, rrule_type="weekly", count=3, mon=True,
                            end_type="count")
        self.assertTrue(event.recurrency)
        self.assertFalse(event.with_user(self.user).mobile_detail()["can_edit"])
        with self.assertRaises(UserError):
            event.with_user(self.user).mobile_write({
                "start": fields.Datetime.to_string(self.base + timedelta(hours=3)),
            })

    def test_creation_sans_rappels_garde_le_defaut(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.default_alarm_minutes", "15")
        res = self.Event.with_user(self.user).mobile_create({
            "name": "Défaut", "start": "2026-10-05 14:00:00",
            "stop": "2026-10-05 15:00:00",
        })
        event = self.Event.browse(res["event"]["id"])
        self.assertEqual(event.alarm_ids.mapped("duration_minutes"), [15])

    def test_creation_avec_liste_vide_n_a_aucun_rappel(self):
        res = self.Event.with_user(self.user).mobile_create({
            "name": "Sans rappel", "start": "2026-10-05 14:00:00",
            "stop": "2026-10-05 15:00:00", "alarm_ids": [],
        })
        self.assertFalse(self.Event.browse(res["event"]["id"]).alarm_ids)

    def test_creation_avec_rappels_choisis(self):
        res = self.Event.with_user(self.user).mobile_create({
            "name": "Choisis", "start": "2026-10-05 14:00:00",
            "stop": "2026-10-05 15:00:00", "alarm_ids": [self.a60.id],
        })
        self.assertEqual(self.Event.browse(res["event"]["id"]).alarm_ids, self.a60)

    def test_choix_des_rappels_tries_avec_le_defaut(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.default_alarm_minutes", "1,15")
        choix = self.Event.with_user(self.user).mobile_alarm_choices()
        minutes = [a["minutes"] for a in choix["alarms"]]
        self.assertEqual(minutes, sorted(minutes))
        self.assertIn(self.courriel.id, [a["id"] for a in choix["alarms"]])
        self.assertEqual(choix["default_minutes"], [1, 15])


@tagged("post_install", "-at_install", "bf_calendar_mobile")
class TestModificationHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = [cls.env.ref("base.group_user").id]

        def usager(login):
            return cls.env["res.users"].create({
                "name": login, "login": login, "email": "%s@example.org" % login,
                "groups_id": [(6, 0, groupes)],
            })

        cls.user = usager("agenda.modif.26073")
        cls.autre = usager("agenda.modif.autre.26073")
        Device = cls.env["bf.email.mobile.device"]
        cls.jeton = Device._issue(cls.user.id, name="Banc modif").device_token
        cls.a60 = _alarme(cls.env, 60)

    def _post(self, route, charge):
        return self.url_open(
            BASE + route, data=json.dumps(charge),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % self.jeton}, timeout=30)

    def _event(self, user):
        return self.env["calendar.event"].with_user(user).create({
            "name": "Rencontre HTTP", "start": "2026-10-05 14:00:00",
            "stop": "2026-10-05 15:00:00",
            "partner_ids": [(6, 0, user.partner_id.ids)], "user_id": user.id,
        })

    def test_ping_annonce_api_5(self):
        rep = self.url_open(BASE + "/ping", timeout=30)
        self.assertGreaterEqual(rep.json()["api"], 5)

    def test_modifier_par_la_route(self):
        event = self._event(self.user)
        rep = self._post("/event/write", {
            "event_id": event.id, "key": "",
            "values": {"start": "2026-10-05 16:00:00", "stop": "2026-10-05 17:00:00",
                       "alarm_ids": [self.a60.id]},
        })
        self.assertEqual(rep.status_code, 200, rep.text)
        self.assertEqual(rep.json()["event"]["start"], "2026-10-05T16:00:00Z")
        self.assertEqual(event.alarm_ids, self.a60)

    def test_rencontre_d_autrui_introuvable(self):
        event = self._event(self.autre)
        rep = self._post("/event/write", {
            "event_id": event.id, "values": {"name": "Détourné"},
        })
        self.assertEqual(rep.status_code, 404)
        self.assertEqual(event.name, "Rencontre HTTP")

    def test_refus_motive_en_400(self):
        event = self._event(self.user)
        rep = self._post("/event/write", {
            "event_id": event.id, "values": {"alarm_ids": [999999999]},
        })
        self.assertEqual(rep.status_code, 400)
        self.assertIn("rappel", rep.json()["detail"])

    def test_modification_rejouee_rend_la_premiere_reponse(self):
        event = self._event(self.user)
        cle = str(uuid.uuid4())
        charge = {"event_id": event.id, "client_uuid": cle,
                  "values": {"name": "Rejouée"}}
        a = self._post("/event/write", charge)
        event.sudo().write({"name": "Changée au bureau entre-temps"})
        b = self._post("/event/write", charge)
        self.assertEqual((a.status_code, b.status_code), (200, 200), a.text)
        self.assertTrue(b.json().get("replay"))
        # Le rejeu ne réécrit pas : le changement du bureau tient.
        self.assertEqual(event.name, "Changée au bureau entre-temps")

    def test_liste_des_rappels(self):
        rep = self.url_open(BASE + "/alarms", headers={
            "Authorization": "Bearer %s" % self.jeton}, timeout=30)
        self.assertEqual(rep.status_code, 200, rep.text)
        self.assertIn(self.a60.id, [a["id"] for a in rep.json()["alarms"]])
