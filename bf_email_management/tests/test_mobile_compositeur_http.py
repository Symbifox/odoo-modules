"""Les routes de l'API mobile qui demandent un vrai HTTP.

Ce que l'ORM ne voit pas : le décorateur d'authentification, la lecture des
paramètres dans le corps JSON, la traduction d'un refus en 400 lisible par
l'app, et le fait que la route existe tout court.

Le composeur deuxième version et le
signalement de pourriel.
"""
import json
from datetime import timedelta
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.tests import HttpCase, tagged

from .common import build_rfc822
from .test_imap_folders_and_uid_guard import FakeImap

BASE = "/bf_email_management/mobile/v1"
IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"


class MobileHttpMixin:
    """Le jeton au porteur et les deux verbes, partagés par les classes."""

    def _auth(self):
        return {"Authorization": "Bearer %s" % self.device.device_token}

    def _get(self, path, headers=None):
        return self.url_open(BASE + path, headers=headers or {}, timeout=30)

    def _post(self, path, payload, headers=None):
        merged = {"Content-Type": "application/json", **(headers or {})}
        return self.url_open(BASE + path, data=json.dumps(payload).encode(),
                             headers=merged, timeout=30)


@tagged("post_install", "-at_install")
class TestCompositeurHttp(MobileHttpMixin, HttpCase):

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


@tagged("post_install", "-at_install")
class TestPourrielHttp(MobileHttpMixin, HttpCase):
    """Les deux routes du signalement de pourriel, par HTTP.

    Les essais de `test_mobile_pourriel` appellent les méthodes du
    modèle : aucun ne traversait les deux routes neuves. Ici : le décorateur
    d'authentification, `email_ids` lu dans le corps JSON, un refus traduit en
    400 lisible, `grouped` honoré jusqu'aux pastilles, et le seul chemin par
    lequel `block_sender` arrive en CHAÎNE — un POST form-encodé.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Porteur Pourriel",
            "login": "mobile.pourriel@test.invalid",
            "email": "pourriel@test.invalid",
            "tz": "America/Montreal",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.account = cls.env["bf.email.account"].create({
            "name": "Boîte pourriel", "user_id": cls.owner.id,
            "host": "imap.test.invalid", "port": 993,
            "login": "pourriel@test.invalid", "password": "x",
            "state": "connected",
            "folder_cache": json.dumps([
                {"name": "INBOX", "special": []},
                {"name": "Junk", "special": ["\\Junk"]},
            ]),
        })
        cls.spam = cls._courriel(
            subject="Buy cheap domains now", sender="Vendeur <promo@spamhttp.test>",
            uid="311")
        # Un fil de DEUX messages, pour que « replié » et « à plat » ne donnent
        # pas le même compte : c'est tout l'enjeu de `grouped`.
        cls.fil_a = cls._courriel(
            subject="Question", sender="client@acme.test", uid="312",
            root="<fil-http@test.invalid>")
        cls.fil_b = cls._courriel(
            subject="Re: Question", sender="client@acme.test", uid="313",
            root="<fil-http@test.invalid>")
        # Un envoi, pour le refus. Hors INBOX et hors « Sent » : il ne doit pas
        # peser sur les pastilles comptées plus bas.
        cls.envoi = cls._courriel(
            subject="Ma réponse", sender="pourriel@test.invalid", uid="314",
            direction="out", in_inbox=False, folder="Archives/2026")
        cls.device = cls.env["bf.email.mobile.device"]._issue(
            cls.owner.id, name="Appareil pourriel")
        cls.env.cr.flush()

    @classmethod
    def _courriel(cls, subject, sender, uid, direction="in", root=False,
                  in_inbox=True, folder="INBOX"):
        return cls.env["bf.email"].with_user(cls.owner).create({
            "subject": subject, "email_from": sender,
            "email_to": "pourriel@test.invalid",
            "direction": direction, "status": "new", "source": "imap",
            "account_id": cls.account.id, "user_id": cls.owner.id,
            "imap_in_inbox": in_inbox, "imap_folder": folder, "imap_uid": uid,
            "message_id_header": "<pourriel-%s@test.invalid>" % uid,
            "thread_root_id": root,
            "date": "2026-10-2%s 12:00:00" % uid[-1],
            "raw_rfc822": build_rfc822(subject, sender,
                                       "pourriel@test.invalid", "Corps."),
        })

    def _fake(self):
        return FakeImap(inbox={self.spam.imap_uid: self.spam.message_id_header})

    def test_les_routes_du_pourriel_refusent_un_appel_anonyme(self):
        for chemin in ("/report/options", "/report"):
            with self.subTest(chemin=chemin):
                reponse = self._post(chemin, {"email_ids": [self.spam.id]})
                self.assertEqual(reponse.status_code, 401)
        self.spam.invalidate_recordset()
        self.assertFalse(self.spam.report_kind, "un appel anonyme n'a rien posé")

    def test_les_options_lisent_email_ids_dans_le_corps_json(self):
        reponse = self._post("/report/options", {"email_ids": [self.spam.id]},
                             self._auth())
        self.assertEqual(reponse.status_code, 200, reponse.text)
        corps = reponse.json()
        self.assertTrue(corps["ok"])
        self.assertEqual(corps["count"], 1, "la liste est bien lue dans le corps")
        self.assertEqual(corps["senders"], ["promo@spamhttp.test"])
        self.assertEqual(corps["junk_folder"], "Junk")
        self.assertTrue(corps["block_offered"])

    def test_un_envoi_est_refuse_des_deux_cotes(self):
        options = self._post("/report/options", {"email_ids": [self.envoi.id]},
                             self._auth())
        self.assertEqual(options.status_code, 200, options.text)
        self.assertFalse(options.json()["ok"])
        self.assertIn("envois", options.json()["blocked"])
        pose = self._post("/report", {"email_ids": [self.envoi.id]}, self._auth())
        self.assertEqual(pose.status_code, 400, pose.text)
        self.assertIn("envois", pose.json()["error"],
                      "un refus sort en 400 lisible, pas en 500")

    def test_les_pastilles_de_report_suivent_grouped(self):
        """🔴 La route appelait `_mobile_counts()` d'office : l'appli en vue à
        plat affichait « Boîte de réception · 6 » au-dessus de cinq lignes."""
        with patch(f"{IMAP}.open_connection", return_value=self._fake()):
            reponse = self._post(
                "/report", {"email_ids": [self.spam.id], "grouped": False},
                self._auth())
        self.assertEqual(reponse.status_code, 200, reponse.text)
        rendu = reponse.json()["counts"]["inbox"]
        plat = self._get("/counts?grouped=0", self._auth()).json()["counts"]["inbox"]
        replie = self._get("/counts?grouped=1", self._auth()).json()["counts"]["inbox"]
        self.assertNotEqual(plat, replie,
                            "le fil de deux messages doit séparer les deux "
                            "comptes, sinon l'essai ne distingue rien")
        self.assertEqual(rendu, plat,
                         "les pastilles rendues comptent ce que la vue affiche")

    def test_annuler_en_form_encode_remet_le_courriel_en_boite(self):
        """🔴 `/handle handled=_flag(...)` n'était mesuré par aucun
        essai.

        C'est le correctif qui fait fonctionner l'« Annuler » du signalement,
        sur une route qui sert TOUTE l'appli, et les deux essais d'annulation du
        lot appellent `mobile_set_handled` avec un booléen déjà typé : ils
        passent identiquement avec `bool()` ou `_flag()`. Form-encodé — le seul
        chemin par lequel la valeur arrive en CHAÎNE — `bool("false")` valait
        `True`, et le geste d'annulation RANGEAIT le courriel de nouveau au
        lieu de le défaire.
        """
        fake = self._fake()
        with patch(f"{IMAP}.open_connection", return_value=fake):
            pose = self._post("/report", {"email_ids": [self.spam.id]},
                              self._auth())
            self.assertEqual(pose.status_code, 200, pose.text)
            self.spam.invalidate_recordset()
            self.assertFalse(self.spam.active,
                             "le signalement sort la ligne de la boîte")
            self.assertEqual(self.spam.report_kind, "spam")
            annule = self.url_open(
                BASE + "/handle",
                data={"email_ids": str(self.spam.id), "handled": "false"},
                headers=self._auth(), timeout=30)
        self.assertEqual(annule.status_code, 200, annule.text)
        self.spam.invalidate_recordset()
        self.assertTrue(self.spam.active,
                        "« Annuler » remet le courriel en boîte ; `bool(\"false\")` "
                        "le rangeait une deuxième fois")
        self.assertFalse(self.spam.report_kind, "le signalement est défait")
        self.assertFalse(self.spam.is_handled)
        self.assertEqual(self.spam.imap_folder, "INBOX",
                         "le message revient aussi du côté du serveur")

    def test_block_sender_false_en_form_encode_ne_cree_aucune_regle(self):
        """🔴 `_body` retombe sur `kw` quand le Content-Type est mauvais : tout
        arrive en chaîne, et `bool("false")` créait une règle de boîte que la
        personne avait décochée — qu'`action_unhandle` ne retire pas."""
        with patch(f"{IMAP}.open_connection", return_value=self._fake()):
            reponse = self.url_open(
                BASE + "/report",
                data={"email_ids": str(self.spam.id), "kind": "spam",
                      "block_sender": "false"},
                headers=self._auth(), timeout=30)
        self.assertEqual(reponse.status_code, 200, reponse.text)
        self.spam.invalidate_recordset()
        self.assertEqual(self.spam.report_kind, "spam",
                         "le rangement demandé, lui, a bien eu lieu")
        self.assertFalse(self.env["bf.email.rule"].search([
            ("user_id", "=", self.owner.id),
            ("name", "ilike", "promo@spamhttp.test"),
        ]), "aucune règle : la case était décochée")
