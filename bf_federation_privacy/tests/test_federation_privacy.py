"""L'avis de violation fédéré, éprouvé sur une base qui se fédère avec elle-même.

Même montage que le socle : B envoie (le mandataire), A reçoit (le responsable),
par la vraie porte HTTP signée.
"""
import base64
import hashlib

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation


@tagged("post_install", "-at_install", "federation", "federation_privacy")
class TestFederationPrivacy(TestFederation):

    def assertUserErrorNotAccess(self, fn, *args, **kwargs):
        """AccessError hérite de UserError : on exige une UserError qui n'est pas un refus de droits."""
        with self.assertRaises(UserError) as cm:
            fn(*args, **kwargs)
        self.assertNotIsInstance(cm.exception, AccessError)

    def setUp(self):
        super().setUp()
        base = self.env.ref("base.group_user")
        self.manager = self.env["res.users"].create({
            "name": "Gestionnaire VP", "login": "gvp-fed-essai", "email": "gvp@mandataire.example",
            "groups_id": [(6, 0, [base.id, self.env.ref("privacy_consent.group_privacy_manager").id])]})
        self.receveur.write({"groups_id": [(4, self.env.ref("privacy_consent.group_privacy_officer").id)]})
        self.receveur.partner_id.function = "Responsable de la protection des RP"
        client = self.peer_b.partner_id
        client.write({"is_company": True, "privacy_officer_email": "rprp@client.example"})
        self.client = client

    def _envoyer(self, **vals):
        data = {
            "responsible_id": self.client.id, "notice_type": "breach", "nature": "unauthorized_access",
            "circumstances": "Mot de passe d'application exposé dans un journal.",
            "discovered_at": "2026-10-07 14:00:00", "pi_description": "Coordonnées des employés.",
            "subject_count": 12, "subject_count_quebec": 11, "encryption": "no",
            "exfiltration": "none_found", "measures_taken": "Mot de passe changé.",
        }
        data.update(vals)
        notice = self.env["privacy.breach.notice"].with_user(self.manager).create(data)
        notice.action_send()
        self._flush()
        return notice

    def _miroir(self, notice):
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(notice.id)),
             ("res_model", "=", "privacy.breach.notice")], limit=1)
        self.assertTrue(link, "aucun miroir pour %s" % notice.display_name)
        return link._record()

    def test_p01_le_genre_est_annonce(self):
        self.assertTrue(self.peer_a.action_ping())
        self.peer_a.invalidate_recordset()
        self.assertIn("breach.share", self.peer_a.accepted_kinds.split(","))
        self.assertIn("breach.ack", self.peer_a.accepted_kinds.split(","))

    def test_p02_l_avis_part_par_courriel_et_par_la_federation(self):
        notice = self._envoyer()
        self.assertEqual(notice.federation_peer_id, self.peer_b)
        self.assertTrue(notice.sudo().mail_id, "le courriel part quand même")
        miroir = self._miroir(notice)
        self.assertEqual(miroir.state, "received")
        self.assertEqual((miroir.name, miroir.version), (notice.name, notice.version))
        self.assertEqual(miroir.responsible_id, self.env.company.partner_id)
        self.assertEqual(miroir.content_sha256, notice.content_sha256)
        self.assertEqual(miroir.sudo().pdf_attachment_id.raw, notice.sudo().pdf_attachment_id.raw)
        self.assertTrue(miroir._pdf_intact())
        self.assertEqual(miroir.subject_count_quebec, 11)
        self.assertIn("Mot de passe", miroir.circumstances)

    def test_p03_l_avis_devient_une_fiche_du_registre(self):
        notice = self._envoyer()
        miroir = self._miroir(notice)
        incident = miroir.incident_id
        self.assertTrue(incident)
        self.assertTrue(incident.declared_by_processor)
        self.assertEqual(incident.processor_partner_id, self.peer_a.partner_id)
        self.assertEqual(incident.processor_notice_ref, notice.name)
        self.assertEqual(incident.processor_notice_sha256, notice.content_sha256)
        self.assertEqual(incident.incident_type, "unauthorized_access")
        self.assertEqual(incident.serious_harm_risk, "undetermined")
        self.assertIn("Non chiffrés", incident.processor_facts)
        self.assertEqual(incident.awareness_date, miroir.received_at.date())

    def test_p04_la_mise_a_jour_suit_la_meme_fiche(self):
        v1 = self._envoyer()
        incident = self._miroir(v1).incident_id
        incident.sudo().write({"serious_harm_risk": "no", "circumstances": "Notre lecture."})
        v2 = self.env["privacy.breach.notice"].with_user(self.manager).browse(v1.action_new_version()["res_id"])
        v2.write({"stage": "final", "measures_taken": "Journal purgé, accès revus."})
        v2.action_send()
        self._flush()
        miroir2 = self._miroir(v2)
        self.assertEqual(miroir2.incident_id, incident, "une seule fiche pour l'avis et ses mises à jour")
        self.assertEqual(incident.processor_notice_version, 2)
        self.assertEqual(incident.processor_notice_sha256, v2.content_sha256)
        self.assertEqual(incident.serious_harm_risk, "no", "l'évaluation du client ne bouge pas")
        self.assertEqual(incident.circumstances, "Notre lecture.")

    def test_p05_l_accuse_revient_avec_l_empreinte_et_notre_heure(self):
        notice = self._envoyer()
        miroir = self._miroir(notice)
        miroir.with_user(self.receveur).action_acknowledge_federated()
        self.assertEqual(miroir.state, "acknowledged")
        self._flush()
        notice.invalidate_recordset()
        self.assertEqual(notice.state, "acknowledged")
        self.assertEqual(notice.ack_channel, "federation")
        self.assertEqual(notice.ack_name, "Personne du pair")
        self.assertEqual(notice.ack_title, "Responsable de la protection des RP")
        self.assertEqual(notice.ack_sha256, notice.content_sha256)
        self.assertGreaterEqual(notice.ack_at, miroir.ack_at, "l'heure est celle de la réception chez le mandataire")

    def test_p06_seul_le_rprp_accuse(self):
        notice = self._envoyer()
        miroir = self._miroir(notice)
        commis = self.env["res.users"].create({
            "name": "Commis", "login": "commis-fed-essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_manager").id])]})
        with self.assertRaises(AccessError):
            miroir.with_user(commis).action_acknowledge_federated()
        self.assertUserErrorNotAccess(notice.with_user(self.manager).action_acknowledge_federated)

    def test_p07_un_avis_recu_ne_se_met_pas_a_jour_ni_ne_change(self):
        miroir = self._miroir(self._envoyer()).sudo(False).with_user(self.receveur)
        self.assertUserErrorNotAccess(miroir.action_new_version)
        self.assertUserErrorNotAccess(setattr, miroir, "circumstances", "Réécrit chez le client")

    def test_p08_empreinte_annoncee_fausse_refusee(self):
        card = {"ref": "AV-2026-9999", "version": 1, "pdf": base64.b64encode(b"%PDF vrai").decode(),
                "sha256": hashlib.sha256(b"%PDF autre").hexdigest(), "circumstances": "x"}
        with self.assertRaisesRegex(UserError, "empreinte"):
            self.env["privacy.breach.notice"].sudo()._federation_receive(self.peer_a, card)

    def test_p09_le_meme_numero_chez_deux_mandataires_ne_se_heurte_pas(self):
        """Le client émet lui aussi des avis, et numérote comme son mandataire."""
        notice = self._envoyer()
        miroir = self._miroir(notice)
        self.assertEqual(miroir.name, notice.name, "même numéro, même version, même société : permis")
        self.assertNotEqual(miroir, notice)

    def test_p10_un_client_non_jumele_recoit_le_courriel_seul(self):
        autre = self.env["res.partner"].create({"name": "Client non jumelé", "is_company": True,
                                               "privacy_officer_email": "rprp@autre.example"})
        notice = self._envoyer(responsible_id=autre.id)
        self.assertFalse(notice.federation_peer_id)
        self.assertTrue(notice.sudo().mail_id)
        self.assertFalse(self.Outbox.search([("kind", "=", "breach.share"), ("payload", "ilike", notice.name)]))

    def test_p11_un_pair_qui_ne_sait_pas_recevoir_n_est_pas_vise(self):
        self.peer_b.sudo().write({"accepted_kinds": "task.share,ping"})
        notice = self._envoyer()
        self.assertFalse(notice.federation_peer_id)

    # --- Les gardes ----------------------------------------------------------------------
    def test_p12_un_avis_ne_se_detourne_pas_vers_un_autre_pair(self):
        """🔴 `received_at` + un pair, en une écriture, faisait partir l'avis chez un autre client."""
        notice = self._envoyer()
        Notice = self.env["privacy.breach.notice"].with_user(self.manager)
        for vals in ({"received_at": "2026-10-08 12:00:00"}, {"processor_name": "Faux"},
                     {"federation_peer_id": self.peer_a.id}):
            self.assertUserErrorNotAccess(Notice.browse(notice.id).write, vals)
        self.assertEqual(notice.federation_peer_id, self.peer_b)

    def test_p13_un_brouillon_ne_se_federe_jamais(self):
        Notice = self.env["privacy.breach.notice"].with_user(self.manager)
        self.assertUserErrorNotAccess(Notice.create, {"responsible_id": self.client.id, "circumstances": "Brouillon",
                                                      "federation_peer_id": self.peer_b.id})
        brouillon = Notice.create({"responsible_id": self.client.id, "circumstances": "Brouillon"})
        self.assertUserErrorNotAccess(brouillon.write, {"federation_peer_id": self.peer_b.id})
        self._flush()
        self.assertFalse(self.Outbox.search([("kind", "=", "breach.share")]))

    def test_p14_un_second_partage_ne_casse_rien_et_l_accuse_revient(self):
        notice = self._envoyer()
        miroir = self._miroir(notice)
        link = self.env["federation.link"]._receive_share(
            self.peer_a, "breach", str(notice.id), notice.sudo()._federation_card())
        self.assertTrue(link, "le second partage est accepté, sans 422")
        self.assertEqual(link._record(), miroir)
        miroir.with_user(self.receveur).action_acknowledge_federated()
        self._flush()
        notice.invalidate_recordset()
        self.assertEqual(notice.state, "acknowledged")

    def test_p15_sans_pdf_pas_d_accuse_federe(self):
        notice_cls = type(self.env["privacy.breach.notice"])
        self.patch(notice_cls, "_breach_card_carries_pdf", lambda self: False)
        notice = self._envoyer()
        miroir = self._miroir(notice)
        self.assertFalse(miroir.pdf_attachment_id, "le PDF n'a pas traversé")
        with self.assertRaisesRegex(UserError, "lien du courriel"):
            miroir.with_user(self.receveur).action_acknowledge_federated()
        notes = notice.message_ids.mapped("body")
        self.assertTrue(any("n'a pas traversé" in (b or "") for b in notes), "l'émetteur le sait")

    def test_p16_une_date_de_constat_ancienne_se_garde(self):
        notice = self._envoyer(discovered_at="2026-06-01 09:00:00")
        self.assertEqual(str(self._miroir(notice).discovered_at), "2026-06-01 09:00:00")

    def test_p17_une_filiale_jumelee_ne_recoit_pas_l_avis_de_sa_mere(self):
        mere = self.env["res.partner"].create({"name": "Regroupement d'essai", "is_company": True,
                                               "privacy_officer_email": "rprp@mere.example"})
        self.client.parent_id = mere
        notice = self._envoyer(responsible_id=mere.id)
        self.assertFalse(notice.federation_peer_id, "le pair de la filiale n'est pas celui de la mère")
        self.assertTrue(notice.sudo().mail_id, "la mère reçoit le courriel")

    def test_p18_une_carte_aux_types_hostiles(self):
        card = {"ref": "AV-2026-7777", "version": float("inf"), "circumstances": "x",
                "notice_type": ["breach"], "nature": {"a": 1}, "subject_count": 10 ** 12,
                "subject_count_quebec": -5, "subject_count_estimate": "false",
                "law_enforcement": "true", "report_lines": {"category": "dict"},
                "occurred_approximate": 1}
        miroir = self.env["privacy.breach.notice"].sudo()._federation_receive(self.peer_a, card)
        self.assertEqual(miroir.version, 1)
        self.assertEqual(miroir.notice_type, "breach", "valeur par défaut, pas la liste reçue")
        self.assertFalse(miroir.nature)
        self.assertEqual(miroir.subject_count, 2 ** 31 - 1)
        self.assertEqual(miroir.subject_count_quebec, 0)
        self.assertFalse(miroir.subject_count_estimate, "« false » n'est pas vrai")
        self.assertFalse(miroir.law_enforcement, "seul le vrai booléen JSON compte")
        self.assertFalse(miroir.report_line_ids)
        self.assertTrue(miroir.incident_id)

    def test_p19_le_rprp_est_prevenu_dans_odoo(self):
        miroir = self._miroir(self._envoyer())
        activites = miroir.incident_id.activity_ids
        self.assertEqual(activites.user_id, self.receveur)
        self.assertIn("évaluer le risque", activites.summary)

    def test_p20_une_version_arrivee_en_retard_ne_relance_pas_le_rprp(self):
        Notice = self.env["privacy.breach.notice"].sudo()
        base = {"ref": "AV-2026-8888", "circumstances": "Désordre", "pdf": "", "sha256": "a" * 64}
        v2 = Notice._federation_receive(self.peer_a, dict(base, version=2))
        incident = v2.incident_id
        self.assertEqual(len(incident.activity_ids), 1)
        v1 = Notice._federation_receive(self.peer_a, dict(base, version=1, sha256="b" * 64))
        self.assertEqual(v1.incident_id, incident)
        self.assertEqual(incident.processor_notice_version, 2, "la fiche reste sur la plus récente")
        self.assertEqual(len(incident.activity_ids), 1, "pas de seconde activité pour une version périmée")


    def test_p21_une_mise_a_jour_ne_part_pas_chez_un_autre_pair_par_defaut(self):
        """🔴 `default_received_at` + `default_federation_peer_id` sur la copie de mise à jour."""
        v1 = self._envoyer()
        v2 = self.env["privacy.breach.notice"].with_user(self.manager).browse(v1.with_user(self.manager).with_context(
            default_received_at="2026-10-08 12:00:00", default_federation_peer_id=self.peer_a.id,
            default_processor_name="Faux").action_new_version()["res_id"])
        self.assertFalse(v2.received_at or v2.federation_peer_id or v2.processor_name)
        avant = self.Outbox.search_count([("kind", "=", "breach.share")])
        self._flush()
        self.assertEqual(self.Outbox.search_count([("kind", "=", "breach.share")]), avant, "rien n'est parti")

    def test_p22_un_numero_recu_slash_ne_prend_pas_notre_sequence(self):
        miroir = self.env["privacy.breach.notice"].sudo()._federation_receive(
            self.peer_a, {"ref": "/", "version": 1, "circumstances": "x"})
        self.assertEqual(miroir.name, "(sans numéro)")


    def test_p23_un_client_devenu_contact_ne_federe_plus(self):
        b = self.env["res.partner"].create({"name": "Maison B d'essai", "is_company": True})
        self.peer_a.sudo().partner_id = b
        notice = self.env["privacy.breach.notice"].with_user(self.manager).create({
            "responsible_id": self.client.id, "circumstances": "x", "nature": "loss",
            "discovered_at": "2026-10-07 10:00:00", "pi_description": "y"})
        self.client.sudo().write({"is_company": False, "parent_id": b.id})
        self.assertFalse(notice._federation_allowed_peers())

    def test_p24_sans_pdf_le_mandataire_refuse_l_accuse_federe(self):
        notice_cls = type(self.env["privacy.breach.notice"])
        self.patch(notice_cls, "_breach_card_carries_pdf", lambda self: False)
        notice = self._envoyer()
        self.assertFalse(notice.federation_pdf_sent)
        link = notice._federation_link()
        notice.sudo()._federation_apply_ack(link, {"by": "Pair", "sha256": notice.content_sha256})
        self.assertEqual(notice.state, "sent", "l'empreinte figurait dans la carte : elle ne prouve rien")


    def test_p25_un_lien_d_avis_ne_se_pose_pas_a_la_main(self):
        """🔴 Le socle ouvre la création des liens au gestionnaire de projet."""
        notice = self._envoyer()
        gestionnaire_projet = self.receveur  # gestionnaire de projet dans le montage du socle
        with self.assertRaises(UserError):
            self.env["federation.link"].with_user(gestionnaire_projet).create({
                "peer_id": self.peer_a.id, "res_model": "privacy.breach.notice", "res_id": notice.id,
                "origin": "local"})
        link = notice._federation_link()
        with self.assertRaises(UserError):
            link.with_user(gestionnaire_projet).write({"peer_id": self.peer_a.id})

    def test_p26_l_accuse_d_un_autre_pair_n_est_pas_retenu(self):
        notice = self._envoyer()
        autre = self.env["federation.link"].sudo().create({
            "peer_id": self.peer_a.id, "res_model": "privacy.breach.notice", "res_id": notice.id,
            "origin": "local", "remote_ref": "999999"})
        notice.sudo()._federation_apply_ack(autre, {"by": "Intrus", "sha256": notice.content_sha256})
        self.assertEqual(notice.state, "sent")
        self.assertEqual(notice.federation_shared_peer_id, self.peer_b)

    def test_p27_la_fusion_ne_deplace_pas_un_pair(self):
        createur = self.env["res.users"].create({
            "name": "Création de contacts 3", "login": "cc3-essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])]})
        doublon = self.env["res.partner"].create({"name": "Doublon", "is_company": True,
                                                 "email": self.client.email or "c@client.example"})
        self.client.sudo().email = doublon.email
        with self.assertRaisesRegex(UserError, "pair de fédération"):
            self.env["base.partner.merge.automatic.wizard"].with_user(createur)._merge(
                [self.client.id, doublon.id], doublon)


    def test_p28_les_liens_d_avis_ne_se_lisent_pas_sans_le_role(self):
        self._envoyer()
        interne = self.env["res.users"].create({
            "name": "Interne", "login": "interne-essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        Link = self.env["federation.link"]
        self.assertFalse(Link.with_user(interne).search([("res_model", "=", "privacy.breach.notice")]))
        self.assertTrue(Link.with_user(self.manager).search([("res_model", "=", "privacy.breach.notice")]))


    def test_p29_les_messages_de_lien_d_avis_ne_se_lisent_pas_sans_le_role(self):
        notice = self._envoyer()
        self._miroir(notice).with_user(self.receveur).action_acknowledge_federated()
        self._flush()
        interne = self.env["res.users"].create({
            "name": "Interne 2", "login": "interne2-essai",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        Message = self.env["federation.link.message"]
        domaine = [("link_id.res_model", "=", "privacy.breach.notice")]
        self.assertTrue(Message.sudo().search(domaine), "il y a bien des messages de lien d'avis")
        self.assertFalse(Message.with_user(interne).search([]).filtered(
            lambda m: m.sudo().link_id.res_model == "privacy.breach.notice"))

    def test_p30_l_activite_va_a_un_responsable_vie_privee(self):
        """L'utilisateur du pair sans rôle Vie privée ne reçoit pas l'activité (il en lirait le résumé)."""
        for xmlid in ("privacy_consent.group_privacy_officer", "privacy_consent.group_privacy_manager",
                      "privacy_consent.group_privacy_user"):
            self.receveur.write({"groups_id": [(3, self.env.ref(xmlid).id)]})
        self.assertFalse(self.receveur.has_group("privacy_consent.group_privacy_user"))
        miroir = self._miroir(self._envoyer())
        user = miroir.incident_id.activity_ids.user_id
        self.assertTrue(user, "l'activité existe : un membre du groupe Responsable la reçoit")
        self.assertNotEqual(user, self.receveur)
        self.assertTrue(user.has_group("privacy_consent.group_privacy_user"))


    def test_p31_pas_de_courriel_d_assignation(self):
        """L'objet du courriel d'assignation laisserait un suivi lisible ailleurs."""
        miroir = self._miroir(self._envoyer())
        self.assertTrue(miroir.incident_id.activity_ids)
        self.assertFalse(self.env["mail.mail"].sudo().search(
            [("model", "=", "privacy.incident"), ("res_id", "=", miroir.incident_id.id)]))
        # mail_post_defer, quand il est installé, diffère l'envoi : on juge la notification elle-même,
        # que `action_notify` crée tout de suite.
        self.assertFalse(self.env["mail.message"].sudo().search(
            [("model", "=", "privacy.incident"), ("res_id", "=", miroir.incident_id.id),
             ("message_type", "=", "user_notification")]))

    def test_p32_les_messages_de_lien_restent_dans_leur_societe(self):
        notice = self._envoyer()
        self._miroir(notice).with_user(self.receveur).action_acknowledge_federated()
        self._flush()
        autre = self.env["res.company"].create({"name": "Autre société d'essai"})
        lecteur = self.env["res.users"].create({
            "name": "Lecteur autre société", "login": "lecteur-autre-essai",
            "company_id": autre.id, "company_ids": [(6, 0, autre.ids)],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_user").id])]})
        self.assertFalse(self.env["federation.link.message"].with_user(lecteur).search([]).filtered(
            lambda m: m.sudo().link_id.res_model == "privacy.breach.notice"))

    def test_p33_un_rprp_designe_sans_role_ne_recoit_pas_l_activite(self):
        sans_role = self.env["res.users"].create({
            "name": "RPRP sans rôle", "login": "rprp-sans-role-essai", "email": "sr@pair.example",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        self.env.company.partner_id.sudo().write({"privacy_officer_partner_id": sans_role.partner_id.id,
                                                  "privacy_officer_email": "sr@pair.example"})
        miroir = self._miroir(self._envoyer())
        self.assertNotEqual(miroir.incident_id.activity_ids.user_id, sans_role)


    def test_p34_aucun_message_ne_traverse_un_lien_d_avis(self):
        notice = self._envoyer()
        avant = self.Outbox.search_count([("kind", "=", "message.new")])
        notice.with_user(self.manager).message_post(body="<p>Commentaire</p>", message_type="comment",
                                                    subtype_xmlid="mail.mt_comment")
        self.assertEqual(self.Outbox.search_count([("kind", "=", "message.new")]), avant,
                         "le fil de l'avis ne part pas chez le client")
        link = self._miroir(notice)._federation_link()
        self.assertIsNone(link._apply_message({"body": "<p>du pair</p>", "sender_message_ref": "1"}),
                          "et rien n'en revient")


    def test_p35_un_rprp_designe_sans_ecriture_accuse(self):
        """Le système consigne l'accusé ; le responsable n'écrit pas lui-même au fil (registre)."""
        rprp = self.env["res.users"].create({
            "name": "RPRP désigné", "login": "rprp-designe-essai", "email": "rprp-d@pair.example",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_user").id])]})
        self.assertFalse(rprp.has_group("privacy_consent.group_privacy_officer"))
        self.env.company.partner_id.sudo().write({"privacy_officer_partner_id": rprp.partner_id.id,
                                                  "privacy_officer_email": "rprp-d@pair.example"})
        miroir = self._miroir(self._envoyer())
        miroir.with_user(rprp).action_acknowledge_federated()
        self.assertEqual(miroir.state, "acknowledged")
        note = miroir.message_ids.filtered(lambda m: "Accusé de réception envoyé" in (m.body or ""))
        self.assertEqual(note.author_id, rprp.partner_id)
        with self.assertRaises(AccessError):
            miroir.with_user(rprp).message_post(body="<p>De sa main</p>", message_type="comment")


    def test_p36_le_mandataire_est_le_pair_authentifie(self):
        """La carte déclare un nom ; le registre affiche le pair jumelé."""
        miroir = self._miroir(self._envoyer())
        self.assertEqual(miroir.processor_name, miroir._federation_link().peer_id.name)


    def test_p37_un_lien_d_avis_reste_dans_la_societe_de_l_avis(self):
        """Un pair sans société n'ouvre pas ses liens d'avis aux autres sociétés."""
        notice = self._envoyer()
        link = notice._federation_link()
        link.peer_id.sudo().company_id = False
        autre = self.env["res.company"].create({"name": "Autre société liens"})
        lecteur_b = self.env["res.users"].create({
            "name": "Lecteur liens B", "login": "lecteur-liens-b-essai", "company_id": autre.id,
            "company_ids": [(6, 0, autre.ids)],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_user").id])]})
        lecteur_a = self.env["res.users"].create({
            "name": "Lecteur liens A", "login": "lecteur-liens-a-essai",
            "company_id": notice.company_id.id, "company_ids": [(6, 0, notice.company_id.ids)],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_user").id])]})
        Link = self.env["federation.link"]
        self.assertFalse(Link.with_user(lecteur_b).search([("id", "=", link.id)]))
        self.assertTrue(Link.with_user(lecteur_a).search([("id", "=", link.id)]))
        with self.assertRaises(AccessError):
            link.with_user(lecteur_b).check_access("read")  # le contrôle fiche par fiche, pas la recherche
        link.with_user(lecteur_a).check_access("read")
        # Les messages du lien suivent la même borne.
        self._miroir(notice).with_user(self.receveur).action_acknowledge_federated()
        self._flush()
        messages = self.env["federation.link.message"].sudo().search([("link_id", "=", link.id)])
        self.assertTrue(messages, "le partage et l'accusé laissent des messages de lien")
        LinkMessage = self.env["federation.link.message"]
        self.assertFalse(LinkMessage.with_user(lecteur_b).search([("id", "in", messages.ids)]))
        self.assertTrue(LinkMessage.with_user(lecteur_a).search([("id", "in", messages.ids)]))
