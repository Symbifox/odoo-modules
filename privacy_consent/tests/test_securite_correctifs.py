"""Lot « privacy » de  — correctifs de sécurité.

1. Les secrets DocuSeal et LibreSign (clé API, mot de passe, secrets de
   webhook) se lisaient déchiffrés par le simple utilisateur vie privée.
2. L'aperçu des destinataires de l'assistant de demande injectait le nom et le
   courriel du contact sans échappement dans un champ HTML non assaini.
3. Un consentement RETIRÉ se réaccordait depuis le lien du vieux courriel.
4. Une notification LibreSign signée se rejouait indéfiniment.
5. Une notification DocuSeal signée se rejouait dans sa fenêtre de 300 s.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


def _config_secrets(env):
    docuseal = env["privacy.docuseal.config"].sudo().create({
        "name": "DocuSeal d'essai",
        "api_url": "http://127.0.0.1:1",
        "api_key": "DS-CLE-SECRETE",
        "webhook_secret": "DS-WEBHOOK-SECRET",
    })
    libresign = env["privacy.libresign.config"].sudo().create({
        "name": "LibreSign d'essai",
        "nextcloud_url": "http://127.0.0.1:1",
        "username": "signataire",
        "password": "LS-MOT-DE-PASSE",
        "webhook_secret": "LS-WEBHOOK-SECRET",
    })
    return docuseal, libresign


@tagged("post_install", "-at_install", "privacy_consent", "")
class TestSecretsReservesAuxGestionnaires(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.docuseal, cls.libresign = _config_secrets(cls.env)
        cls.usager = new_test_user(
            cls.env, login="usager_vp",
            groups="base.group_user,privacy_consent.group_privacy_user",
        )
        cls.gestionnaire = new_test_user(
            cls.env, login="gestionnaire_vp",
            groups="base.group_user,privacy_consent.group_privacy_manager",
        )

    def test_simple_usager_ne_lit_pas_les_secrets(self):
        for config, champs in (
            (self.docuseal, ("api_key", "webhook_secret")),
            (self.libresign, ("password", "webhook_secret")),
        ):
            config = config.with_user(self.usager)
            for champ in champs:
                with self.assertRaises(AccessError, msg=f"{config._name}.{champ}"):
                    config.read([champ])
                with self.assertRaises(AccessError, msg=f"{config._name}.{champ}"):
                    config.web_read({champ: {}})
            self.assertFalse(
                set(champs) & set(config.fields_get()),
                "Le simple usager voit encore les champs secrets.",
            )
            # Lecture « tout le modèle » : aucun secret en clair ne sort.
            valeurs = str(config.read())
            for secret in ("DS-CLE-SECRETE", "LS-MOT-DE-PASSE", "WEBHOOK-SECRET"):
                self.assertNotIn(secret, valeurs)

    def test_gestionnaire_lit_et_modifie_la_configuration(self):
        """Le formulaire de configuration (menu des gestionnaires) marche."""
        docuseal = self.docuseal.with_user(self.gestionnaire)
        self.assertEqual(docuseal.read(["api_key"])[0]["api_key"], "DS-CLE-SECRETE")
        docuseal.write({"api_key": "DS-NOUVELLE-CLE", "webhook_secret": "DS-WH-2"})
        self.docuseal.invalidate_recordset()
        self.assertEqual(self.docuseal.sudo().api_key, "DS-NOUVELLE-CLE")
        self.assertNotEqual(
            self.docuseal.sudo().api_key_encrypted, "DS-NOUVELLE-CLE",
            "La clé doit rester chiffrée en base.",
        )

        libresign = self.libresign.with_user(self.gestionnaire)
        self.assertEqual(libresign.read(["password"])[0]["password"], "LS-MOT-DE-PASSE")
        libresign.write({"password": "LS-NOUVEAU"})
        self.libresign.invalidate_recordset()
        self.assertEqual(self.libresign.sudo().password, "LS-NOUVEAU")

    def test_le_code_interne_utilise_les_secrets_pour_le_simple_usager(self):
        """L'assistant d'envoi tourne sous le simple usager : l'appel sortant
        doit quand même porter la clé."""
        entetes = self.env["privacy.docuseal.interface"].with_user(
            self.usager)._get_headers(self.docuseal.with_user(self.usager))
        self.assertEqual(entetes["X-Auth-Token"], "DS-CLE-SECRETE")
        entetes = self.env["privacy.libresign.interface"].with_user(
            self.usager)._get_headers(self.libresign.with_user(self.usager))
        self.assertTrue(entetes["Authorization"].startswith("Basic "))


@tagged("post_install", "-at_install", "privacy_consent", "")
class TestApercuDestinatairesEchappe(TransactionCase):

    def _apercu(self, partenaires):
        assistant = self.env["privacy.consent.request.wizard"].new({
            "partner_ids": [(6, 0, partenaires.ids)],
        })
        return str(assistant.email_recipients_info)

    def test_nom_et_courriel_hostiles_sont_echappes(self):
        hostile = self.env["res.partner"].create({
            "name": '<img src=x onerror="alert(1)">',
            "email": '"><script>alert(2)</script>@example.invalid',
        })
        apercu = self._apercu(hostile)
        self.assertNotIn("<img", apercu)
        self.assertNotIn("<script", apercu)
        self.assertIn("&lt;img", apercu)

    def test_responsable_hostile_est_echappe(self):
        responsable = self.env["res.partner"].create({
            "name": "<b onmouseover=alert(3)>Parent</b>",
            "email": "parent@example.invalid",
        })
        mineur = self.env["res.partner"].create({
            "name": "<i>Enfant</i>",
            "is_minor_child": True,
            "legal_guardian_ids": [(6, 0, responsable.ids)],
        })
        apercu = self._apercu(mineur)
        self.assertNotIn("<b ", apercu)
        self.assertNotIn("<i>", apercu)

    def test_rendu_inchange_pour_des_noms_ordinaires(self):
        avec = self.env["res.partner"].create({
            "name": "Personne Essai", "email": "jean@example.invalid",
        })
        sans = self.env["res.partner"].create({"name": "Marie Roy"})
        apercu = self._apercu(avec | sans)
        self.assertEqual(
            apercu,
            "<ul style='margin:0; padding-left:16px;'>"
            "<li><strong>Personne Essai</strong> (jean@example.invalid)</li>"
            '<li>Marie Roy → <span style="color: #dc3545;">Aucun courriel</span></li>'
            "</ul>",
        )


@tagged("post_install", "-at_install", "privacy_consent", "")
class TestLienPublicApresRetrait(HttpCase):

    def setUp(self):
        super().setUp()
        self.Consent = self.env["privacy.consent"]
        self.purpose = self.env["privacy.purpose"].create({
            "code": "Tessai_RETRAIT",
            "name": "Finalité d'essai — retrait et lien public",
            "default_validity_days": 365,
            "plain_language_summary": "Résumé de la finalité.",
        })
        self.notice = self.env["privacy.notice"].create({
            "name": "Avis d'essai — retrait",
            "purpose_id": self.purpose.id,
            "body_fr": "<p>Avis courant</p>",
        })
        self.partner = self.env["res.partner"].create({
            "name": "Sujet du retrait", "email": "retrait@example.invalid",
        })
        self.consent = self.Consent.create({
            "subject_partner_id": self.partner.id,
            "purpose_id": self.purpose.id,
            "notice_id": self.notice.id,
            "status": "withdrawn",
            "withdrawn_at": fields.Datetime.now(),
        })
        self.url = f"/privacy/consent/{self.consent.id}/{self.consent.access_token}"

    def test_get_renouvellement_ferme(self):
        reponse = self.url_open(f"{self.url}/renew", allow_redirects=False)
        self.assertIn(reponse.status_code, (302, 303))
        self.assertIn("withdrawn_link_closed", reponse.headers.get("Location", ""))

    def test_post_force_ne_reaccorde_rien(self):
        avant = self.Consent.search_count([])
        reponse = self.url_open(
            f"{self.url}/renew", data={"notice_read": "1"}, allow_redirects=False,
        )
        self.assertNotEqual(reponse.status_code, 500)
        self.consent.invalidate_recordset()
        self.assertEqual(self.consent.status, "withdrawn")
        self.assertFalse(self.consent.renewed_to_id)
        self.assertEqual(self.Consent.search_count([]), avant)

    def test_page_lisible_sans_bouton_d_octroi(self):
        reponse = self.url_open(self.url)
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("You withdrew this consent", reponse.text)
        self.assertNotIn(f"{self.url}/renew", reponse.text)

    def test_jeton_errone_ou_exotique(self):
        faux = "x" * len(self.consent.access_token)
        for jeton in (faux, "jéton-été"):
            reponse = self.url_open(f"/privacy/consent/{self.consent.id}/{jeton}")
            self.assertEqual(reponse.status_code, 200)
            self.assertNotIn("Sujet du retrait", reponse.text)

    def test_comparaison_de_jeton(self):
        from ..controllers.portal import _jeton_valide

        self.assertTrue(_jeton_valide(self.consent, self.consent.access_token))
        self.assertFalse(_jeton_valide(self.consent, "é"))
        self.assertFalse(_jeton_valide(self.consent, ""))
        self.assertFalse(_jeton_valide(self.consent, None))
        self.assertFalse(_jeton_valide(self.Consent.browse(), "x"))


@tagged("post_install", "-at_install", "privacy_consent", "")
class TestRejeuWebhookLibresign(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["privacy.libresign.config"].sudo().search([]).unlink()
        _docuseal, self.libresign = _config_secrets(self.env)
        purpose = self.env["privacy.purpose"].create({
            "code": "Tessai_REJEU", "name": "Finalité d'essai — rejeu",
        })
        partner = self.env["res.partner"].create({"name": "Signataire"})
        self.consent = self.env["privacy.consent"].create({
            "subject_partner_id": partner.id,
            "purpose_id": purpose.id,
            "status": "granted",
            "granted_at": fields.Datetime.now(),
            "libresign_file_uuid": "uuid-",
            "libresign_status": "completed",
        })

    def _poster(self, corps):
        brut = json.dumps(corps).encode()
        signature = hmac.new(b"LS-WEBHOOK-SECRET", brut, hashlib.sha256).hexdigest()
        return self.url_open(
            "/privacy/libresign/webhook", data=brut,
            headers={"Content-Type": "application/json",
                     "X-LibreSign-Signature": signature},
        )

    def test_notification_rejouee_sans_effet(self):
        preuves = len(self.consent.evidence_ids) if "evidence_ids" in self.consent._fields else None
        messages = len(self.consent.message_ids)
        reponse = self._poster({"event": "file_signed", "file": {"uuid": "uuid-"}})
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json().get("message"), "Already processed")
        self.consent.invalidate_recordset()
        self.assertEqual(self.consent.status, "granted")
        self.assertEqual(len(self.consent.message_ids), messages)
        if preuves is not None:
            self.assertEqual(len(self.consent.evidence_ids), preuves)


@tagged("post_install", "-at_install", "privacy_consent", "")
class TestRejeuWebhookDocuseal(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["privacy.docuseal.config"].sudo().search([]).unlink()
        self.docuseal, _libresign = _config_secrets(self.env)
        purpose = self.env["privacy.purpose"].create({
            "code": "Tessai_REJEU_DS", "name": "Finalité d'essai — rejeu DocuSeal",
        })
        partner = self.env["res.partner"].create({"name": "Signataire DocuSeal"})
        self.consent = self.env["privacy.consent"].create({
            "subject_partner_id": partner.id,
            "purpose_id": purpose.id,
            "status": "granted",
            "granted_at": fields.Datetime.now(),
            "docuseal_submission_id": "4242",
            "docuseal_status": "completed",
        })

    def _poster(self, corps):
        brut = json.dumps(corps).encode()
        horodatage = str(int(time.time()))
        signature = hmac.new(b"DS-WEBHOOK-SECRET",
                             horodatage.encode() + b"." + brut,
                             hashlib.sha256).hexdigest()
        return self.url_open(
            "/privacy/docuseal/webhook", data=brut,
            headers={"Content-Type": "application/json",
                     "X-Docuseal-Signature": f"{horodatage}.{signature}"},
        )

    def test_notifications_rejouees_sans_effet(self):
        messages = len(self.consent.message_ids)
        preuves = len(self.consent.evidence_ids)
        with patch.object(
                type(self.env["privacy.docuseal.interface"]),
                "get_submission_documents",
                return_value=[{"name": "signe.pdf", "content": b"JVBERi0="}]) as telechargement:
            for evenement in ("submission.completed", "form.completed",
                              "submission.expired"):
                reponse = self._poster({"event_type": evenement,
                                        "data": {"id": 4242}})
                self.assertEqual(reponse.status_code, 200, evenement)
                self.assertEqual(reponse.json().get("message"),
                                 "Already processed", evenement)
        telechargement.assert_not_called()
        self.consent.invalidate_recordset()
        self.assertEqual(self.consent.status, "granted")
        self.assertEqual(self.consent.docuseal_status, "completed")
        self.assertEqual(len(self.consent.message_ids), messages)
        self.assertEqual(len(self.consent.evidence_ids), preuves)

    def test_premiere_completion_toujours_traitee(self):
        self.consent.write({"status": "pending", "granted_at": False,
                            "docuseal_status": "pending"})
        reponse = self._poster({"event_type": "submission.completed",
                                "data": {"id": 4242}})
        self.assertEqual(reponse.status_code, 200)
        self.assertNotEqual(reponse.json().get("message"), "Already processed")
        self.consent.invalidate_recordset()
        self.assertEqual(self.consent.status, "granted")
        self.assertEqual(self.consent.docuseal_status, "completed")
