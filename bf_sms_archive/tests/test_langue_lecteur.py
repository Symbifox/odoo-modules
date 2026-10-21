"""Manifeste PWA, erreurs VOIP.ms et catalogue `fr.po` de la messagerie SMS.

- Le manifeste `/bf_sms_archive/manifest.webmanifest` donnait un nom, une
  description et une `lang` français en dur : le téléphone d'un usager anglais
  installait « … — Messagerie SMS ». Ils suivent maintenant la langue de la
  requête (controllers/push_api.py:69-78).
- Les erreurs du transport VOIP.ms (models/voipms_transport.py) étaient des
  chaînes françaises hors catalogue ; elles passent par `self.env._`.
- `i18n/fr.po` ne portait qu'une poignée de termes : un usager fr_FR (ou fr_BE,
  fr_CH), qui lit `fr.po` et non `fr_CA.po`, voyait le module en anglais. Il est
  régénéré à partir de `fr_CA.po`.

Chaque essai lit le même texte en fr_CA puis en en_CA (ou en fr_FR pour le
catalogue `fr.po`). Les langues sont activées et épinglées : selon la base,
en_US est absente ou seule.
"""

import json
from unittest.mock import patch

import requests

from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.bf_sms_archive.models import voipms_transport

GROUPES = "base.group_user,bf_sms_archive.group_sms_user"


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestManifeste(HttpCase):
    """push_api.py:69-78 : nom, description et `lang` du manifeste selon la requête."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.fr = new_test_user(cls.env, login="essai_manifeste_fr", groups=GROUPES, lang="fr_CA")
        cls.en = new_test_user(cls.env, login="essai_manifeste_en", groups=GROUPES, lang="en_CA")

    def _manifeste(self, usager):
        self.authenticate(usager.login, usager.login)
        rep = self.url_open("/bf_sms_archive/manifest.webmanifest")
        self.assertEqual(rep.status_code, 200, rep.text[:300])
        return json.loads(rep.text)

    def test_manifeste_francais(self):
        manifeste = self._manifeste(self.fr)
        self.assertTrue(manifeste["name"].endswith(" — Messagerie SMS"), manifeste["name"])
        self.assertEqual(manifeste["description"], "Messagerie SMS/MMS via VOIP.ms")
        self.assertEqual(manifeste["lang"], "fr-CA")

    def test_manifeste_anglais(self):
        manifeste = self._manifeste(self.en)
        self.assertTrue(manifeste["name"].endswith(" SMS messaging"), manifeste["name"])
        self.assertNotIn("Messagerie", manifeste["name"])
        self.assertEqual(manifeste["description"], "SMS/MMS messaging via VOIP.ms")
        self.assertEqual(manifeste["lang"], "en-CA")


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestErreursVoipms(TransactionCase):
    """voipms_transport.py : chaque erreur levée suit la langue de qui appelle."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA", "fr_FR"):
            cls.env["res.lang"]._activate_lang(code)
        cls.Voipms = cls.env["sms.archive.voipms"]

    def _identifiants(self, usager="essai", mot="secret"):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_sms_archive.voipms_api_username", usager)
        ICP.set_param("bf_sms_archive.voipms_api_password", mot)

    def test_methode_non_autorisee(self):
        """voipms_transport.py:77 « Méthode VOIP.ms non autorisée »."""
        with self.assertRaisesRegex(ValueError, "^Méthode VOIP.ms non autorisée : piratage$"):
            self.Voipms.with_context(lang="fr_CA")._voipms_call("piratage")
        with self.assertRaisesRegex(ValueError, "^VOIP.ms method not allowed: piratage$"):
            self.Voipms.with_context(lang="en_CA")._voipms_call("piratage")

    def test_identifiants_absents(self):
        """voipms_transport.py:80-83 « Identifiants API VOIP.ms absents »."""
        self._identifiants("", "")
        with self.assertRaisesRegex(ValueError, "^Identifiants API VOIP.ms absents"):
            self.Voipms.with_context(lang="fr_CA")._voipms_call("getSMS")
        with self.assertRaisesRegex(ValueError, "^VOIP.ms API credentials missing"):
            self.Voipms.with_context(lang="en_CA")._voipms_call("getSMS")

    def test_echec_apres_trois_essais(self):
        """voipms_transport.py:116-120 « VOIP.ms … échec » ; aucun appel réseau ne part."""
        self._identifiants()

        def coupe(*args, **kwargs):
            raise requests.ConnectionError("hors ligne")

        for lang, attendu in (("fr_CA", "^VOIP.ms getSMS échec : ConnectionError : "),
                              ("en_CA", "^VOIP.ms getSMS failed: ConnectionError: ")):
            with patch.object(voipms_transport.requests, "get", side_effect=coupe), \
                    patch.object(voipms_transport.time, "sleep", lambda s: None), \
                    self.assertRaisesRegex(RuntimeError, attendu):
                self.Voipms.with_context(lang=lang)._voipms_call("getSMS")

    def _statut(self, lang, appel):
        with patch.object(type(self.Voipms), "_voipms_call",
                          lambda self, method, **kw: {"status": "invalid_did"}):
            return appel(self.Voipms.with_context(lang=lang))

    def test_statut_getdidsinfo(self):
        """voipms_transport.py:135 « getDIDsInfo : statut »."""
        with self.assertRaisesRegex(RuntimeError, "^getDIDsInfo : statut invalid_did$"):
            self._statut("fr_CA", lambda m: m._voipms_fetch_dids())
        with self.assertRaisesRegex(RuntimeError, "^getDIDsInfo: status invalid_did$"):
            self._statut("en_CA", lambda m: m._voipms_fetch_dids())

    def test_statut_getsms(self):
        """voipms_transport.py:178 « getSMS : statut »."""
        with self.assertRaisesRegex(RuntimeError, "^getSMS : statut invalid_did$"):
            self._statut("fr_CA", lambda m: m._voipms_get_sms())
        with self.assertRaisesRegex(RuntimeError, "^getSMS: status invalid_did$"):
            self._statut("en_CA", lambda m: m._voipms_get_sms())

    # ── Catalogue fr.po : la langue fr_FR lit `fr.po`, pas `fr_CA.po` ──
    def test_fr_po_couvre_les_erreurs_voipms(self):
        """i18n/fr.po régénéré : un usager fr_FR lit l'erreur en français."""
        with self.assertRaisesRegex(ValueError, "^Méthode VOIP.ms non autorisée : piratage$"):
            self.Voipms.with_context(lang="fr_FR")._voipms_call("piratage")

    def test_fr_po_couvre_les_textes_d_avant_le_lot(self):
        """i18n/fr.po régénéré : un texte ancien du module (absent de l'ancien fr.po) se lit
        aussi en français pour fr_FR."""
        Thread = self.env["sms.archive.thread"]
        with self.assertRaisesRegex(Exception, "Conversation introuvable"):
            Thread.with_context(lang="fr_FR").get_conversation(987654321)
        usager = new_test_user(self.env, login="essai_fr_fr", groups=GROUPES, lang="fr_FR")
        sans_sms = self.env["sms.archive.line"].create({
            "label": "Ligne fr_FR", "did": "5145550199", "owner_id": usager.id,
            "sms_enabled": False,
        })
        msg_id = self.env["sms.archive.message"].with_user(usager).with_context(
            lang="fr_FR").action_send(sans_sms.id, "+15145550177", "Allo")
        self.assertEqual(self.env["sms.archive.message"].browse(msg_id).error,
                         "Ligne non activée pour les SMS.")
