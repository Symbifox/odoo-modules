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
4. Le plafond de trente secondes (#25069, second lot). Il ne tient QUE si la
   charge utile porte l'horloge du serveur et la durée accordée : c'est ce
   couple, et non un délai côté client, qui empêche une deuxième fenêtre — ou
   un rejeu du bus au réveil du navigateur — de rallonger l'affichage.
5. Les deux gestes des boutons. Ils sont appelables par RPC comme n'importe
   quelle méthode publique, donc ce qu'on éprouve d'abord, c'est le refus de la
   ligne d'autrui.
"""
import json
import time
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.bf_email_management.models import (
    bf_email_imap,
    popup_transport,
)

from .common import MobileApiCase

BASE = "odoo.addons.bf_email_management.models.bf_email.BfEmail"
MOBILE = ("odoo.addons.bf_email_management.models.bf_email_mobile.BfEmailMobile")


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

    def _run_mirror_without_imap(self):
        """Le cron de miroir, sans toucher à un serveur de courriel.

        Le réveil des reports échus est une passe globale faite AVANT la boucle
        des comptes ; l'erreur de connexion est celle que la boucle sait
        attraper, donc le cron va jusqu'au bout comme en production.
        """
        with patch(
            "odoo.addons.bf_email_management.models.bf_email"
            ".bf_email_imap.open_connection",
            side_effect=bf_email_imap.ImapConnectionError("pas d'IMAP en test"),
        ):
            self.env["bf.email"]._cron_imap_mirror()

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
        self.assertEqual(
            set(payload),
            {"kind", "email_id", "sticky", "ttl_ms", "wake", "sent_ms"},
        )
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

    # ------------------------------------------------------------------
    # 7. Le plafond de trente secondes
    # ------------------------------------------------------------------
    def test_charge_utile_porte_l_horloge_du_serveur(self):
        """Sans `sent_ms`, le client ne peut compter que depuis l'affichage.

        Et compter depuis l'affichage, c'est accorder trente secondes NEUVES à
        chaque fenêtre ouverte, puis trente autres au rejeu du bus quand le
        navigateur se reconnecte le lendemain.
        """
        before = int(time.time() * 1000)
        self.Popup._notify_new_emails(self._new_inbound())
        after = int(time.time() * 1000)
        _channel, payload = self._popups()[0]
        self.assertGreaterEqual(payload["sent_ms"], before)
        self.assertLessEqual(payload["sent_ms"], after)

    def test_ephemere_huit_secondes_persistant_trente(self):
        self.Popup._notify_new_emails(self._new_inbound())
        _channel, payload = self._popups()[0]
        self.assertEqual(payload["ttl_ms"], 8000)

        self.account.popup_sticky_folders = "Direction"
        self.Popup._notify_new_emails(
            self._new_inbound(folder="Direction", offset=250))
        payloads = [p for _c, p in self._popups()]
        collant = [p for p in payloads if p["sticky"]][0]
        self.assertEqual(collant["ttl_ms"], 30000)

    def test_aucun_mode_ne_depasse_le_plafond(self):
        """Le plafond est une règle, pas une valeur qu'on relit dans la table.

        Le jour où quelqu'un allongera le mode persistant « juste un peu »,
        c'est ici que ça doit s'arrêter, pas dans le navigateur.
        """
        with patch.dict(popup_transport.POPUP_TTL_MS, {"sticky": 120000}):
            self.assertEqual(self.Popup._ttl_ms("sticky"), 30000)

    def test_mode_inconnu_retombe_sur_l_ephemere(self):
        self.assertEqual(self.Popup._ttl_ms("n'importe quoi"), 8000)

    def test_resume_nomme_ses_lignes_sans_les_decrire(self):
        """Le résumé porte des identifiants, jamais des noms.

        Le client relit ces lignes-là par l'ORM pour nommer les expéditeurs :
        les règles d'enregistrement s'appliquent alors comme partout ailleurs,
        ce que le bus, lui, ne sait pas faire.
        """
        self.Popup._notify_new_emails(self._new_inbound(count=6))
        _channel, payload = self._popups()[0]
        self.assertEqual(payload["kind"], "batch")
        self.assertEqual(len(payload["email_ids"]), 6)
        serialised = json.dumps(payload)
        self.assertNotIn("Sujet", serialised)
        self.assertNotIn("acme.test", serialised)

    def test_resume_borne_les_identifiants_joints(self):
        """Une reprise de cent lignes ne doit pas en porter cent."""
        self.Popup._notify_new_emails(self._new_inbound(count=12))
        _channel, payload = self._popups()[0]
        self.assertEqual(payload["count"], 12)
        self.assertEqual(
            len(payload["email_ids"]), popup_transport.BATCH_PREVIEW_IDS)

    # ------------------------------------------------------------------
    # 8. Les boutons — ce que l'avis pose sur la ligne
    # ------------------------------------------------------------------
    def test_les_deux_gestes_refusent_la_ligne_d_autrui(self):
        """Publiques, donc appelables par RPC avec n'importe quel identifiant.

        Un membre de `group_email_admin` peut lire la boîte de tout le monde :
        sans ce refus, un identifiant deviné suffirait à archiver le courriel
        d'un collègue depuis une console.
        """
        rec = self._new_inbound(offset=830)
        rec.sudo().write({"user_id": self.stranger.id})
        # ⚠️ Le cache de l'ORM est par transaction : l'écriture en sudo vient
        # d'y poser la valeur, et sans cette invalidation le test passerait
        # sur un cache chaud plutôt que sur une vraie lecture par le rôle.
        self.env.invalidate_all()
        Email = self.env["bf.email"].with_user(self.owner)
        with self.assertRaises(AccessError):
            Email.popup_snooze(rec.id)
        with self.assertRaises(AccessError):
            Email.popup_mark_handled(rec.id)

    def test_reporter_sort_le_courriel_et_pose_l_echeance(self):
        rec = self._new_inbound(offset=800)
        result = self.env["bf.email"].with_user(self.owner).popup_snooze(
            rec.id, 30)
        rec.invalidate_recordset()
        self.assertTrue(rec.is_handled)
        self.assertTrue(rec.snoozed_until)
        minutes = (rec.snoozed_until - fields.Datetime.now()).total_seconds() / 60
        self.assertGreater(minutes, 28)
        self.assertLess(minutes, 31)
        self.assertEqual(result["minutes"], 30)

    def test_report_sans_argument_suit_le_reglage_du_compte(self):
        self.account.popup_snooze_minutes = 15
        rec = self._new_inbound(offset=840)
        result = self.env["bf.email"].with_user(self.owner).popup_snooze(rec.id)
        self.assertEqual(result["minutes"], 15)

    def test_report_borne_les_valeurs_absurdes(self):
        """Un clic ne doit pas pouvoir faire disparaître un courriel des années.

        Et zéro rendrait une échéance déjà passée, que `mobile_snooze` refuse
        d'un `UserError` — l'avis afficherait alors « le report a échoué » sur
        un réglage de compte laissé à zéro par distraction.
        """
        Email = self.env["bf.email"].with_user(self.owner)
        self.assertEqual(Email.popup_snooze(self._new_inbound(offset=850).id, 0)
                         ["minutes"], 1)
        self.assertEqual(
            Email.popup_snooze(self._new_inbound(offset=860).id, 10 ** 9)
            ["minutes"], 60 * 24 * 30)
        self.assertEqual(
            Email.popup_snooze(self._new_inbound(offset=870).id, "trente")
            ["minutes"], 60)

    def test_traite_passe_par_le_chemin_du_telephone(self):
        """Même geste que dans la boîte et dans l'app, pas un jumeau.

        La recopie IMAP vers `Archives/{AAAA}` et le retrait de la
        notification déjà posée sur le téléphone tiennent à ce que ce soit
        LA MÊME méthode ; deux chemins finiraient par ne plus archiver au même
        endroit.
        """
        rec = self._new_inbound(offset=810)
        # ⚠️ Pas `BASE` : `mobile_set_handled` est portée par la classe de
        # `bf_email_mobile.py`, pas par celle de `bf_email.py`. Les deux
        # construisent le même modèle, mais `patch` vise une classe Python.
        with patch(f"{MOBILE}.mobile_set_handled", autospec=True) as mocked:
            self.env["bf.email"].with_user(self.owner).popup_mark_handled(rec.id)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args[0][1], [rec.id])
        self.assertEqual(mocked.call_args[1], {"handled": True})

    def test_traite_marque_vraiment_la_ligne(self):
        self.account.writeback_archive = False
        rec = self._new_inbound(offset=820)
        self.env["bf.email"].with_user(self.owner).popup_mark_handled(rec.id)
        rec.invalidate_recordset()
        self.assertTrue(rec.is_handled)

    # ------------------------------------------------------------------
    # 9. Le report échu — l'avis repart avec le courriel
    # ------------------------------------------------------------------
    def test_report_echu_reveille_et_annonce_de_nouveau(self):
        """Sans ça, « Reporter » ferait disparaître au lieu de différer.

        Le réveil vit déjà dans `_cron_imap_mirror` ; ce qui est neuf, c'est
        que l'avis reparte avec, marqué `wake` pour que le toast dise « report
        échu » plutôt que d'annoncer une arrivée qui n'en est pas une.
        """
        rec = self._new_inbound(offset=880)
        rec.sudo().write({
            "is_handled": True,
            "snoozed_until": fields.Datetime.subtract(
                fields.Datetime.now(), minutes=1),
        })
        # L'IMAP n'a rien à faire ici : le réveil est une passe globale, faite
        # avant la boucle des comptes et sans connexion.
        self._run_mirror_without_imap()

        rec.invalidate_recordset()
        self.assertFalse(rec.is_handled, "le report échu doit revenir en boîte")
        payloads = [p for _c, p in self._popups()]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["email_id"], rec.id)
        self.assertTrue(payloads[0]["wake"])

    def test_report_echu_muet_si_l_instance_est_eteinte(self):
        """Le réveil doit continuer de fonctionner sans l'avis."""
        self.param.set_param("bf_email.popup_enabled", "0")
        rec = self._new_inbound(offset=890)
        rec.sudo().write({
            "is_handled": True,
            "snoozed_until": fields.Datetime.subtract(
                fields.Datetime.now(), minutes=1),
        })
        self._run_mirror_without_imap()
        rec.invalidate_recordset()
        self.assertFalse(rec.is_handled)
        self.assertFalse(self._popups())
