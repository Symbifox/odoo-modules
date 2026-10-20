"""Langue des textes de l'archive SMS : chacun sort dans la langue de qui le lira.

La source du module est anglaise ; le français vit dans `i18n/fr_CA.po`. Ces essais
prouvent que :

- une note posée au chatter (SMS, appels, contact) par un geste de l'usager suit la
  langue de cet usager ;
- une note relayée sans session (webhook, sondeur) suit la langue du propriétaire de
  la ligne, et non celle de l'environnement qui relaie ;
- une notification poussée (UnifiedPush, FCM, Web Push) suit la langue de l'usager
  qui la reçoit ;
- le nom d'un appareil apparié suit la langue de son propriétaire ;
- les erreurs, les noms d'actions, l'export CSV et le résumé d'import suivent la
  langue de l'usager qui agit ;
- l'API mobile à jeton rend ses erreurs dans la langue du propriétaire de l'appareil,
  quelle que soit la langue annoncée par le téléphone ;
- un libellé calculé non stocké se relit dans chaque langue sans garder la première.

Chaque essai vérifie aussi la langue inverse : un texte resté français pour tous, ou
anglais pour tous, le fait tomber. Les essais activent fr_CA et en_CA et fixent la
langue de chaque usager et de chaque appel : selon la base, en_US est absente ou
seule, et l'administrateur peut être réglé en français.
"""

import base64
import json
import sys
import types
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.bf_sms_archive.models import fcm_transport

GROUPES = "base.group_user,bf_sms_archive.group_sms_user,project.group_project_user"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n0000").decode()


def _langues(env):
    for code in ("fr_CA", "en_CA"):
        env["res.lang"]._activate_lang(code)
    env["ir.module.module"]._load_module_terms(["bf_sms_archive"], ["fr_CA"], overwrite=True)


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _langues(cls.env)
        cls.fr = new_test_user(cls.env, login="essai_fr", groups=GROUPES, lang="fr_CA")
        cls.en = new_test_user(cls.env, login="essai_en", groups=GROUPES, lang="en_CA")
        cls.ligne = cls.env["sms.archive.line"].create({
            "label": "Ligne d'essai", "did": "5145550101", "owner_id": cls.fr.id,
            "sms_enabled": True,
        })
        cls.ligne_en = cls.env["sms.archive.line"].create({
            "label": "Test line", "did": "5145550102", "owner_id": cls.en.id,
            "sms_enabled": True,
        })
        cls.fil = cls.env["sms.archive.thread"].create({
            "phone_normalized": "+15145550111", "contact_name": "Contact un",
            "owner_id": cls.fr.id, "line_id": cls.ligne.id,
        })
        cls.fil2 = cls.env["sms.archive.thread"].create({
            "phone_normalized": "+15145550112", "contact_name": "Contact deux",
            "owner_id": cls.fr.id, "line_id": cls.ligne.id,
        })
        cls.fil_en = cls.env["sms.archive.thread"].create({
            "phone_normalized": "+15145550113", "contact_name": "Contact three",
            "owner_id": cls.en.id, "line_id": cls.ligne_en.id,
        })
        cls.recu, cls.envoye, cls.recu_en = cls.env["sms.archive.message"].create([
            {"thread_id": cls.fil.id, "message_hash": "essai-a", "direction": "in",
             "body": "Bonjour", "date_sent": "2026-10-19 12:00:00", "line_id": cls.ligne.id},
            {"thread_id": cls.fil2.id, "message_hash": "essai-b", "direction": "out",
             "body": "Salut", "date_sent": "2026-10-19 12:05:00", "line_id": cls.ligne.id},
            {"thread_id": cls.fil_en.id, "message_hash": "essai-c", "direction": "in",
             "body": "Hello", "date_sent": "2026-10-19 12:10:00", "line_id": cls.ligne_en.id},
        ])
        cls.projet = cls.env["project.project"].create({"name": "Projet d'essai"})
        cls.tache = cls.env["project.task"].create({"name": "Tâche d'essai", "project_id": cls.projet.id})

    # ── Note de chatter des SMS ───────────────────────────────────────
    def test_01_note_sms_dans_la_langue_de_qui_pose(self):
        """En-tête et sens du message suivent la langue du contexte."""
        deux = self.recu | self.envoye
        fr = str(deux.with_context(lang="fr_CA")._render_task_post_body())
        en = str(deux.with_context(lang="en_CA")._render_task_post_body())
        self.assertIn("sur 2 conversations", fr)
        self.assertIn("Reçu", fr)
        self.assertIn("Envoyé", fr)
        self.assertIn("across 2 conversations", en)
        self.assertIn("Received", en)
        self.assertNotIn("sur 2 conversations", en)

    # ── Relais automatique, sans session ──────────────────────────────
    def _relayer(self, message, fil, lang_env):
        fil.auto_post_task_ids = [(4, self.tache.id)]
        message.with_context(lang=lang_env)._auto_post_to_task()
        note = self.env["mail.message"].search([
            ("model", "=", "project.task"), ("res_id", "=", self.tache.id),
            ("message_type", "=", "comment"),
        ], limit=1)
        self.assertTrue(note, "le relais n'a rien posé")
        return str(note.body)

    def test_02a_relais_dans_la_langue_du_proprietaire(self):
        """Propriétaire fr_CA, environnement en_CA : la note est en français."""
        corps = self._relayer(self.recu, self.fil, "en_CA")
        self.assertIn("Reçu", corps)
        self.assertNotIn("Received", corps)

    def test_02b_contre_epreuve_proprietaire_anglophone(self):
        """Propriétaire en_CA, environnement fr_CA : la note est en anglais."""
        corps = self._relayer(self.recu_en, self.fil_en, "fr_CA")
        self.assertIn("Received", corps)
        self.assertNotIn("Reçu", corps)

    # ── Note de chatter des appels ────────────────────────────────────
    def test_03_note_appels_dans_la_langue_de_qui_pose(self):
        """« 1 appel », type de l'appel et lien d'écoute traduits."""
        appel = self.env["call.archive.call"].create({
            "thread_id": self.fil.id, "call_hash": "essai-appel", "call_type": "missed",
            "date": "2026-10-19 13:00:00", "duration": 0,
            "recording_url": "https://nc.example.com/audio.m4a",
        })
        fr = str(appel.with_context(lang="fr_CA")._render_task_post_body())
        en = str(appel.with_context(lang="en_CA")._render_task_post_body())
        self.assertIn("1 appel", fr)
        self.assertIn("Manqué", fr)
        self.assertIn("Écouter", fr)
        self.assertIn("1 call", en)
        self.assertIn("Missed", en)
        self.assertIn("Listen", en)

    # ── Notes au chatter du contact ───────────────────────────────────
    def _notes_du_contact(self, contact):
        # Posté sans `message_type` : c'est une « notification », pas un « comment ».
        return " ".join(self.env["mail.message"].search([
            ("model", "=", "res.partner"), ("res_id", "=", contact.id),
        ]).mapped(lambda m: str(m.body)))

    def test_04a_numero_inscrit_au_contact(self):
        """Note « Mobile … inscrit » dans la langue de qui rattache."""
        contact = self.env["res.partner"].create({"name": "Contact sans mobile"})
        self.fil.with_context(lang="fr_CA").write({"partner_id": contact.id})
        self.assertEqual(contact.mobile, "+15145550111")
        self.assertIn("inscrit depuis la Messagerie SMS", self._notes_du_contact(contact))
        autre = self.env["res.partner"].create({"name": "Contact without a mobile"})
        self.fil_en.with_context(lang="en_CA").write({"partner_id": autre.id})
        self.assertIn("added from SMS Messaging", self._notes_du_contact(autre))

    def test_04b_numero_different_signale(self):
        """Note « numéro non reporté » dans la langue de qui rattache."""
        contact = self.env["res.partner"].create({"name": "Contact au mobile", "mobile": "+15145550199"})
        self.fil2.with_context(lang="fr_CA").write({"partner_id": contact.id})
        self.assertIn("Numéro non reporté", self._notes_du_contact(contact))
        autre = self.env["res.partner"].create({"name": "Contact with a mobile", "mobile": "+15145550198"})
        self.fil_en.with_context(lang="en_CA").write({"partner_id": autre.id})
        self.assertIn("Number not copied", self._notes_du_contact(autre))

    # ── Champ `error` d'un envoi refusé ────────────────────────────────
    def _envoi_refuse(self, usager, lang, did):
        sans_sms = self.env["sms.archive.line"].create({
            "label": "Ligne sans SMS", "did": did, "owner_id": usager.id, "sms_enabled": False,
        })
        msg_id = self.env["sms.archive.message"].with_user(usager).with_context(
            lang=lang).action_send(sans_sms.id, "+15145550177", "Allo")
        message = self.env["sms.archive.message"].browse(msg_id)
        self.assertEqual(message.delivery_state, "failed")
        return message.error

    def test_05_erreur_d_envoi_stockee_dans_la_langue_de_l_expediteur(self):
        """La UserError rattrapée devient `error`, dans la langue de l'expéditeur."""
        self.assertEqual(self._envoi_refuse(self.fr, "fr_CA", "5145550103"),
                         "Ligne non activée pour les SMS.")
        self.assertEqual(self._envoi_refuse(self.en, "en_CA", "5145550104"),
                         "Line not enabled for SMS.")

    # ── Nom d'appareil par défaut ─────────────────────────────────────
    def test_06a_appareil_nomme_dans_la_langue_de_son_proprietaire(self):
        """Appariement depuis l'API mobile : le nom suit le propriétaire, pas l'environnement."""
        Device = self.env["sms.archive.mobile.device"]
        self.assertEqual(Device.with_context(lang="en_CA")._issue(self.fr.id).name, "Appareil Android")
        self.assertEqual(Device.with_context(lang="fr_CA")._issue(self.en.id).name, "Android device")

    def test_06b_defaut_du_champ_traduit(self):
        """Le défaut du champ passe par le catalogue."""
        Device = self.env["sms.archive.mobile.device"].sudo()
        self.assertEqual(Device.with_context(lang="fr_CA").create({"user_id": self.fr.id}).name,
                         "Appareil Android")
        self.assertEqual(Device.with_context(lang="en_CA").create({"user_id": self.en.id}).name,
                         "Android device")

    # ── Notifications poussées ────────────────────────────────────────
    def _piece_seule(self, fil, ligne, empreinte):
        """Un message entrant sans texte : une pièce jointe seule."""
        return self.env["sms.archive.message"].create({
            "thread_id": fil.id, "message_hash": empreinte, "direction": "in",
            "body": False, "date_sent": "2026-10-19 14:00:00", "line_id": ligne.id,
        })

    def _cas_push(self):
        """(message, langue de l'environnement, corps attendu) : le destinataire décide."""
        return [
            (self._piece_seule(self.fil, self.ligne, "essai-piece-fr"), "en_CA", "📎 Pièce jointe"),
            (self._piece_seule(self.fil_en, self.ligne_en, "essai-piece-en"), "fr_CA", "📎 Attachment"),
        ]

    def test_07a_unifiedpush_dans_la_langue_du_destinataire(self):
        """UnifiedPush : texte du destinataire."""
        Push = self.env["sms.archive.unifiedpush"]
        for message, lang_env, attendu in self._cas_push():
            envois = []
            with patch.object(type(Push), "_devices", lambda self, owner: owner), \
                    patch.object(type(Push), "_send", lambda self, user, charge: envois.append(charge)):
                Push.with_context(lang=lang_env)._notify_new_message(message.with_context(lang=lang_env))
            self.assertTrue(envois)
            self.assertEqual(envois[0]["body"], attendu)

    def test_07b_fcm_dans_la_langue_du_proprietaire_de_l_appareil(self):
        """FCM : texte du propriétaire de l'appareil."""
        Device = self.env["sms.archive.mobile.device"].sudo()
        Device.create({"user_id": self.fr.id, "name": "Pixel fr", "fcm_token": "jeton-fcm-fr"})
        Device.create({"user_id": self.en.id, "name": "Pixel en", "fcm_token": "jeton-fcm-en"})
        Fcm = self.env["sms.archive.fcm"]

        class Reponse:
            status_code = 200
            text = ""

        sa = {"project_id": "essai", "client_email": "essai@example.com", "private_key": "cle"}
        for message, lang_env, attendu in self._cas_push():
            corps = []

            def poste(url, headers=None, data=None, timeout=None):
                corps.append(json.loads(data))
                return Reponse()

            with patch.object(type(Fcm), "_sa_info", lambda self: sa), \
                    patch.object(type(Fcm), "_access_token", lambda self, sa: "jeton"), \
                    patch.object(fcm_transport.requests, "post", side_effect=poste):
                Fcm.with_context(lang=lang_env)._notify_new_message(message.with_context(lang=lang_env))
            self.assertTrue(corps)
            self.assertEqual(corps[0]["message"]["data"]["body"], attendu)

    def test_07c_web_push_dans_la_langue_de_l_abonne(self):
        """Web Push du navigateur : texte de l'usager abonné."""
        Abonnement = self.env["sms.archive.push.subscription"]
        for usager in (self.fr, self.en):
            Abonnement.sudo().create({
                "user_id": usager.id, "endpoint": "https://push.example.com/%s" % usager.login,
                "p256dh": "cle", "auth": "secret",
            })
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_sms_archive.vapid_private_key", "cle-privee")
        for message, lang_env, attendu in self._cas_push():
            donnees = []
            pywebpush = types.ModuleType("pywebpush")
            pywebpush.WebPushException = type("WebPushException", (Exception,), {})
            pywebpush.webpush = lambda **kw: donnees.append(json.loads(kw["data"]))
            py_vapid = types.ModuleType("py_vapid")
            py_vapid.Vapid01 = type("Vapid01", (), {"from_pem": staticmethod(lambda pem: object())})
            with patch.dict(sys.modules, {"pywebpush": pywebpush, "py_vapid": py_vapid}):
                Abonnement.with_context(lang=lang_env)._notify_new_message(
                    message.with_context(lang=lang_env))
            self.assertTrue(donnees)
            self.assertEqual(donnees[0]["body"], attendu)

    # ── Export CSV ────────────────────────────────────────────────────
    def _csv(self, lang):
        action = self.env["sms.archive.thread"].with_context(lang=lang)._build_csv_download(
            self.envoye, filename="essai.csv")
        att_id = int(action["url"].split("/web/content/")[1].split("?")[0])
        texte = base64.b64decode(self.env["ir.attachment"].browse(att_id).datas).decode("utf-8-sig")
        return texte.splitlines()[:2]

    def test_08_csv_dans_la_langue_de_qui_exporte(self):
        """En-têtes et Oui/Non du CSV."""
        self.envoye.is_mms = True
        entete, ligne = self._csv("fr_CA")
        self.assertIn("Contenu", entete)
        self.assertTrue(ligne.rstrip().endswith("Oui"), ligne)
        entete, ligne = self._csv("en_CA")
        self.assertIn("Content", entete)
        self.assertTrue(ligne.rstrip().endswith("Yes"), ligne)

    # ── Résumé d'import ───────────────────────────────────────────────
    def _importer(self, usager, lang, numero):
        xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<calls count="1" backup_set="essai">'
            b'<call number="' + numero.encode() + b'" duration="12" date="1760000000000"'
            b' type="1" presentation="1" contact_name="(Unknown)" />'
            b'</calls>'
        )
        sorcier = self.env["sms.archive.import.wizard"].with_user(usager).with_context(
            lang=lang).create({"file_data": base64.b64encode(xml), "file_name": "appels.xml"})
        sorcier.action_import()
        return sorcier.result_message

    def test_09_resume_d_import_dans_la_langue_de_qui_importe(self):
        """Le résumé du sorcier d'import."""
        fr = self._importer(self.fr, "fr_CA", "+15145550188")
        self.assertIn("Import terminé :", fr)
        self.assertIn("Appels traités : 1", fr)
        en = self._importer(self.en, "en_CA", "+15145550189")
        self.assertIn("Import complete:", en)
        self.assertIn("Calls processed: 1", en)

    # ── Notification du sorcier de pose ───────────────────────────────
    def test_10_notification_du_sorcier(self):
        """Titre et message de la notification."""
        sorcier = self.env["sms.archive.post.to.task.wizard"].with_context(lang="fr_CA").create({
            "message_ids": [(6, 0, self.recu.ids)],
            "target_reference": f"project.task,{self.tache.id}",
        })
        params = sorcier.action_post()["params"]
        self.assertEqual(params["title"], "Posté sur la fiche")
        self.assertIn("élément(s) posté(s) sur « ", params["message"])
        anglais = self.env["sms.archive.post.to.task.wizard"].with_context(lang="en_CA").create({
            "message_ids": [(6, 0, self.envoye.ids)],
            "target_reference": f"project.task,{self.tache.id}",
        }).action_post()["params"]
        self.assertEqual(anglais["title"], "Posted to the record")
        self.assertIn('item(s) posted to "', anglais["message"])

    # ── Erreurs ───────────────────────────────────────────────────────
    def test_11a_conversation_introuvable(self):
        Thread = self.env["sms.archive.thread"]
        with self.assertRaisesRegex(UserError, "Conversation introuvable"):
            Thread.with_context(lang="fr_CA").get_conversation(987654321)
        with self.assertRaisesRegex(UserError, "Conversation not found"):
            Thread.with_context(lang="en_CA").get_conversation(987654321)

    def test_11b_ligne_d_envoi_introuvable(self):
        Msg = self.env["sms.archive.message"]
        with self.assertRaisesRegex(UserError, "Ligne d'envoi introuvable"):
            Msg.with_context(lang="fr_CA").action_send(987654321, "+15145550177", "Allo")
        with self.assertRaisesRegex(UserError, "Sending line not found"):
            Msg.with_context(lang="en_CA").action_send(987654321, "+15145550177", "Hello")

    def test_11c_url_publique_manquante(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_sms_archive.public_base_url", "")
        with self.assertRaisesRegex(UserError, "Réglez d'abord"):
            self.ligne.with_context(lang="fr_CA").action_configure_webhook()
        with self.assertRaisesRegex(UserError, "First set"):
            self.ligne.with_context(lang="en_CA").action_configure_webhook()

    def test_11d_appareil_cree_a_la_main(self):
        Device = self.env["sms.archive.mobile.device"]
        with self.assertRaisesRegex(AccessError, "ne se crée qu'en s'appariant"):
            Device.with_user(self.fr).with_context(lang="fr_CA").create({"user_id": self.fr.id})
        with self.assertRaisesRegex(AccessError, "only be created by pairing"):
            Device.with_user(self.en).with_context(lang="en_CA").create({"user_id": self.en.id})

    def test_11e_sorcier_sans_element(self):
        Wizard = self.env["sms.archive.post.to.task.wizard"]
        for lang, motif in (("fr_CA", "Aucun élément sélectionné"), ("en_CA", "No item selected")):
            sorcier = Wizard.with_context(lang=lang).create({
                "target_reference": f"project.task,{self.tache.id}",
            })
            with self.assertRaisesRegex(UserError, motif):
                sorcier.action_post()

    # ── Noms d'actions ────────────────────────────────────────────────
    def test_12_noms_d_actions(self):
        """Noms des fenêtres ouvertes par les boutons, dans la langue de l'usager.

        « Conversations (%s) » est identique dans les deux langues : rien à prouver."""
        fil = self.fil.with_context(lang="fr_CA")
        self.assertEqual(fil.action_view_messages()["name"], f"Messages : {fil.display_name}")
        self.assertEqual(fil.action_view_calls()["name"], f"Appels : {fil.display_name}")
        self.assertEqual(self.fil.with_context(lang="en_CA").action_view_calls()["name"],
                         f"Calls: {fil.display_name}")
        self.assertEqual(self.tache.with_context(lang="fr_CA").action_open_sms_links()["name"],
                         "SMS rattachés (Tâche d'essai)")
        self.assertEqual(self.recu.with_context(lang="fr_CA").action_post_to_task()["name"],
                         "Poster sur une fiche")
        appel = self.env["call.archive.call"].create({
            "thread_id": self.fil.id, "call_hash": "essai-nom", "call_type": "incoming",
            "date": "2026-10-19 15:00:00",
        })
        self.assertEqual(appel.with_context(lang="fr_CA").action_post_to_task()["name"],
                         "Poster sur une fiche")

    # ── Libellés calculés non stockés ─────────────────────────────────
    def test_13_fiche_supprimee_relue_dans_chaque_langue(self):
        """Le nom d'une fiche supprimée se relit dans chaque langue."""
        jetable = self.env["project.task"].create({"name": "Jetable", "project_id": self.projet.id})
        self.recu._post_to_record(jetable)
        lien = self.recu.link_ids.filtered(lambda l: l.res_id == jetable.id)
        jetable.unlink()
        self.assertEqual(lien.with_context(lang="en_CA").res_name, "Deleted record")
        self.assertEqual(lien.with_context(lang="fr_CA").res_name, "Fiche supprimée")

    def test_16_marqueur_de_jeton_relu_dans_chaque_langue(self):
        """URL de rappel sans jeton : le marqueur se relit dans chaque langue."""
        self.env.cr.execute("UPDATE sms_archive_line SET webhook_token = '' WHERE id = %s",
                            [self.ligne.id])
        self.ligne.invalidate_recordset()
        ligne = self.ligne.sudo()
        self.assertIn("token=<token>", ligne.with_context(lang="en_CA").webhook_url)
        self.assertIn("token=<jeton>", ligne.with_context(lang="fr_CA").webhook_url)


@tagged("bf_sms_archive", "post_install", "-at_install")
class TestLangueApiMobile(HttpCase):
    """API mobile à jeton : sans session web, les erreurs rendues à l'app sortent dans
    la langue du propriétaire de l'appareil, quelle que soit la langue que le
    téléphone annonce."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _langues(cls.env)
        cls.fr = new_test_user(cls.env, login="essai_api_fr", groups=GROUPES, lang="fr_CA")
        cls.en = new_test_user(cls.env, login="essai_api_en", groups=GROUPES, lang="en_CA")
        Line = cls.env["sms.archive.line"]
        cls.ligne_fr = Line.create({
            "label": "Ligne API", "did": "5145550121", "owner_id": cls.fr.id,
            "sms_enabled": True, "mms_enabled": False,
        })
        cls.ligne_en = Line.create({
            "label": "API line", "did": "5145550122", "owner_id": cls.en.id,
            "sms_enabled": True, "mms_enabled": False,
        })
        Device = cls.env["sms.archive.mobile.device"]
        cls.jeton_fr = Device._issue(cls.fr.id, name="Tel fr").device_token
        cls.jeton_en = Device._issue(cls.en.id, name="Tel en").device_token

    def _send(self, jeton, accept, charge):
        return self.url_open(
            "/bf_sms_archive/mobile/v1/send", data=json.dumps(charge),
            headers={"Content-Type": "application/json", "Authorization": "Bearer %s" % jeton,
                     "Accept-Language": accept},
        )

    def test_12a_erreur_dans_la_langue_du_proprietaire(self):
        """Téléphone en anglais, propriétaire fr_CA : erreur en français."""
        r = self._send(self.jeton_fr, "en-CA,en;q=0.9",
                       {"phone": "+15145550177", "line_id": self.ligne_fr.id, "media": "x"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["error"], "Pièces jointes invalides.")

    def test_12b_contre_epreuve_proprietaire_anglophone(self):
        """Téléphone en français, propriétaire en_CA : erreur en anglais."""
        r = self._send(self.jeton_en, "fr-CA,fr;q=0.9",
                       {"phone": "+15145550177", "line_id": self.ligne_fr.id, "media": "x"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["error"], "Invalid attachments.")

    def _sans_mms(self, jeton, ligne, accept):
        r = self._send(jeton, accept, {
            "phone": "+15145550178", "line_id": ligne.id, "body": "photo",
            "media": [{"filename": "p.png", "content_type": "image/png", "data_b64": PNG}],
        })
        self.assertEqual(r.status_code, 400, r.text)
        return r.json()["error"]

    def test_12c_ligne_sans_mms(self):
        """Refus d'une pièce jointe sur une ligne sans MMS, dans la langue du propriétaire."""
        self.assertIn("n'envoie pas de MMS", self._sans_mms(self.jeton_fr, self.ligne_fr, "en-CA"))
        self.assertIn("does not send MMS", self._sans_mms(self.jeton_en, self.ligne_en, "fr-CA"))
