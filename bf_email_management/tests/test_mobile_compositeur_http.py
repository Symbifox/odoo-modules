"""Les routes du composeur deuxième version, par HTTP.

Ce que l'ORM ne voit pas : le décorateur d'authentification, la lecture des
paramètres dans le corps JSON, la traduction d'un refus en 400 lisible par
l'app, et le fait que la route existe tout court.
"""
import json
from datetime import timedelta

import pytz

from odoo import fields
from odoo.tests import HttpCase, tagged

from .common import build_rfc822

BASE = "/bf_email_management/mobile/v1"


@tagged("post_install", "-at_install")
class TestCompositeurHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Porteur Composeur",
            "login": "mobile.compositeur@test.invalid",
            "email": "compositeur@test.invalid",
            "tz": "America/Montreal",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.account = cls.env["bf.email.account"].create({
            "name": "Boîte composeur", "user_id": cls.owner.id,
            "host": "imap.test.invalid", "port": 993,
            "login": "compositeur@test.invalid", "password": "x",
            "state": "connected",
        })
        Identity = cls.env["bf.email.identity"].sudo()
        Identity.search([("user_id", "=", cls.owner.id)]).unlink()
        cls.identite = Identity.create({
            "user_id": cls.owner.id, "name": "Porteur Composeur",
            "email": "compositeur@test.invalid", "verified": True,
            "is_default": True, "account_id": cls.account.id,
        })
        cls.email = cls.env["bf.email"].with_user(cls.owner).create({
            "subject": "Sujet composeur", "email_from": "Tiers <tiers@test.invalid>",
            "email_to": "compositeur@test.invalid, copie@test.invalid",
            "direction": "in", "status": "new",
            "source": "imap", "account_id": cls.account.id,
            "user_id": cls.owner.id, "imap_in_inbox": True,
            "message_id_header": "<compositeur-1@test.invalid>",
            "date": "2026-09-14 12:00:00",
            "raw_rfc822": build_rfc822("Sujet composeur", "tiers@test.invalid",
                                       "compositeur@test.invalid", "Corps."),
        })
        cls.device = cls.env["bf.email.mobile.device"]._issue(
            cls.owner.id, name="Appareil composeur")
        cls.env.cr.flush()

    def _auth(self):
        return {"Authorization": "Bearer %s" % self.device.device_token}

    def _get(self, path, headers=None):
        return self.url_open(BASE + path, headers=headers or {}, timeout=30)

    def _post(self, path, payload, headers=None):
        merged = {"Content-Type": "application/json", **(headers or {})}
        return self.url_open(BASE + path, data=json.dumps(payload).encode(),
                             headers=merged, timeout=30)

    def test_les_routes_neuves_refusent_un_appel_anonyme(self):
        for methode, chemin in (
                ("get", "/reply/prepare?email_id=%s&mode=reply" % self.email.id),
                ("get", "/scheduled"),
                ("post", "/scheduled/unschedule")):
            with self.subTest(chemin=chemin):
                reponse = (self._get(chemin) if methode == "get"
                           else self._post(chemin, {"id": 1}))
                self.assertEqual(reponse.status_code, 401)

    def test_config_annonce_le_composeur(self):
        corps = self._get("/config", self._auth()).json()
        self.assertEqual(corps["compose_api"], 2)
        self.assertEqual([i["email"] for i in corps["identities"]],
                         ["compositeur@test.invalid"])

    def test_preparer_une_reponse_a_tous(self):
        reponse = self._get("/reply/prepare?email_id=%s&mode=reply_all"
                            % self.email.id, self._auth())
        self.assertEqual(reponse.status_code, 200)
        corps = reponse.json()
        self.assertEqual(corps["to"], [{"name": "Tiers", "email": "tiers@test.invalid"}])
        self.assertEqual([c["email"] for c in corps["cc"]], ["copie@test.invalid"])
        self.assertEqual(corps["subject"], "Re: Sujet composeur")
        self.assertEqual(corps["identity_id"], self.identite.id)

    def test_preparer_sans_courriel_ou_celui_d_un_autre(self):
        self.assertEqual(self._get("/reply/prepare", self._auth()).status_code, 400)
        self.assertEqual(self._get("/reply/prepare?email_id=999999",
                                   self._auth()).status_code, 404)

    def test_repondre_avec_cci_objet_adresse_et_programmation(self):
        quand = fields.Datetime.now() + timedelta(days=1)
        ms = int(quand.replace(tzinfo=pytz.UTC).timestamp() * 1000)
        reponse = self._post("/reply", {
            "email_id": self.email.id, "mode": "reply",
            "body": "Réponse programmée par HTTP.",
            "to": ["Tiers <tiers@test.invalid>"], "cc": [],
            "bcc": ["cache@test.invalid"], "subject": "Objet retouché",
            "identity_id": self.identite.id, "scheduled_ms": ms,
            "client_token": "jeton-http-compositeur",
        }, self._auth())
        self.assertEqual(reponse.status_code, 200, reponse.text)
        corps = reponse.json()
        self.assertTrue(corps["scheduled"])
        liste = self._get("/scheduled", self._auth()).json()["scheduled"]
        self.assertEqual([s["id"] for s in liste], [corps["scheduled_id"]])
        self.assertEqual(liste[0]["subject"], "Objet retouché")
        retenu = self._post("/scheduled/unschedule", {"id": corps["scheduled_id"]},
                            self._auth())
        self.assertEqual(retenu.status_code, 200)
        self.assertEqual(self._get("/scheduled", self._auth()).json()["scheduled"], [])

    def test_une_heure_passee_rend_un_refus_lisible(self):
        passe = fields.Datetime.now() - timedelta(hours=2)
        ms = int(passe.replace(tzinfo=pytz.UTC).timestamp() * 1000)
        reponse = self._post("/compose", {
            "to": ["tiers@test.invalid"], "subject": "Trop tard",
            "body": "X", "scheduled_ms": ms,
        }, self._auth())
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("passée", reponse.json()["error"])

    def test_une_adresse_d_envoi_etrangere_rend_un_refus_lisible(self):
        reponse = self._post("/compose", {
            "to": ["tiers@test.invalid"], "subject": "Usurpation",
            "body": "X", "identity_id": 999999,
        }, self._auth())
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("adresse d'envoi", reponse.json()["error"])

    def test_un_envoi_refuse_ne_brule_pas_son_jeton(self):
        """🔴 Relecture adverse du 2026-09-16 : un refus rendu en 400 était un
        retour NORMAL pour Odoo, qui validait la transaction — jeton
        anti-doublon compris. Le téléphone corrigeait, renvoyait avec le même
        jeton, lisait « doublon » et affichait « envoyé » : rien n'était parti.
        """
        jeton = "jeton-refuse-puis-corrige"
        trop = ["cache%d@test.invalid" % i for i in range(60)]
        refus = self._post("/reply", {
            "email_id": self.email.id, "mode": "forward", "body": "Trop large.",
            "to": ["tiers@test.invalid"], "bcc": trop, "client_token": jeton,
        }, self._auth())
        self.assertEqual(refus.status_code, 400)
        Partner = self.env["res.partner"].sudo()
        self.assertFalse(Partner.search_count([("email", "=like", "cache%@test.invalid")]),
                         "le refus ne laisse aucune fiche derrière lui")
        corrige = self._post("/reply", {
            "email_id": self.email.id, "mode": "forward", "body": "Corrigé.",
            "to": ["tiers@test.invalid"], "client_token": jeton,
        }, self._auth())
        self.assertEqual(corrige.status_code, 200, corrige.text)
        self.assertFalse(corrige.json().get("duplicate"),
                         "le jeton d'un envoi refusé doit rester libre")
