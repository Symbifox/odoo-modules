"""Avis à l'arrivée d'un courriel, dans Odoo.

Ce que ces tests éprouvent vraiment, dans l'ordre où ça peut casser :

1. L'interrupteur d'instance. Le cas qui compte n'est pas « valeur illisible »,
   c'est « clé ABSENTE » — l'état de toute installation neuve, donc celui de
   chaque locataire au prochain ``-u``.
2. Ce que la charge utile transporte. Elle ne doit porter qu'un identifiant :
   le bus diffuse au partenaire sans consulter la moindre règle
   d'enregistrement.
3. La décision de relever les lignes fraîches. Elle reposait sur la seule
   présence d'un appareil mobile ; comme il n'y en a plus aucun d'inscrit,
   un avis Odoo n'aurait jamais été calculé.
"""
import json
from unittest.mock import patch

from odoo.tests import tagged

from .common import MobileApiCase

BASE = "odoo.addons.bf_email_management.models.bf_email.BfEmail"


@tagged("post_install", "-at_install")
class TestPopupNotify(MobileApiCase):

    def setUp(self):
        super().setUp()
        self.Popup = self.env["bf.email.popup"]
        self.param = self.env["ir.config_parameter"].sudo()
        self.param.set_param("bf_email.popup_enabled", "1")
        self.account.popup_mode = "transient"
        self.account.popup_sticky_folders = False
        # Repère : les messages de bus posés avant ce point ne nous regardent
        # pas. La création des courriels du socle en produit.
        self.env.cr.precommit.run()
        self._watermark = self.env["bus.bus"].sudo().search(
            [], order="id desc", limit=1).id or 0

    # ------------------------------------------------------------------
    # Outils
    # ------------------------------------------------------------------
    def _popups(self):
        """Les charges utiles envoyées sur `bf_email/popup` depuis le repère.

        ⚠️ ``_sendone`` n'écrit rien tout de suite : il empile dans
        ``cr.precommit`` et la ligne n'existe qu'au déclenchement du crochet.
        Une transaction de test ne valide jamais, donc sans ce ``run()``
        explicite la table reste vide et TOUS les tests passeraient au vert
        sans rien éprouver.
        """
        self.env.cr.precommit.run()
        rows = self.env["bus.bus"].sudo().search([("id", ">", self._watermark)])
        out = []
        for row in rows:
            message = json.loads(row.message)
            if message.get("type") == "bf_email/popup":
                out.append((json.loads(row.channel), message["payload"]))
        return out

    def _new_inbound(self, count=1, folder="INBOX", offset=200):
        """``count`` courriels entrants tout neufs dans la boîte du propriétaire."""
        BfEmail = self.env["bf.email"].with_user(self.owner)
        recs = BfEmail.browse()
        for index in range(count):
            uid = str(offset + index)
            recs |= BfEmail.create({
                "subject": "Sujet %s" % uid,
                "email_from": "client@acme.test",
                "email_to": "owner@test.invalid",
                "direction": "in",
                "status": "new",
                "source": "imap",
                "account_id": self.account.id,
                "user_id": self.owner.id,
                "imap_in_inbox": True,
                "imap_folder": folder,
                "imap_uid": uid,
                "message_id_header": "<popup-%s@test.invalid>" % uid,
                "date": "2026-08-20 12:00:00",
            })
        return recs

    # ------------------------------------------------------------------
    # 1. L'interrupteur d'instance
    # ------------------------------------------------------------------
    def test_cle_absente_vaut_non(self):
        """Une installation neuve n'a pas la clé, et ne doit rien annoncer."""
        self.param.search([("key", "=", "bf_email.popup_enabled")]).unlink()
        self.Popup._notify_new_emails(self._new_inbound())
        self.assertFalse(self._popups(),
                         "clé absente : l'avis ne doit pas partir")

    def test_cle_vide_vaut_non(self):
        """Une clé posée puis vidée vaut non, pas « le défaut »."""
        self.param.set_param("bf_email.popup_enabled", "")
        self.Popup._notify_new_emails(self._new_inbound())
        self.assertFalse(self._popups())

    def test_cle_zero_vaut_non(self):
        self.param.set_param("bf_email.popup_enabled", "0")
        self.Popup._notify_new_emails(self._new_inbound())
        self.assertFalse(self._popups())

    def test_watching_faux_sans_le_parametre(self):
        """`_watching` porte l'interrupteur : sinon on relève pour rien."""
        self.param.set_param("bf_email.popup_enabled", "0")
        self.assertFalse(self.Popup._watching(self.owner))

    def test_reglage_instance_aller_retour(self):
        """Cocher, décocher, et que le « non » tienne.

        ⚠️ ``set_param(clé, False)`` SUPPRIME la rangée. Le fichier de
        réglages documente déjà le défaut qui en découle pour une case cochée
        par défaut ; on écrit donc « 0 » explicitement, et ce test le vérifie
        pour que le jour où le défaut du code changerait, la case ne se
        remette pas à cocher toute seule.
        """
        Settings = self.env["res.config.settings"]
        Settings.create({"bf_email_popup_enabled": True}).set_values()
        self.assertTrue(self.Popup._instance_enabled())
        Settings.create({"bf_email_popup_enabled": False}).set_values()
        self.assertFalse(self.Popup._instance_enabled())
        self.assertEqual(
            self.param.get_param("bf_email.popup_enabled"), "0",
            "le « non » doit être écrit, pas laissé à l'absence de rangée",
        )
        self.assertFalse(Settings.get_values()["bf_email_popup_enabled"])

    # ------------------------------------------------------------------
    # 2. Ce que la charge utile transporte
    # ------------------------------------------------------------------
    def test_charge_utile_sans_objet_ni_expediteur(self):
        """Le bus ne consulte aucune règle : il ne doit rien porter de lisible.

        C'est la raison d'être du canal tel qu'il est écrit. Si un jour
        quelqu'un ajoute l'objet « pour éviter un aller-retour », ce test doit
        l'arrêter.
        """
        rec = self._new_inbound()
        self.Popup._notify_new_emails(rec)
        popups = self._popups()
        self.assertEqual(len(popups), 1)
        _channel, payload = popups[0]
        self.assertEqual(payload["email_id"], rec.id)
        self.assertEqual(set(payload), {"kind", "email_id", "sticky"})
        serialised = json.dumps(payload)
        self.assertNotIn("Sujet", serialised)
        self.assertNotIn("acme.test", serialised)

    def test_avis_adresse_au_partenaire_du_proprietaire(self):
        rec = self._new_inbound()
        self.Popup._notify_new_emails(rec)
        channel, _payload = self._popups()[0]
        self.assertEqual(channel[1], "res.partner")
        self.assertEqual(channel[2], self.owner.partner_id.id)

    # ------------------------------------------------------------------
    # 3. Le tri : compte, puis dossier
    # ------------------------------------------------------------------
    def test_compte_a_aucun_avis_reste_muet(self):
        self.account.popup_mode = "none"
        self.Popup._notify_new_emails(self._new_inbound())
        self.assertFalse(self._popups())
        self.assertFalse(self.Popup._watching(self.owner))

    def test_compte_ephemere_donne_un_avis_non_collant(self):
        self.Popup._notify_new_emails(self._new_inbound())
        _channel, payload = self._popups()[0]
        self.assertEqual(payload["kind"], "mail")
        self.assertFalse(payload["sticky"])

    def test_dossier_suivi_rend_l_avis_collant(self):
        self.account.popup_sticky_folders = "Clients/Urgent, Direction"
        self.Popup._notify_new_emails(
            self._new_inbound(folder="Clients/Urgent"))
        _channel, payload = self._popups()[0]
        self.assertTrue(payload["sticky"])

    def test_dossier_suivi_insensible_a_la_casse_et_aux_espaces(self):
        """La saisie humaine met des espaces et se trompe de casse."""
        self.account.popup_sticky_folders = "  clients/URGENT ,Direction"
        self.Popup._notify_new_emails(
            self._new_inbound(folder="Clients/Urgent"))
        _channel, payload = self._popups()[0]
        self.assertTrue(payload["sticky"])

    def test_dossier_suivi_ne_rallume_pas_un_compte_muet(self):
        """Le champ resserre l'attention, il n'est pas un second interrupteur."""
        self.account.popup_mode = "none"
        self.account.popup_sticky_folders = "INBOX"
        self.Popup._notify_new_emails(self._new_inbound(folder="INBOX"))
        self.assertFalse(self._popups())

    # ------------------------------------------------------------------
    # 4. Le lot
    # ------------------------------------------------------------------
    def test_resume_au_dela_du_plafond(self):
        """Une reprise après panne ne doit pas empiler la boîte à l'écran."""
        self.Popup._notify_new_emails(self._new_inbound(count=6))
        popups = self._popups()
        self.assertEqual(len(popups), 1)
        _channel, payload = popups[0]
        self.assertEqual(payload["kind"], "batch")
        self.assertEqual(payload["count"], 6)
        self.assertFalse(payload["sticky"])

    def test_sous_le_plafond_chacun_son_avis(self):
        self.Popup._notify_new_emails(self._new_inbound(count=5))
        self.assertEqual(len(self._popups()), 5)

    def test_collants_et_ephemeres_comptes_separement(self):
        """Un lot d'éphémères ne doit pas noyer les quelques collants.

        Six éphémères passent en résumé ; les deux collants gardent chacun leur
        avis, sans quoi le dossier suivi ne servirait à rien un jour de reprise.
        """
        self.account.popup_sticky_folders = "Direction"
        recs = self._new_inbound(count=6, offset=300)
        recs |= self._new_inbound(count=2, folder="Direction", offset=400)
        self.Popup._notify_new_emails(recs)
        payloads = [payload for _channel, payload in self._popups()]
        collants = [p for p in payloads if p["sticky"]]
        ephemeres = [p for p in payloads if not p["sticky"]]
        self.assertEqual(len(collants), 2)
        self.assertTrue(all(p["kind"] == "mail" for p in collants))
        self.assertEqual(len(ephemeres), 1)
        self.assertEqual(ephemeres[0]["kind"], "batch")
        self.assertEqual(ephemeres[0]["count"], 6)

    # ------------------------------------------------------------------
    # 5. Ce qui ne mérite pas d'avis
    # ------------------------------------------------------------------
    def test_sortant_et_deja_traite_ignores(self):
        recs = self._new_inbound(count=2, offset=500)
        recs[0].direction = "out"
        recs[1].is_handled = True
        self.Popup._notify_new_emails(recs)
        self.assertFalse(self._popups())

    # ------------------------------------------------------------------
    # 6. L'accroche à l'ingestion — le défaut que la refonte corrige
    # ------------------------------------------------------------------
    def test_watching_vrai_sans_aucun_appareil_inscrit(self):
        """Aucun appareil n'a de point de poussée, et pourtant on veut l'avis.

        C'est l'état réel depuis le 2026-08-29 : les six points de poussée ont
        été vidés et bf_email.push_enabled est à 0. Le relevé des lignes
        fraîches ne peut donc plus dépendre de la présence d'un appareil.
        """
        self.env["bf.email.mobile.device"].sudo().search(
            [("user_id", "=", self.owner.id)]).write({"push_endpoint": False})
        self.assertTrue(self.Popup._watching(self.owner))

    def test_sync_account_annonce_sans_appareil(self):
        """Bout en bout : une passe qui ramène une ligne produit l'avis.

        La base ``_sync_account`` est remplacée par une fausse qui se contente
        de déposer un courriel, parce que ce qu'on éprouve ici est la décision
        de l'enrobage, pas l'IMAP.
        """
        self.env["bf.email.mobile.device"].sudo().search(
            [("user_id", "=", self.owner.id)]).write({"push_endpoint": False})

        def fake_sync(self_model, account):
            self._new_inbound(offset=600)
            return True

        with patch(f"{BASE}._sync_account", autospec=True, side_effect=fake_sync):
            self.env["bf.email"]._sync_account(self.account)

        popups = self._popups()
        self.assertEqual(len(popups), 1, "la passe doit produire un avis")
        self.assertEqual(popups[0][1]["kind"], "mail")

    def test_sync_account_muet_quand_personne_n_ecoute(self):
        """Ni appareil ni avis : l'enrobage doit repasser la main sans relever."""
        self.env["bf.email.mobile.device"].sudo().search(
            [("user_id", "=", self.owner.id)]).write({"push_endpoint": False})
        self.account.popup_mode = "none"

        def fake_sync(self_model, account):
            self._new_inbound(offset=700)
            return True

        with patch(f"{BASE}._sync_account", autospec=True, side_effect=fake_sync):
            self.env["bf.email"]._sync_account(self.account)

        self.assertFalse(self._popups())
