"""Garde du vieux courriel et geste « Pourriel » (18.0.11.55.0).

Le rail des dossiers est du client seul : il s'éprouve au navigateur
(paquet d'actifs et balayage), pas ici.
"""
import json
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MobileApiCase
from .test_imap_folders_and_uid_guard import FakeImap

IMAP = "odoo.addons.bf_email_management.models.bf_email_imap"


def _brut(message_id, date, sujet="Vieux courriel", expediteur="vieux@ailleurs.test",
          entetes_en_plus=""):
    return (
        "Message-ID: %s\r\nFrom: %s\r\nTo: owner@test.invalid\r\nSubject: %s\r\n"
        "Date: %s\r\n%s\r\nCorps.\r\n" % (message_id, expediteur, sujet, date, entetes_en_plus)
    ).encode()


@tagged("post_install", "-at_install")
class TestGardeVieuxCourriel(MobileApiCase):
    """Un vieux courriel jamais vu n'entre pas seul."""

    VIEUX = "Mon, 06 Jan 2025 10:00:00 +0000"

    def _ingerer(self, raw, uid=7001, folder="Archives/2025", garde=True):
        env = self.env["bf.email"].with_user(self.owner)
        if garde:
            env = env.with_context(bf_email_ingest_guard=True)
        return env._ingest_rfc822(raw, uid, folder, self.account)

    def _ligne(self, mid):
        return self.env["bf.email"].with_context(active_test=False).search(
            [("message_id_header", "=", mid)])

    def test_vieux_retenu_sur_le_chemin_automatique(self):
        mid = "<vieux-1@test.invalid>"
        self.assertFalse(self._ingerer(_brut(mid, self.VIEUX)))
        self.assertFalse(self._ligne(mid), "rien ne doit entrer dans bf.email")
        retenu = self.env["bf.email.held"].search([("message_id", "=", mid)])
        self.assertEqual(retenu.state, "pending")
        self.assertEqual(retenu.folder, "Archives/2025")
        self.assertEqual(retenu.user_id, self.owner)
        # Une deuxième passe ne le recrée pas et ne l'ingère pas.
        self.assertFalse(self._ingerer(_brut(mid, self.VIEUX)))
        self.assertEqual(
            self.env["bf.email.held"].search_count([("message_id", "=", mid)]), 1)

    def test_rattrapage_voulu_passe(self):
        mid = "<vieux-2@test.invalid>"
        self.assertTrue(self._ingerer(_brut(mid, self.VIEUX), garde=False))
        self.assertTrue(self._ligne(mid))
        self.assertFalse(self.env["bf.email.held"].search([("message_id", "=", mid)]))

    def test_recent_passe(self):
        mid = "<recent-1@test.invalid>"
        self.assertTrue(self._ingerer(_brut(mid, "Tue, 06 Oct 2099 10:00:00 +0000")))
        self.assertTrue(self._ligne(mid))

    def test_sans_date_passe(self):
        raw = ("Message-ID: <sans-date@test.invalid>\r\nFrom: x@y.test\r\n"
               "Subject: s\r\n\r\nCorps.\r\n").encode()
        self.assertTrue(self._ingerer(raw))

    def test_message_id_connu_n_est_jamais_retenu(self):
        # La déduplication passe AVANT la garde : un courriel déjà en base qui
        # réapparaît dans un vieux dossier ne fait pas de lot.
        mid = self.inbound.message_id_header
        self.assertFalse(self._ingerer(_brut(mid, self.VIEUX)))
        self.assertFalse(self.env["bf.email.held"].search([("message_id", "=", mid)]))

    def test_seuil_zero_coupe_la_garde(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.ingest_max_age_days", "0")
        self.assertTrue(self._ingerer(_brut("<vieux-3@test.invalid>", self.VIEUX)))

    def test_ignorer_est_memorise(self):
        mid = "<vieux-4@test.invalid>"
        self._ingerer(_brut(mid, self.VIEUX))
        Held = self.env["bf.email.held"].with_user(self.owner)
        self.assertEqual(Held.held_count(), 1)
        self.assertEqual(Held.held_decide(self.account.id, "Archives/2025", "ignore"), 1)
        self.assertEqual(Held.held_count(), 0)
        self.assertFalse(self._ingerer(_brut(mid, self.VIEUX)))
        self.assertFalse(self._ligne(mid))

    def test_decider_le_lot_d_autrui_est_refuse(self):
        self._ingerer(_brut("<vieux-5@test.invalid>", self.VIEUX))
        with self.assertRaises(AccessError):
            self.env["bf.email.held"].with_user(self.stranger).held_decide(
                self.account.id, "Archives/2025", "ignore")
        # Et l'autre ne le voit pas.
        self.assertFalse(self.env["bf.email.held"].with_user(self.stranger).search([]))

    def test_ajouter_ingere_sans_renvoi_imap(self):
        mid = "<vieux-6@test.invalid>"
        self._ingerer(_brut(mid, self.VIEUX), uid=7006)
        self.env["bf.email.held"].with_user(self.owner).held_decide(
            self.account.id, "Archives/2025", "add")
        fake = FakeImap(mailboxes={"Archives/2025": {"7006": mid}})
        brut = _brut(mid, self.VIEUX)
        with patch(f"{IMAP}.open_connection", return_value=fake), \
                patch(f"{IMAP}.fetch_rfc822", return_value=brut), \
                patch(f"{IMAP}.uid_carries_message_id", return_value=True), \
                patch.object(type(self.env["bf.email"]), "_imap_writeback_move") as move:
            n = self.env["bf.email.held"]._cron_ingest_queued()
        self.assertEqual(n, 1)
        self.assertTrue(self._ligne(mid))
        self.assertEqual(
            self.env["bf.email.held"].search([("message_id", "=", mid)]).state, "added")
        move.assert_not_called()

    def test_un_lot_a_ajouter_reste_dehors_pour_la_reconciliation(self):
        # Relecture adverse B1 : « À ajouter » ne doit pas ouvrir la porte à la
        # réconciliation, qui ingérerait avec renvois et réponses d'absence.
        mid = "<vieux-8@test.invalid>"
        self._ingerer(_brut(mid, self.VIEUX))
        self.env["bf.email.held"].search([("message_id", "=", mid)]).state = "queued"
        self.assertFalse(self._ingerer(_brut(mid, self.VIEUX)))
        self.assertFalse(self._ligne(mid))

    def test_introuvable_revu_redevient_a_decider(self):
        mid = "<vieux-9@test.invalid>"
        self._ingerer(_brut(mid, self.VIEUX))
        retenu = self.env["bf.email.held"].search([("message_id", "=", mid)])
        retenu.write({"state": "gone", "announced": True})
        self._ingerer(_brut(mid, self.VIEUX), uid=7999, folder="Archive")
        self.assertEqual(retenu.state, "pending")
        self.assertEqual(retenu.folder, "Archive")
        self.assertFalse(retenu.announced)

    def test_compte_injoignable_rend_le_lot_sans_boucler(self):
        # Relecture adverse B3 : plus de relance sans pause.
        mid = "<vieux-10@test.invalid>"
        self._ingerer(_brut(mid, self.VIEUX))
        self.env["bf.email.held"].with_user(self.owner).held_decide(
            self.account.id, "Archives/2025", "add")
        from odoo.addons.bf_email_management.models import bf_email_imap
        cron = self.env.ref("bf_email_management.ir_cron_bf_email_held_ingest")
        with patch(f"{IMAP}.open_connection",
                   side_effect=bf_email_imap.ImapConnectionError("refus")), \
                patch.object(type(cron), "_trigger") as relance:
            self.assertEqual(self.env["bf.email.held"]._cron_ingest_queued(), 0)
        relance.assert_not_called()
        self.assertEqual(
            self.env["bf.email.held"].search([("message_id", "=", mid)]).state, "pending")

    def test_compte_desactive_ne_propose_rien(self):
        self._ingerer(_brut("<vieux-11@test.invalid>", self.VIEUX))
        self.account.active = False
        Held = self.env["bf.email.held"].with_user(self.owner)
        self.assertEqual(Held.held_count(), 0)
        with self.assertRaises(Exception):
            Held.held_decide(self.account.id, "Archives/2025", "add")

    def test_dossier_a_decider_dans_l_arbre(self):
        self._ingerer(_brut("<vieux-7@test.invalid>", self.VIEUX))
        dossiers = self.env["bf.email"].with_user(self.owner).inbox_get_folders()
        held = [f for f in dossiers if f["key"] == "held"]
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["count"], 1)
        # Et pas chez l'autre.
        autres = self.env["bf.email"].with_user(self.stranger).inbox_get_folders()
        self.assertFalse([f for f in autres if f["key"] == "held"])

    def test_avis_groupe_puis_silence(self):
        for i in range(3):
            self._ingerer(_brut("<lot-%s@test.invalid>" % i, self.VIEUX), uid=7100 + i)
        with patch.object(type(self.env["bus.bus"]), "_sendone") as envoi:
            self.env["bf.email.held"]._announce(self.account)
            self.env["bf.email.held"]._announce(self.account)
        self.assertEqual(envoi.call_count, 1, "un avis par lot, une seule fois")
        charge = envoi.call_args[0][2]
        self.assertEqual(charge["lots"][0]["count"], 3)
        self.assertNotIn("subject", json.dumps(charge))


@tagged("post_install", "-at_install")
class TestRegleLocale(MobileApiCase):
    """L'ajout d'un lot classe sans rien faire sortir ni bouger."""

    def test_regle_qui_range_ne_bouge_rien_en_local(self):
        self.account.writeback_archive = True
        self.env["bf.email.rule"].with_user(self.owner).create({
            "name": "Ranger Acme",
            "user_id": self.owner.id,
            "condition_ids": [(0, 0, {"kind": "condition", "field_name": "email_from",
                                      "operator": "contains", "value": "acme.test"})],
            "set_folder": "Clients/Acme",
            "set_handled": True,
        })
        Model = type(self.env["bf.email"])
        with patch.object(Model, "_imap_writeback_move") as move, \
                patch.object(Model, "_imap_writeback_archive") as archive:
            self.inbound.with_context(bf_email_rules_local_only=True)._apply_rules()
        move.assert_not_called()
        archive.assert_not_called()
        # Relecture adverse B4 : en local on classe sans traiter ; une ligne
        # traitée encore dans l'INBOX serait rangée dans l'heure par le balayage.
        self.assertFalse(self.inbound.is_handled)


@tagged("post_install", "-at_install")
class TestPourriel(MobileApiCase):
    """Le geste, l'annulation, la règle, la plainte."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account.writeback_archive = True
        cls.account.folder_cache = json.dumps([
            {"name": "INBOX", "special": []},
            {"name": "Junk", "special": ["\\Junk"]},
            {"name": "Trash", "special": ["\\Trash"]},
        ])
        cls.spam = cls.env["bf.email"].with_user(cls.owner).create(cls._vals(
            subject="Buy cheap domains now", sender="Vendeur <promo@spam.test>",
            direction="in", status="new", root=False,
            body="Achetez maintenant.", uid="201",
        ))
        cls.spam.raw_headers = (
            "Received-SPF: pass (mx: domain of spam.test designates 203.0.113.9 "
            "as permitted sender)\nAuthentication-Results: mx; dkim=pass "
            "header.d=spam.test; spf=pass; dmarc=pass\nX-Migadu-Country: US\n"
            "Delivered-To: owner@test.invalid\n")

    # -- dossier Indésirables --------------------------------------------
    def test_junk_par_attribut_puis_par_nom(self):
        self.assertEqual(self.account._junk_folder_name(), "Junk")
        self.account.folder_cache = json.dumps([
            {"name": "INBOX", "special": []}, {"name": "Courrier indésirable", "special": []}])
        self.assertEqual(self.account._junk_folder_name(), "Courrier indésirable")
        self.account.folder_cache = json.dumps([
            {"name": "[Gmail]/Spam", "special": ["\\Junk"]}, {"name": "Junk", "special": []}])
        self.assertEqual(self.account._junk_folder_name(), "[Gmail]/Spam",
                         "l'attribut passe avant le nom")
        self.account.folder_cache = json.dumps([{"name": "INBOX", "special": []}])
        self.assertFalse(self.account._junk_folder_name())

    # -- le geste et son annulation ---------------------------------------
    def test_signaler_range_dans_junk_sans_creer(self):
        fake = FakeImap(inbox={"201": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.spam.with_user(self.owner)._report_mark("spam")
        self.spam.invalidate_recordset()
        self.assertEqual(fake.copied, [("201", "Junk")])
        self.assertEqual(self.spam.imap_folder, "Junk")
        self.assertEqual(self.spam.report_kind, "spam")
        self.assertTrue(self.spam.is_handled)
        self.assertFalse(self.spam.active)

    def test_junk_absent_rien_n_est_cree(self):
        fake = FakeImap(inbox={"201": self.spam.message_id_header}, fail_copy=True)
        with patch(f"{IMAP}.open_connection", return_value=fake), \
                patch(f"{IMAP}.ensure_folder") as creer:
            self.spam.with_user(self.owner)._report_mark("spam")
        creer.assert_not_called()
        self.assertFalse(self.spam.active, "la ligne sort quand même de la boîte")

    def test_annuler_ramene_et_reactive(self):
        fake = FakeImap(inbox={"201": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.spam.with_user(self.owner)._report_mark("spam")
            self.env["bf.email"].with_user(self.owner).inbox_run_action(
                "unhandle", [self.spam.id])
        self.spam.invalidate_recordset()
        self.assertTrue(self.spam.active, "annuler doit rendre la ligne visible")
        self.assertFalse(self.spam.report_kind)
        self.assertFalse(self.spam.is_handled)
        self.assertEqual(self.spam.imap_folder, "INBOX")

    def test_annuler_sans_recopie_ramene_quand_meme(self):
        # Relecture adverse B2 : le geste déplace même sans « Réécriture des
        # archives » ; l'annuler doit ramener dans les mêmes conditions.
        self.account.writeback_archive = False
        fake = FakeImap(inbox={"201": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.spam.with_user(self.owner)._report_mark("spam")
            self.spam.with_user(self.owner).action_unhandle()
        self.spam.invalidate_recordset()
        self.assertEqual(self.spam.imap_folder, "INBOX")
        self.assertTrue(self.spam.imap_in_inbox)
        self.assertTrue(self.spam.active)

    def test_sauve_de_junk_au_webmail_revient(self):
        # Relecture adverse A3 : le miroir réactive la ligne.
        self.spam.write({"report_kind": "spam", "is_handled": True, "active": False,
                         "imap_folder": "Junk", "imap_uid": "999",
                         "imap_in_inbox": False})
        fake = FakeImap(inbox={"901": self.spam.message_id_header})
        self.env["bf.email"]._imap_mirror_adopt(fake, self.account, ["901"])
        self.spam.invalidate_recordset()
        self.assertTrue(self.spam.active)
        self.assertFalse(self.spam.report_kind)
        self.assertFalse(self.spam.is_handled)

    def test_annuler_une_corbeille_reactive_aussi(self):
        fake = FakeImap(inbox={"201": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.spam.with_user(self.owner).action_trash()
            self.spam.with_user(self.owner).action_unhandle()
        self.spam.invalidate_recordset()
        self.assertTrue(self.spam.active)

    def test_balayage_renvoie_le_signale_dans_junk(self):
        self.spam.write({"report_kind": "spam", "is_handled": True})
        fake = FakeImap(inbox={"201": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            self.spam._imap_writeback_where_rules_asked()
        self.assertEqual(fake.copied, [("201", "Junk")],
                         "pas vers les archives : vers Indésirables")

    # -- la règle ----------------------------------------------------------
    def test_regle_une_par_adresse(self):
        lot = self.spam | self.with_attachment
        self.assertEqual(lot.with_user(self.owner)._report_block_senders(), (2, 0))
        self.assertEqual(lot.with_user(self.owner)._report_block_senders(), (0, 0))
        regle = self.env["bf.email.rule"].search([("name", "ilike", "promo@spam.test")])
        self.assertEqual(regle.set_folder, "{JUNK}")
        self.assertEqual(regle._resolve_folder(self.spam), "Junk")
        self.assertTrue(regle.set_handled and regle.set_no_popup and regle.stop_processing)
        self.assertEqual(regle.user_id, self.owner)
        # L'adresse exacte, pas un morceau : jimbob@ n'est pas bob@.
        ctx = self.env["bf.email"]._rule_owner_context(self.owner)
        self.assertTrue(regle._match(self.spam, ctx))
        # Les copies passent par create() et donc par la règle : serveur de papier.
        with patch(f"{IMAP}.open_connection", return_value=FakeImap()):
            voisin = self.spam.copy({"email_from": "Autre <xpromo@spam.test>",
                                     "message_id_header": "<voisin@test.invalid>"})
            seul = self.spam.copy({"email_from": "promo@spam.test",
                                   "message_id_header": "<seul@test.invalid>"})
        self.assertFalse(regle._match(voisin, ctx))
        self.assertTrue(regle._match(seul, ctx))

    def test_regle_vise_l_adresse_reelle_pas_le_nom_affiche(self):
        self.spam.email_from = '"service@paypal.com" <x@phish.test>'
        self.spam.with_user(self.owner)._report_block_senders()
        self.assertTrue(self.env["bf.email.rule"].search([("name", "ilike", "x@phish.test")]))
        self.assertFalse(self.env["bf.email.rule"].search([("name", "ilike", "paypal")]))

    def test_regle_manuelle_jamais_reactivee(self):
        # Relecture adverse B5 : une vieille règle de renvoi désactivée.
        # Écrite par un administrateur : le renvoi externe lui est réservé.
        manuelle = self.env["bf.email.rule"].create({
            "name": "Comptable", "user_id": self.owner.id, "active": False,
            "condition_ids": [(0, 0, {"kind": "condition", "field_name": "email_from",
                                      "operator": "contains", "value": "<promo@spam.test>"})],
            "set_handled": True, "forward_to": "comptable@externe.test",
            "forward_allow_external": True,
        })
        self.assertEqual(self.spam.with_user(self.owner)._report_block_senders(), (1, 0))
        self.assertFalse(manuelle.active)

    def test_sans_junk_pas_de_regle(self):
        self.account.folder_cache = json.dumps([{"name": "INBOX", "special": []}])
        self.assertEqual(self.spam.with_user(self.owner)._report_block_senders(), (0, 1))

    def test_pourriel_sur_un_envoi_refuse(self):
        with self.assertRaises(Exception):
            self.outbound.with_user(self.owner).action_report_spam()

    # -- la plainte --------------------------------------------------------
    def _activer_plaintes(self):
        projet = self.env["project.project"].create({"name": "Plaintes (essai)"})
        self.owner.company_id.write({
            "bf_email_complaints_enabled": True,
            "bf_email_complaint_project_id": projet.id,
        })
        self.owner.groups_id = [(4, self.env.ref("project.group_project_user").id)]
        return projet

    def test_motif_desabonnement_etabli(self):
        crtc = self.env.ref("bf_email_management.report_authority_crtc")
        _objet, corps = self.spam._report_complaint(crtc)
        self.assertIn("6(2)(c)", corps, "aucun lien : le motif est retenu")
        self.assertIn("203.0.113.9", corps)
        self.assertIn("buy", corps.lower())
        # Un lien de désabonnement présent (champ calculé du module) : on le
        # simule au niveau de la preuve.
        with patch.object(type(self.spam), "_report_evidence",
                          lambda rec: dict(TestPourriel._preuve(rec), unsubscribe=True)):
            _objet, corps = self.spam._report_complaint(crtc)
        self.assertNotIn("6(2)(c)", corps, "un lien existe : le motif tombe")

    def test_motifs_etablis_par_le_corps(self):
        crtc = self.env.ref("bf_email_management.report_authority_crtc")
        self.spam.write({
            "subject": "Votre facture.pdf",
            "body_html": "<p>Appelez le 514 555-1234. Pour vous désabonner, cliquez.</p>",
            "raw_headers": "Authentication-Results: mx; spf=pass\n",
        })
        _objet, corps = self.spam._report_complaint(crtc)
        self.assertNotIn("6(2)(c)", corps, "le corps offre un désabonnement")
        self.assertNotIn("6(2)(a)", corps, "le corps porte un téléphone")
        self.assertNotIn("all passed", corps, "SPF seul n'est pas « tout passé »")
        self.assertNotIn("harvesting", corps, "facture.pdf n'est pas un domaine")

    @staticmethod
    def _preuve(rec):
        from odoo.addons.bf_email_management.models.bf_email_report import BfEmailReport
        return BfEmailReport._report_evidence(rec)

    def test_plainte_en_brouillon_sur_la_tache(self):
        projet = self._activer_plaintes()
        crtc = self.env.ref("bf_email_management.report_authority_crtc")
        brouillon = self.spam.with_user(self.owner)._report_stage_complaint(crtc)
        self.assertTrue(brouillon.bf_is_draft, "rien ne part sans « Envoyer maintenant »")
        self.assertEqual(brouillon.partner_ids.email, "spam@fightspam.gc.ca")
        tache = self.env["project.task"].browse(brouillon.res_id)
        self.assertEqual(tache.project_id, projet)
        self.assertEqual(tache.name, "Vendeur <promo@spam.test> (spam)")
        self.assertEqual(brouillon.attachment_ids.mimetype, "message/rfc822")
        # Une deuxième plainte pour le même expéditeur va sur la même tâche.
        autre = self.spam.with_user(self.owner)._report_stage_complaint(crtc)
        self.assertEqual(autre.res_id, tache.id)

    def test_fenetre_complete(self):
        self._activer_plaintes()
        crtc = self.env.ref("bf_email_management.report_authority_crtc")
        ftc = self.env.ref("bf_email_management.report_authority_ftc")
        wiz = self.env["bf.email.report.wizard"].with_user(self.owner).create({
            "email_ids": [(6, 0, self.spam.ids)],
            "block_sender": True,
        })
        self.assertTrue(wiz.complaints_enabled)
        self.assertIn(crtc, wiz.available_authority_ids)
        wiz.authority_ids = crtc | ftc
        self.assertIn("reportfraud.ftc.gov", wiz.form_help or "")
        fake = FakeImap(inbox={"201": self.spam.message_id_header})
        with patch(f"{IMAP}.open_connection", return_value=fake):
            res = wiz.action_confirm()
        self.assertEqual(res["infos"]["bf_email_reported"], self.spam.ids)
        brouillons = self.env["mail.scheduled.message"].search([
            ("partner_ids.email", "=", "spam@fightspam.gc.ca")])
        self.assertEqual(len(brouillons), 1, "le formulaire FTC ne prépare rien")

    def test_lot_sans_plainte(self):
        self._activer_plaintes()
        wiz = self.env["bf.email.report.wizard"].with_user(self.owner).create({
            "email_ids": [(6, 0, (self.spam | self.with_attachment).ids)],
        })
        self.assertFalse(wiz.complaints_enabled, "une plainte vise UN courriel")

    def test_classee_refusee(self):
        tache = self.env["project.task"].create({"name": "Dossier client"})
        self.spam.write({"res_model": "project.task", "res_id": tache.id})
        with self.assertRaises(Exception):
            self.env["bf.email"].with_user(self.owner).inbox_run_action(
                "spam", [self.spam.id])
