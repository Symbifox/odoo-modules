import hashlib

import psycopg2.errors
from markupsafe import Markup

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import BreachNoticeCase


@tagged("post_install", "-at_install")
class TestBreachNotice(BreachNoticeCase):

    def test_le_responsable_vient_de_la_designation(self):
        notice = self._breach()
        self.assertEqual(notice.officer_email, "rprp@client-essai.invalid")
        self.assertEqual(notice.officer_partner_id, self.rprp)
        self.assertTrue(notice.name.startswith("AV-"))
        self.assertEqual(notice.version, 1)

    def test_sans_designation_aucune_adresse_devinee(self):
        org = self.env["res.partner"].create({"name": "Sans RPRP", "is_company": True,
                                              "email": "info@sans.invalid"})
        notice = self._breach(responsible_id=org.id)
        self.assertFalse(notice.officer_email)
        with self.assertRaisesRegex(UserError, "adresse de réception"):
            notice.action_send()

    def test_l_envoi_exige_les_faits(self):
        notice = self._breach(nature=False, circumstances=False, pi_description=False)
        with self.assertRaises(UserError) as err:
            notice.action_send()
        for mot in ("circonstances", "nature", "renseignements visés"):
            self.assertIn(mot, str(err.exception))

    def test_l_envoi_fige_signe_et_empreinte(self):
        notice = self._breach()
        notice.action_send()
        self.assertEqual(notice.state, "sent")
        self.assertEqual(notice.signer_id, self.manager)
        self.assertEqual(notice.signer_title, "Responsable de la sécurité")
        self.assertEqual(notice.sent_to, "rprp@client-essai.invalid")
        raw = notice.sudo().pdf_attachment_id.raw
        self.assertEqual(notice.content_sha256, hashlib.sha256(raw).hexdigest())
        self.assertTrue(notice._pdf_intact())
        mail = notice.sudo().mail_id
        self.assertEqual(mail.email_to, "rprp@client-essai.invalid")
        self.assertFalse(mail.recipient_ids, "jamais au courriel général de l'organisation")
        self.assertIn(notice.sudo().pdf_attachment_id, mail.attachment_ids)
        self.assertFalse(mail.auto_delete, "le courriel se garde : c'est la trace de l'envoi")
        token = notice.sudo().ack_token
        self.assertIn(f"/privacy/breach/{notice.id}/{token}", mail.body_html, "le courriel envoyé porte le vrai lien")
        self.assertNotIn("__lien-d-accuse__", mail.body_html)
        self.assertIn(notice.content_sha256, mail.body_html)

    def test_un_avis_envoye_ne_change_plus(self):
        notice = self._breach()
        notice.action_send()
        self.assertIn("mise à jour", str(self.assertUserErrorNotAccess(setattr, notice, "circumstances", "Autre")))
        self.assertIn("envoyé", str(self.assertUserErrorNotAccess(notice.unlink)))

    def test_les_champs_de_preuve_ne_s_ecrivent_pas_a_la_main(self):
        notice = self._breach()
        self.assertUserErrorNotAccess(notice.write, {"state": "acknowledged"})
        self.assertUserErrorNotAccess(notice.with_context(privacy_breach_sending=True).write, {"ack_name": "Faux"})
        self.assertUserErrorNotAccess(self._breach, ack_at="2026-10-07 10:00:00")
        self.assertUserErrorNotAccess(self._breach, content_sha256="0" * 64)

    def test_mise_a_jour_chainee(self):
        v1 = self._breach()
        v1.action_send()
        action = v1.action_new_version()
        v2 = self.Notice.browse(action["res_id"])
        self.assertEqual((v2.name, v2.version, v2.stage), (v1.name, 2, "update"))
        self.assertEqual(v2.previous_id, v1)
        self.assertEqual(v2.root_id, v1)
        self.assertEqual(v2.circumstances, v1.circumstances)
        self.assertEqual(v2.state, "draft")
        self.assertFalse(v2.content_sha256)
        self.assertEqual(v1.action_new_version()["res_id"], v2.id, "un seul brouillon de mise à jour")
        v2.write({"stage": "final", "measures_taken": "Tout est corrigé."})
        v2.action_send()
        self.assertFalse(v1.is_latest)
        self.assertTrue(v2.is_latest)
        self.assertNotEqual(v1.content_sha256, v2.content_sha256)
        v3 = self.Notice.browse(v2.action_new_version()["res_id"])
        self.assertEqual((v3.version, v3.previous_id, v3.root_id), (3, v2, v1))

    def test_accuse(self):
        notice = self._breach()
        notice.action_send()
        with self.assertRaisesRegex(UserError, "empreinte"):
            notice._record_ack("Directrice", "DG", "0" * 64, "link")
        self.assertEqual(notice.state, "sent")
        with self.assertRaisesRegex(UserError, "nom"):
            notice._record_ack("  ", "DG", notice.content_sha256, "link")
        self.assertTrue(notice._record_ack("Directrice Essai", "DG", notice.content_sha256, "link", ip="203.0.113.5"))
        self.assertEqual(notice.state, "acknowledged")
        self.assertEqual((notice.ack_name, notice.ack_title, notice.ack_ip), ("Directrice Essai", "DG", "203.0.113.5"))
        self.assertEqual(notice.ack_sha256, notice.content_sha256)
        self.assertFalse(notice._record_ack("Autre", "", notice.content_sha256, "link"), "un seul accusé")
        self.assertEqual(notice.ack_name, "Directrice Essai")

    def test_le_pdf_envoye_est_protege(self):
        notice = self._breach()
        notice.action_send()
        pdf = notice.sudo().pdf_attachment_id.with_user(self.manager)
        with self.assertRaisesRegex(UserError, "ne se modifie pas"):
            pdf.write({"raw": b"autre texte"})
        with self.assertRaisesRegex(UserError, "ne se modifie pas"):
            pdf.write({"res_id": notice.id + 1000})
        with self.assertRaisesRegex(UserError, "ne se supprime pas"):
            pdf.unlink()
        # Si la pièce change quand même (administrateur), l'empreinte le dit.
        notice.sudo().pdf_attachment_id.with_user(self.env.ref("base.user_admin")).write({"raw": b"x"})
        self.assertFalse(notice._pdf_intact())

    def test_le_lecteur_ne_cree_rien(self):
        with self.assertRaises(AccessError):
            self.env["privacy.breach.notice"].with_user(self.reader).create({"responsible_id": self.org.id})
        notice = self._breach()
        self.assertEqual(notice.with_user(self.reader).name, notice.name)

    def test_releve_periodique(self):
        notice = self.Notice.create({
            "responsible_id": self.org.id, "notice_type": "report",
            "measures_taken": "Blocage automatique au pare-feu."})
        with self.assertRaisesRegex(UserError, "période"):
            notice.action_send()
        notice.write({"report_period_from": "2026-07-01", "report_period_to": "2026-09-30",
                      "report_line_ids": [(0, 0, {"category": "Connexions refusées", "count": 42})]})
        notice.action_send()
        self.assertEqual(notice.state, "sent")
        with self.assertRaises(UserError):
            notice.report_line_ids.count = 1
        with self.assertRaises(UserError):
            self.env["privacy.breach.notice.report.line"].with_user(self.manager).create(
                {"notice_id": notice.id, "category": "Ajout", "count": 1})

    def test_un_vrai_pdf(self):
        """Hors mode essai, le rapport rend un PDF, et c'est lui qu'on empreinte."""
        notice = self._breach()
        notice.with_context(force_report_rendering=True).action_send()
        self.assertTrue(notice.sudo().pdf_attachment_id.raw.startswith(b"%PDF"))


@tagged("post_install", "-at_install")
class TestBreachNoticeGardes(BreachNoticeCase):
    """Les gardes de l'avis, un essai chacune."""

    def _pas_une_erreur_de_droits(self, cm):
        # 🔴 AccessError hérite de UserError : un refus de droits rendrait l'essai vert pour
        # la mauvaise raison.
        self.assertNotIsInstance(cm.exception, AccessError)

    def test_numero_version_et_chaine_figes(self):
        v1 = self._breach()
        v1.action_send()
        autre = self._breach()
        for vals in ({"name": "AV-2026-9999"}, {"version": 7}, {"root_id": autre.id},
                     {"previous_id": autre.id}):
            with self.assertRaises(UserError) as cm:
                autre.write(vals)
            self._pas_une_erreur_de_droits(cm)
        with self.assertRaises(UserError) as cm:
            self._breach(previous_id=v1.id)
        self._pas_une_erreur_de_droits(cm)
        self.assertTrue(v1.is_latest)
        with self.assertRaisesRegex(UserError, "mise à jour"):
            v1.company_id = self.env["res.company"].create({"name": "Autre société d'essai"})

    def test_une_ligne_ne_se_deplace_pas_vers_un_avis_envoye(self):
        envoye = self.Notice.create({
            "responsible_id": self.org.id, "notice_type": "report", "measures_taken": "Blocage.",
            "report_period_from": "2026-07-01", "report_period_to": "2026-09-30",
            "report_line_ids": [(0, 0, {"category": "Connexions refusées", "count": 42})]})
        envoye.action_send()
        brouillon = self.Notice.create({
            "responsible_id": self.org.id, "notice_type": "report",
            "report_line_ids": [(0, 0, {"category": "Ajout clandestin", "count": 1})]})
        with self.assertRaisesRegex(UserError, "relevé"):
            brouillon.report_line_ids.write({"notice_id": envoye.id})
        self.assertEqual(len(envoye.report_line_ids), 1)

    def test_mise_a_jour_depuis_une_ancienne_version(self):
        v1 = self._breach()
        v1.action_send()
        v2 = self.Notice.browse(v1.action_new_version()["res_id"])
        v2.measures_taken = "Mesures de la v2."
        v2.action_send()
        self.org.privacy_officer_email = "nouvelle-rprp@client-essai.invalid"
        v3 = self.Notice.browse(v1.action_new_version()["res_id"])
        self.assertEqual((v3.version, v3.previous_id), (3, v2))
        self.assertEqual(v3.measures_taken, "Mesures de la v2.", "la suite part de la dernière version")
        self.assertEqual(v3.officer_email, "nouvelle-rprp@client-essai.invalid",
                         "la désignation du responsable est relue")

    def test_le_lecteur_ne_lance_pas_de_mise_a_jour(self):
        v1 = self._breach()
        v1.action_send()
        with self.assertRaises(AccessError):
            v1.with_user(self.reader).action_new_version()

    def test_jeton_et_empreinte_non_ascii(self):
        notice = self._breach()
        notice.action_send()
        self.assertFalse(notice._check_ack_token("é" * 43))
        with self.assertRaises(UserError) as cm:
            notice._record_ack("Directrice", "DG", "é" * 64, "link")
        self.assertIn("empreinte", str(cm.exception))

    def test_pas_d_accuse_sur_un_pdf_altere(self):
        notice = self._breach()
        notice.action_send()
        notice.sudo().pdf_attachment_id.with_user(self.env.ref("base.user_admin")).write({"raw": b"x"})
        with self.assertRaisesRegex(UserError, "PDF conservé"):
            notice._record_ack("Directrice", "DG", notice.content_sha256, "link")
        self.assertEqual(notice.state, "sent")

    def test_le_lien_d_accuse_n_est_pas_lisible_par_les_lecteurs(self):
        notice = self._breach()
        notice.action_send()
        token = notice.sudo().ack_token
        mail = notice.sudo().mail_id
        self.assertIn(token, mail.body_html, "le courriel envoyé porte bien le lien")
        self.assertNotIn(token, str(mail.body or ""), "le message lisible, lui, est caviardé")
        self.assertEqual((mail.model, mail.res_id), (notice._name, notice.id),
                         "le courriel reste rattaché : rebonds et réponses trouvent la fiche")
        Message = self.env["mail.message"].with_user(self.reader)
        self.assertFalse(Message.search([("body", "ilike", token)]))
        notice_messages = Message.search([("model", "=", notice._name), ("res_id", "=", notice.id)])
        self.assertFalse(any(token in (m.body or "") for m in notice_messages))

    def test_le_pdf_ne_se_publie_pas(self):
        notice = self._breach()
        notice.action_send()
        pdf = notice.sudo().pdf_attachment_id.with_user(self.manager)
        for vals in ({"public": True}, {"type": "url", "url": "https://ailleurs.invalid"},
                     {"access_token": "devine"}, {"name": "autre.pdf"}):
            with self.assertRaisesRegex(UserError, "ne se modifie pas"):
                pdf.write(vals)


@tagged("post_install", "-at_install")
class TestBreachNoticeDefautsDuContexte(BreachNoticeCase):
    """Les défauts du contexte et les défauts personnels ne forgent aucun champ réservé."""

    def test_les_defauts_du_contexte_ne_forgent_rien(self):
        """🔴 La garde ne voyait que `vals` ; `default_state` et compagnie passaient par-dessous."""
        forge = self.Notice.with_context(
            default_state="acknowledged", default_ack_name="Faux", default_ack_at="2026-10-08 10:00:00",
            default_content_sha256="0" * 64, default_version=9, default_name="AV-2026-9999",
            default_signer_id=self.manager.id).create({"responsible_id": self.org.id})
        self.assertEqual((forge.state, forge.version), ("draft", 1))
        self.assertFalse(forge.ack_name or forge.ack_at or forge.content_sha256 or forge.signer_id)
        self.assertNotEqual(forge.name, "AV-2026-9999")

    def test_les_defauts_personnels_ne_forgent_rien(self):
        self.env["ir.default"].with_user(self.manager).set(
            "privacy.breach.notice", "state", "acknowledged", user_id=self.manager.id)
        notice = self.Notice.create({"responsible_id": self.org.id})
        self.assertEqual(notice.state, "draft")

    def test_une_ligne_ne_s_ajoute_pas_par_defaut_a_un_avis_envoye(self):
        envoye = self.Notice.create({
            "responsible_id": self.org.id, "notice_type": "report", "measures_taken": "Blocage.",
            "report_period_from": "2026-07-01", "report_period_to": "2026-09-30",
            "report_line_ids": [(0, 0, {"category": "Connexions refusées", "count": 42})]})
        envoye.action_send()
        with self.assertRaisesRegex(UserError, "relevé"):
            self.env["privacy.breach.notice.report.line"].with_user(self.manager).with_context(
                default_notice_id=envoye.id).create({"category": "Ajout", "count": 1})

    def test_changer_de_responsable_relit_sa_designation(self):
        notice = self._breach()
        autre = self.env["res.partner"].create({"name": "Autre client d'essai", "is_company": True})
        autre.privacy_officer_email = "rprp@autre-essai.invalid"
        notice.responsible_id = autre
        self.assertEqual(notice.officer_email, "rprp@autre-essai.invalid",
                         "le courriel ne part pas chez l'ancien responsable")


@tagged("post_install", "-at_install")
class TestBreachNoticeVersions(BreachNoticeCase):
    """Les mises à jour d'un avis ne forgent rien et suivent la désignation."""

    def test_la_mise_a_jour_ne_forge_rien_par_defaut(self):
        """🔴 La copie de mise à jour tourne en sudo et héritait du contexte de l'appelant."""
        v1 = self._breach()
        v1.action_send()
        action = v1.with_context(
            default_state="acknowledged", default_ack_name="Faux", default_ack_at="2026-10-08 10:00:00",
            default_content_sha256=v1.content_sha256, default_pdf_attachment_id=v1.sudo().pdf_attachment_id.id,
            default_sent_at="2026-10-08 09:00:00", default_signer_id=self.manager.id).action_new_version()
        v2 = self.Notice.browse(action["res_id"])
        self.assertEqual(v2.state, "draft")
        self.assertFalse(v2.ack_name or v2.ack_at or v2.content_sha256 or v2.sudo().pdf_attachment_id
                         or v2.sent_at or v2.signer_id)

    def test_la_mise_a_jour_ignore_les_defauts_personnels(self):
        v1 = self._breach()
        v1.action_send()
        self.env["ir.default"].with_user(self.manager).set(
            "privacy.breach.notice", "state", "sent", user_id=self.manager.id)
        v2 = self.Notice.browse(v1.action_new_version()["res_id"])
        self.assertEqual(v2.state, "draft")

    def test_le_gabarit_ne_rend_jamais_le_jeton(self):
        """🔴 Un lecteur rendait le gabarit (aperçu, send_mail vers lui-même) et lisait le lien."""
        notice = self._breach()
        notice.action_send()
        token = notice.sudo().ack_token
        template = self.env.ref("privacy_breach_notice.mail_template_breach_notice")
        apercu = self.env["mail.template.preview"].with_user(self.reader).create({
            "mail_template_id": template.id, "resource_ref": f"{notice._name},{notice.id}"})
        self.assertNotIn(token, apercu.body_html or "")
        self.assertIn("__lien-d-accuse__", apercu.body_html or "")
        with self.assertRaises(AccessError):
            template.with_user(self.reader).send_mail(notice.id, email_values={"email_to": "moi@essai.invalid"})
        mail_id = template.with_user(self.manager).send_mail(notice.id, email_values={"email_to": "moi@essai.invalid"})
        self.assertNotIn(token, self.env["mail.mail"].sudo().browse(mail_id).body_html or "",
                         "même un gestionnaire n'obtient pas le jeton en rendant le gabarit")

    def test_l_envoi_ignore_les_defauts_de_l_appelant(self):
        """🔴 Copie cachée, faux « envoyé », PDF public : tout passait par le contexte."""
        notice = self._breach()
        notice.with_context(default_headers="{'Bcc': 'moi@essai.invalid'}", default_state="sent",
                            default_email_cc="moi@essai.invalid", default_public=True,
                            default_access_token="devine").action_send()
        mail = notice.sudo().mail_id
        self.assertEqual(mail.state, "outgoing")
        self.assertFalse(mail.headers)
        self.assertFalse(mail.email_cc)
        pdf = notice.sudo().pdf_attachment_id
        self.assertFalse(pdf.public)
        self.assertFalse(pdf.access_token)

    def test_une_mise_a_jour_garde_son_responsable(self):
        v1 = self._breach()
        v1.action_send()
        v2 = self.Notice.browse(v1.action_new_version()["res_id"])
        autre = self.env["res.partner"].create({"name": "Autre client", "is_company": True})
        with self.assertRaisesRegex(ValidationError, "responsable"):
            v2.responsible_id = autre

    def test_l_envoi_ignore_les_defauts_personnels_de_l_appelant(self):
        """Un `ir.default` personnel survit au nettoyage du contexte : seules les valeurs explicites
        de l'envoi protègent alors le courriel et le PDF."""
        Default = self.env["ir.default"].with_user(self.manager)
        Default.set("mail.mail", "state", "sent", user_id=self.manager.id)
        Default.set("mail.mail", "headers", "{'Bcc': 'moi@essai.invalid'}", user_id=self.manager.id)
        Default.set("ir.attachment", "public", True, user_id=self.manager.id)
        notice = self._breach()
        notice.action_send()
        mail = notice.sudo().mail_id
        # (L'état, lui, est imposé par mail.mail.default_get ; ce sont l'en-tête et le PDF qui
        # ne tiennent qu'aux valeurs explicites de l'envoi.)
        self.assertFalse(mail.headers)
        self.assertFalse(notice.sudo().pdf_attachment_id.public)


@tagged("post_install", "-at_install")
class TestBreachNoticeDestinataire(BreachNoticeCase):
    """L'avis ne part qu'à l'adresse que l'organisation responsable a désignée."""

    def test_un_client_rattache_sous_un_autre_ne_recoit_rien(self):
        """🔴 Un gestionnaire de contacts rattachait le client A sous B : les avis de A partaient chez B."""
        b = self.env["res.partner"].create({"name": "Client B d'essai", "is_company": True,
                                            "privacy_officer_email": "rprp@b.invalid"})
        notice = self._breach()
        self.org.sudo().write({"is_company": False, "parent_id": b.id})
        self.assertIn("organisation", str(self.assertUserErrorNotAccess(notice.action_send)))
        self.assertFalse(notice.sudo().mail_id)

    def test_l_adresse_est_relue_sur_la_fiche_a_l_envoi(self):
        notice = self._breach()
        self.org.privacy_officer_email = "nouvelle@client-essai.invalid"
        notice.action_send()
        self.assertEqual(notice.sent_to, "nouvelle@client-essai.invalid")

    def test_un_nom_qui_porte_le_marqueur_ne_recoit_pas_le_jeton(self):
        """🔴 Le marqueur fixe, glissé dans le nom du RPRP, recevait le vrai jeton."""
        self.rprp.name = "Directrice https://ailleurs.invalid/__lien-d-accuse__"
        notice = self._breach()
        notice.action_send()
        body = notice.sudo().mail_id.body_html
        token = notice.sudo().ack_token
        self.assertEqual(body.count(token), 1, "le jeton n'entre qu'une fois, dans le lien d'accusé")
        self.assertIn(f"/privacy/breach/{notice.id}/{token}", body)
        self.assertNotIn(f"ailleurs.invalid/{token}", body)

    def test_un_gabarit_sans_lien_n_envoie_pas(self):
        template = self.env.ref("privacy_breach_notice.mail_template_breach_notice")
        template.body_html = "<p>Avis sans lien.</p>"
        notice = self._breach()
        self.assertIn("lien d'accusé", str(self.assertUserErrorNotAccess(notice.action_send)))
        self.assertEqual(notice.state, "draft")


@tagged("post_install", "-at_install")
class TestBreachNoticeCleEtrangeres(BreachNoticeCase):
    """Ni la fusion ni la suppression d'un contact ne déplacent un avis envoyé."""

    def _createur_de_contacts(self):
        return self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Création de contacts", "login": "cc-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])]})

    def test_la_fusion_ne_redirige_pas_un_avis(self):
        """🔴 La fusion réécrit les clés étrangères en SQL, gel et gardes compris."""
        notice = self._breach()
        notice.action_send()
        autre = self.env["res.partner"].create({"name": "Client Z", "is_company": True,
                                                "email": self.org.email})
        wizard = self.env["base.partner.merge.automatic.wizard"].with_user(self._createur_de_contacts())
        with self.assertRaisesRegex(UserError, "avis de violation"):
            wizard._merge([self.org.id, autre.id], autre)
        self.assertEqual(notice.responsible_id, self.org)

    def test_le_jeton_ne_rentre_pas_par_une_reponse_citee(self):
        notice = self._breach()
        notice.action_send()
        token = notice.sudo().ack_token
        notice.sudo().message_post(body=f"<p>Merci.</p><blockquote>Lire : /privacy/breach/{notice.id}/{token}</blockquote>",
                                   message_type="email")
        self.assertFalse(self.env["mail.message"].sudo().search([("body", "ilike", token)]))

    def test_un_lien_rendu_deux_fois_n_envoie_pas(self):
        template = self.env.ref("privacy_breach_notice.mail_template_breach_notice")
        template.body_html = ('<p><a t-att-href="object._ack_url()">un</a> '
                              '<img t-att-src="object._ack_url()"/></p>')
        notice = self._breach()
        self.assertIn("une seule", str(self.assertUserErrorNotAccess(notice.action_send)))

    def test_un_lecteur_n_envoie_pas(self):
        notice = self._breach()
        with self.assertRaises(AccessError):
            notice.with_user(self.reader).action_send()


@tagged("post_install", "-at_install")
class TestBreachNoticeCodeAUsageUnique(BreachNoticeCase):

    def test_un_contact_vise_ne_s_efface_pas(self):
        """`ON DELETE SET NULL` effaçait la désignation ou le responsable d'un avis envoyé."""
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        notice = self._breach()
        notice.action_send()
        refuse = False
        try:
            with mute_logger("odoo.sql_db"), self.cr.savepoint():
                self.rprp.sudo().unlink()
        except (UserError, IntegrityError):
            refuse = True
        self.assertTrue(refuse, "un contact visé par un avis ne se supprime pas")
        self.assertEqual(notice.officer_partner_id, self.rprp)


@tagged("post_install", "-at_install")
class TestBreachNoticeEnvoi(BreachNoticeCase):

    def test_les_compteurs_du_code_sont_illisibles_et_remis_a_zero(self):
        notice = self.Notice.with_context(default_ack_otp_count=-10 ** 6,
                                          default_ack_otp_last_sent="2999-01-01 00:00:00").create({
            "responsible_id": self.org.id, "nature": "loss", "circumstances": "x",
            "discovered_at": "2026-10-07 10:00:00", "pi_description": "y"})
        notice.action_send()
        self.assertEqual(notice.sudo().ack_otp_count, 0, "l'envoi remet les compteurs à zéro")
        self.assertFalse(notice.sudo().ack_otp_last_sent)
        with self.assertRaises(AccessError):
            notice.with_user(self.reader).read(["ack_otp_hash"])

    def test_un_responsable_vise_ne_s_efface_pas(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        autre = self.env["res.partner"].create({"name": "Responsable seul", "is_company": True,
                                                "privacy_officer_email": "rprp@seul.invalid"})
        self._breach(responsible_id=autre.id).action_send()
        refuse = False
        try:
            with mute_logger("odoo.sql_db"), self.cr.savepoint():
                autre.sudo().unlink()
        except (UserError, IntegrityError):
            refuse = True
        self.assertTrue(refuse)


@tagged("post_install", "-at_install")
class TestBreachNoticeRegistre(BreachNoticeCase):
    """Le fil d'un avis est un registre : on n'y écrit qu'avec le droit d'écrire sur l'avis."""

    def test_un_lecteur_abonne_ne_poste_rien_au_fil(self):
        notice = self._breach()
        notice.action_send()
        lu = notice.with_user(self.reader)
        lu.message_subscribe(partner_ids=self.reader.partner_id.ids)  # permis : la lecture suffit
        with self.assertRaises(AccessError):
            lu.message_post(body="<p>Accusé réception (faux)</p>", message_type="comment",
                            subtype_xmlid="mail.mt_comment", author_id=self.env.ref("base.partner_root").id)
        with self.assertRaises(AccessError):
            self.env["mail.message"].with_user(self.reader).create({
                "model": notice._name, "res_id": notice.id, "body": "<p>Faux</p>", "message_type": "comment"})
        with self.assertRaises(AccessError):
            self.env["mail.message"].with_user(self.reader).with_context(
                default_model=notice._name, default_res_id=notice.id).create({"body": "<p>Faux</p>"})
        notice.message_post(body="<p>Note du gestionnaire</p>", message_type="comment",
                            subtype_xmlid="mail.mt_note")  # le gestionnaire, lui, écrit

    def test_le_compositeur_exige_l_ecriture(self):
        """Par identifiants comme par domaine : le cœur résout les fiches de l'une ou l'autre façon."""
        notice = self._breach()
        notice.action_send()
        template = self.env.ref("privacy_breach_notice.mail_template_breach_notice")
        tiers = self.env["res.partner"].create({"name": "Tiers", "email": "tiers@essai.invalid"})
        for cible in ({"default_res_ids": notice.ids},
                      {"default_res_domain": "[('id', '=', %d)]" % notice.id}):
            composer = self.env["mail.compose.message"].with_user(self.reader).with_context(
                default_model=notice._name, default_composition_mode="mass_mail",
                default_template_id=template.id, **cible).create({"partner_ids": [(6, 0, tiers.ids)]})
            with self.assertRaisesRegex(AccessError, "est un registre", msg=str(cible)):
                composer.action_send_mail()

    def test_send_mail_ne_rattache_rien_a_un_avis(self):
        """`email_values` remplace le modèle et la fiche du courriel, créé en sudo."""
        notice = self._breach()
        notice.action_send()
        template = self.env["mail.template"].create({
            "name": "Gabarit ordinaire", "model_id": self.env.ref("base.model_res_partner").id,
            "subject": "Bonjour", "body_html": "<p>Bonjour</p>", "email_to": "z@essai.invalid"})
        lecteur = template.with_user(self.reader)
        self.assertTrue(lecteur.send_mail(self.org.id))  # le gabarit lui-même lui est permis
        with self.assertRaisesRegex(AccessError, "est un registre"):
            lecteur.send_mail(self.org.id, email_values={"model": notice._name, "res_id": notice.id})

    def test_une_activite_ne_se_pose_ni_ne_se_deplace_sur_un_avis(self):
        notice = self._breach()
        modele = self.env["ir.model"]._get_id(notice._name)
        activite = self.org.activity_schedule("mail.mail_activity_data_todo", user_id=self.reader.id,
                                              summary="À moi")
        a_moi = activite.with_user(self.reader)
        a_moi.write({"summary": "Toujours à moi"})  # la sienne, sur sa fiche : permis
        with self.assertRaises(AccessError):
            a_moi.write({"res_model_id": modele, "res_id": notice.id})
        with self.assertRaises(AccessError):
            self.env["mail.activity"].with_user(self.reader).sudo().create({
                "res_model_id": modele, "res_id": notice.id, "user_id": self.reader.id,
                "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id})
        notice.with_user(self.manager).activity_schedule("mail.mail_activity_data_todo",
                                                         user_id=self.manager.id)

    def test_les_chemins_sudo_du_coeur_jugent_l_acteur(self):
        """La note d'un envoi de SMS en masse, ou tout journal posé en sudo pour un lecteur."""
        notice = self._breach()
        notice.action_send()
        with self.assertRaises(AccessError):
            notice.with_user(self.reader).sudo()._message_log(body=Markup("<p>Faux</p>"))
        sms = self.env["sms.composer"].with_user(self.reader).with_context(
            default_res_model=notice._name, default_res_ids=notice.ids, default_composition_mode="mass",
            default_mass_keep_log=True).create({"body": "Faux"})
        with self.assertRaises(AccessError):
            sms.action_send_sms()
        notice.with_user(self.manager).sudo()._message_log(body=Markup("<p>Note du gestionnaire</p>"))

    def test_un_message_existant_ne_se_reecrit_ni_ne_s_efface(self):
        notice = self._breach()
        notice.action_send()
        message = notice.message_post(body=Markup("<p>Courriel au client</p>"), message_type="comment",
                                      subtype_xmlid="mail.mt_comment", partner_ids=self.reader.partner_id.ids)
        lu = message.with_user(self.reader)
        lu.toggle_message_starred()  # une étoile n'est pas le contenu
        self.assertIn(self.reader.partner_id, message.starred_partner_ids)
        with self.assertRaises(AccessError):
            lu.write({"body": "<p>Réécrit</p>"})  # destinataire notifié : Odoo le laissait faire
        for champs in ({"body": "<p>Réécrit</p>"}, {"res_id": notice.id + 1000}, {"author_id": self.org.id}):
            with self.assertRaises(AccessError, msg=str(champs)):
                lu.sudo().write(champs)
        with self.assertRaises(AccessError):
            lu.sudo().unlink()
        autre = self.org.message_post(body=Markup("<p>Ailleurs</p>"), message_type="comment")
        with self.assertRaises(AccessError):
            autre.with_user(self.reader).sudo().write({"model": notice._name, "res_id": notice.id})
        self.env.cr._breach_fresh_messages = set()  # une autre requête que celle qui l'a créé
        with self.assertRaisesRegex(AccessError, "est un registre"):
            message.with_user(self.manager).sudo().write({"body": "<p>Corrigé par le gestionnaire</p>"})
        with self.assertRaisesRegex(AccessError, "est un registre"):
            message.with_user(self.manager).sudo().unlink()  # avis envoyé : pas même le gestionnaire
        brouillon = self._breach()
        note = brouillon.with_user(self.manager).message_post(body=Markup("<p>Brouillon</p>"),
                                                              message_type="comment", subtype_xmlid="mail.mt_note")
        note.with_user(self.manager).sudo().write({"body": "<p>Brouillon corrigé</p>"})
        note.with_user(self.manager).sudo().unlink()

    def test_un_message_ne_se_pose_pas_sur_un_avis_a_venir(self):
        prochain = self.env["privacy.breach.notice"].search([], order="id desc", limit=1).id + 5
        with self.assertRaises(AccessError):
            self.env["mail.message"].with_user(self.manager).sudo().create({
                "model": "privacy.breach.notice", "res_id": prochain, "body": "<p>Préhistoire</p>",
                "message_type": "comment", "reply_to": "x@essai.invalid", "record_name": "x"})

    def test_le_renvoi_ne_repart_pas_chez_un_tiers(self):
        """Le renvoi d'Odoo crée une notification puis un courriel rattachés au message existant."""
        notice = self._breach()
        notice.action_send()
        officiel = notice.sudo().mail_id.mail_message_id
        tiers = self.env["res.partner"].create({"name": "Autre client", "email": "autre@essai.invalid"})
        Notification = self.env["mail.notification"].with_user(self.reader)
        ailleurs = self.org.message_post(body=Markup("<p>Ailleurs</p>"), message_type="comment")
        mien = Notification.create({"mail_message_id": ailleurs.id, "res_partner_id": tiers.id,
                                    "notification_type": "email"})  # le cœur le permet à la lecture
        with self.assertRaises(AccessError):
            Notification.create({"mail_message_id": officiel.id, "res_partner_id": tiers.id,
                                 "notification_type": "email", "notification_status": "exception"})
        with self.assertRaises(AccessError):
            mien.with_user(self.reader).sudo().write({"mail_message_id": officiel.id})
        Mail = self.env["mail.mail"].with_user(self.reader).sudo()
        with self.assertRaises(AccessError):
            Mail.create({"mail_message_id": officiel.id, "email_to": "autre@essai.invalid"})
        with self.assertRaises(AccessError):
            notice.sudo().mail_id.with_user(self.reader).sudo().write({"email_to": "autre@essai.invalid"})
        notice.sudo().mail_id.with_user(self.manager).sudo().write({"email_to": notice.sent_to})

    def test_un_message_du_fil_ne_change_que_d_etoile(self):
        """Liste blanche : ce que Odoo et mail_tracking ajoutent au message reste le registre."""
        notice = self._breach()
        notice.action_send()
        message = notice.message_post(body=Markup("<p>Note</p>"), message_type="comment",
                                      subtype_xmlid="mail.mt_comment", partner_ids=self.reader.partner_id.ids)
        tiers = self.env["res.partner"].create({"name": "Tiers", "email": "tiers@essai.invalid"})
        lu = message.with_user(self.reader).sudo()
        for champs in ({"email_to": "tiers@essai.invalid"}, {"recipient_cc_ids": [(4, tiers.id)]},
                       {"notified_partner_ids": [(5, 0, 0)]}, {"subject": "Autre objet"},
                       {"reply_to": "detour@tiers.invalid"}):
            with self.assertRaises(AccessError, msg=str(champs)):
                lu.write(champs)
        if "mail_tracking_needs_action" in lu._fields:  # champ de mail_tracking, s'il est installé
            lu.write({"mail_tracking_needs_action": False})

    def test_les_pieces_d_un_message_suivent_le_fil(self):
        notice = self._breach()
        notice.action_send()
        message = notice.message_post(body=Markup("<p>Note</p>"), message_type="comment",
                                      subtype_xmlid="mail.mt_note")
        piece = self.env["ir.attachment"].create({"name": "fait.txt", "raw": b"fait",
                                                  "res_model": "mail.message", "res_id": message.id})
        # Rattachée, comme celles d'une activité faite (le système le fait ici : l'avis est envoyé).
        self.env["mail.message"].browse(message.id).attachment_ids = [(4, piece.id)]
        Attachment = self.env["ir.attachment"].with_user(self.reader).sudo()
        # Posée sur le message sans y être rattachée (le .eml de bf_email) : hors du fil, permise.
        service = Attachment.create({"name": "message.eml", "raw": b"eml", "res_model": "mail.message",
                                     "res_id": message.id})
        service.write({"name": "message (2).eml"})
        service.unlink()
        with self.assertRaises(AccessError):
            message.with_user(self.reader).sudo().write({"attachment_ids": [(4, piece.copy().id)]})
        with self.assertRaises(AccessError):
            piece.with_user(self.reader).sudo().write({"raw": b"autre"})
        with self.assertRaises(AccessError):
            piece.with_user(self.reader).sudo().unlink()

    def test_la_fusion_epargne_les_auteurs_du_fil(self):
        notice = self._breach()
        notice.action_send()
        auteur = self.env["res.partner"].create({"name": "RPRP du client", "email": "rprp@client.invalid"})
        double = self.env["res.partner"].create({"name": "RPRP du client (2)", "email": "rprp@client.invalid"})
        notice.message_post(body=Markup("<p>Réponse</p>"), message_type="email", author_id=auteur.id)
        createur = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Création de contacts 2", "login": "cc2-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])]})
        wizard = self.env["base.partner.merge.automatic.wizard"].with_user(createur)
        with self.assertRaisesRegex(UserError, "avis de violation"):
            wizard._merge([auteur.id, double.id], double)
        abonne = self.env["res.partner"].create({"name": "Abonné", "email": "abonne@client.invalid"})
        double2 = self.env["res.partner"].create({"name": "Abonné (2)", "email": "abonne@client.invalid"})
        notice.message_subscribe(partner_ids=abonne.ids)
        with self.assertRaisesRegex(UserError, "avis de violation"):
            wizard._merge([abonne.id, double2.id], double2)

    def _echec_au_client(self, notice):
        client = self.env["res.partner"].create({"name": "Contact client", "email": "contact@client.invalid"})
        message = notice.with_user(self.manager).message_post(
            body=Markup("<p>Au client</p>"), message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=client.ids)
        # `mail_post_defer` reporte les notifications à son cron (en système) : on pose l'échec comme lui.
        notification = self.env["mail.notification"].search(
            [("mail_message_id", "=", message.id), ("res_partner_id", "=", client.id)]) or \
            self.env["mail.notification"].create({"mail_message_id": message.id, "res_partner_id": client.id,
                                                  "notification_type": "email"})
        notification.write({"notification_type": "email", "notification_status": "exception"})
        return message, notification

    def test_le_renvoi_par_l_assistant_reel(self):
        """`mail.resend.message` : ni renvoyer ni annuler l'échec d'autrui sur un avis."""
        notice = self._breach()
        notice.action_send()
        message, notification = self._echec_au_client(notice)
        Wizard = self.env["mail.resend.message"].with_context(mail_message_to_resend=message.id)
        wizard = Wizard.with_user(self.reader).create({})
        self.assertTrue(wizard.partner_ids.filtered("resend"), "l'assistant propose bien le renvoi")
        with self.assertRaises(AccessError):
            wizard.resend_mail_action()
        with self.assertRaises(AccessError):
            Wizard.with_user(self.reader).create({}).cancel_mail_action()
        self.assertEqual(notification.notification_status, "exception")
        Wizard.with_user(self.manager).create({}).cancel_mail_action()  # le gestionnaire, lui, annule
        self.assertEqual(notification.notification_status, "canceled")

    def test_chacun_lit_sa_notification(self):
        notice = self._breach()
        notice.action_send()
        message = notice.with_user(self.manager).message_post(
            body=Markup("<p>Pour le lecteur</p>"), message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=self.reader.partner_id.ids)
        sienne = self.env["mail.notification"].search(
            [("mail_message_id", "=", message.id), ("res_partner_id", "=", self.reader.partner_id.id)]) or \
            self.env["mail.notification"].create({"mail_message_id": message.id,
                                                  "res_partner_id": self.reader.partner_id.id,
                                                  "notification_type": "inbox"})
        sienne.with_user(self.reader).sudo().write({"is_read": True})
        self.assertTrue(sienne.is_read)
        with self.assertRaises(AccessError):
            sienne.with_user(self.reader).sudo().write({"notification_status": "canceled"})
        with self.assertRaises(AccessError):
            sienne.with_user(self.reader).sudo().unlink()
        autrui = self.env["mail.notification"].create({
            "mail_message_id": message.id, "res_partner_id": self.manager.partner_id.id,
            "notification_type": "inbox"})
        with self.assertRaises(AccessError):
            autrui.with_user(self.reader).sudo().write({"is_read": True})

    def test_une_activite_ne_se_confie_qu_a_qui_peut_ecrire(self):
        notice = self._breach()
        with self.assertRaisesRegex(UserError, "se confie"):
            notice.with_user(self.manager).activity_schedule("mail.mail_activity_data_todo", user_id=self.reader.id)
        activite = notice.with_user(self.manager).activity_schedule("mail.mail_activity_data_todo",
                                                                    user_id=self.manager.id)
        with self.assertRaisesRegex(UserError, "se confie"):
            activite.with_user(self.manager).write({"user_id": self.reader.id})

    def test_les_chemins_legitimes_passent(self):
        """Activité faite avec pièce, brouillon supprimé avec son fil, pièce ordinaire d'un lecteur."""
        notice = self._breach()
        en_main = notice.with_user(self.manager)
        activite = en_main.activity_schedule("mail.mail_activity_data_todo", user_id=self.manager.id)
        piece = self.env["ir.attachment"].with_user(self.manager).create({
            "name": "preuve.txt", "raw": b"preuve", "res_model": "mail.activity", "res_id": activite.id})
        activite.with_user(self.manager).action_feedback(feedback="Fait", attachment_ids=piece.ids)
        self.assertEqual(piece.res_model, "mail.message")
        en_main.message_post(body=Markup("<p>Brouillon</p>"), message_type="comment",
                             subtype_xmlid="mail.mt_note", attachments=[("b.txt", b"b")])
        en_main.unlink()
        self.assertFalse(notice.exists())
        self.env["ir.attachment"].with_user(self.reader).create({"name": "a moi.txt", "raw": b"x"})

    def test_une_activite_en_multisociete(self):
        """L'assigné est jugé dans ses sociétés, pas dans celles que l'appelant a cochées."""
        notice = self._breach()
        autre = self.env["res.company"].create({"name": "Autre société activité"})
        self.manager.write({"company_ids": [(4, autre.id)]})
        collegue = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Gestionnaire 2", "login": "gvp2-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_manager").id])]})
        coche = notice.with_user(self.manager).with_context(
            allowed_company_ids=[notice.company_id.id, autre.id])
        coche.activity_schedule("mail.mail_activity_data_todo", user_id=collegue.id)
        with self.assertRaisesRegex(UserError, "se confie"):
            coche.activity_schedule("mail.mail_activity_data_todo", user_id=self.reader.id)

    def test_un_gabarit_sur_l_avis_exige_l_ecriture(self):
        notice = self._breach()
        notice.action_send()
        officiel = self.env.ref("privacy_breach_notice.mail_template_breach_notice")
        copie = officiel.copy({"name": "Copie faite pour l'occasion"})
        with self.assertRaisesRegex(AccessError, "est un registre"):
            copie.with_user(self.reader).send_mail(notice.id)
        with self.assertRaisesRegex(UserError, "administration"):
            officiel.with_user(self.manager).write({"reply_to": "detour@essai.invalid"})
        with self.assertRaisesRegex(UserError, "administration"):
            officiel.with_user(self.manager).unlink()
        officiel.write({"name": officiel.name})  # l'administration (et la mise à jour du module) passe

    def test_la_passerelle_appelee_par_rpc_n_ecrit_pas_au_fil(self):
        notice = self._breach()
        notice.action_send()
        officiel = notice.sudo().mail_id.mail_message_id
        brut = ("From: Client <rprp@client-essai.invalid>\r\nTo: catchall@essai.invalid\r\n"
                "Subject: Re: avis\r\nMessage-ID: <faux-%s@essai.invalid>\r\n"
                "In-Reply-To: %s\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nAccuse (faux)\r\n")
        # La garde elle-même, appelée avec l'appelant AVANT la bascule en OdooBot. (Par le chemin complet,
        # un autre module installé peut refuser avant elle : il ne la prouverait pas.)
        with self.assertRaisesRegex(AccessError, "est un registre"):
            notice.with_user(self.reader).message_update({"body": "faux"})
        with self.assertRaises(AccessError):
            self.env["mail.thread"].with_user(self.reader).message_process(
                False, brut % ("lecteur", officiel.message_id))
        avant = len(notice.message_ids)
        self.env["mail.thread"].message_process(False, brut % ("systeme", officiel.message_id))  # fetchmail
        notice.invalidate_recordset(["message_ids"])
        self.assertEqual(len(notice.message_ids), avant + 1, "la vraie réception passe")

    def test_une_note_du_fil_ne_se_reecrit_pas(self):
        notice = self._breach()
        en_main = notice.with_user(self.manager)
        note = en_main.message_post(body=Markup("<p>Note</p>"), message_type="comment",
                                    subtype_xmlid="mail.mt_note")
        with self.assertRaisesRegex(UserError, "ne se modifie pas"):
            en_main._message_update_content(note, Markup("<p>Vidée</p>"))

    def test_un_transfert_ne_recoit_pas_le_fil(self):
        """`mail_partner_forwarding` : seuls les nommés et les abonnés de l'avis sont notifiés."""
        if "forwarding_partner_id" not in self.env["res.partner"]._fields:
            self.skipTest("mail_partner_forwarding absent")
        notice = self._breach()
        notice.action_send()
        espion = self.env["res.partner"].create({"name": "Transfert", "email": "transfert@tiers.invalid"})
        self.reader.partner_id.sudo().forwarding_partner_id = espion  # un abonné, pas l'auteur
        notice.message_subscribe(partner_ids=self.reader.partner_id.ids)
        nomme = self.env["res.partner"].create({"name": "Nommé", "email": "nomme@client.invalid"})
        message = notice.with_user(self.manager).message_post(
            body=Markup("<p>Note</p>"), message_type="comment", subtype_xmlid="mail.mt_comment",
            partner_ids=nomme.ids)
        ids = {r["id"] for r in notice._notify_get_recipients(message, {})}
        self.assertIn(nomme.id, ids)
        self.assertIn(self.reader.partner_id.id, ids)
        self.assertNotIn(espion.id, ids)

    def test_les_abonnes_d_un_avis_se_cachent_hors_du_role(self):
        notice = self._breach()
        notice.message_subscribe(partner_ids=self.rprp.ids)
        interne = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Interne sans rôle", "login": "interne-abonnes-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        domaine = [("res_model", "=", notice._name), ("res_id", "=", notice.id)]
        self.assertFalse(self.env["mail.followers"].with_user(interne).search(domaine))
        self.assertTrue(self.env["mail.followers"].with_user(self.reader).search(domaine))
        self.org.message_subscribe(partner_ids=self.rprp.ids)
        self.assertTrue(self.env["mail.followers"].with_user(interne).search(
            [("res_model", "=", "res.partner"), ("res_id", "=", self.org.id)]))  # le reste du système inchangé

    def test_un_faux_rebond_ne_marque_pas_l_avis(self):
        notice = self._breach()
        notice.action_send()
        with self.assertRaisesRegex(AccessError, "est un registre"):
            notice.with_user(self.reader)._message_receive_bounce(notice.sent_to, self.rprp)
        notice._message_receive_bounce(notice.sent_to, self.rprp)  # la passerelle, en système

    def test_une_activite_se_solde_hors_des_societes_cochees(self):
        notice = self._breach()
        autre = self.env["res.company"].create({"name": "Autre société solde"})
        self.manager.write({"company_ids": [(4, autre.id)]})
        activite = notice.with_user(self.manager).activity_schedule("mail.mail_activity_data_todo",
                                                                    user_id=self.manager.id)
        activite.with_user(self.manager).with_context(allowed_company_ids=[autre.id]).action_feedback(
            feedback="Fait")
        self.assertFalse(activite.exists())

    def test_ni_copie_ni_copie_cachee_par_defaut_personnel(self):
        fields_ = self.env["mail.mail"]._fields
        if "email_bcc" not in fields_:
            self.skipTest("mail_composer_cc_bcc absent")
        self.env["ir.default"].set("mail.mail", "email_bcc", "espion@tiers.invalid", user_id=self.manager.id)
        notice = self._breach()
        notice.action_send()
        self.assertFalse(notice.sudo().mail_id.email_bcc)

    def test_le_renvoi_par_le_gestionnaire_passe(self):
        """L'envoi réécrit `message_id` à l'identique : ce n'est pas une réécriture du registre."""
        notice = self._breach()
        notice.action_send()
        message, notification = self._echec_au_client(notice)
        Mail = self.env["mail.mail"].sudo()
        avant = Mail.search([("mail_message_id", "=", message.id)])
        self.env.cr._breach_fresh_messages = set()  # le message vient d'une autre requête
        self.env["mail.resend.message"].with_context(mail_message_to_resend=message.id).with_user(
            self.manager).create({}).resend_mail_action()
        nouveaux = Mail.search([("mail_message_id", "=", message.id)]) - avant
        self.assertNotIn("exception", (nouveaux | notification.mail_mail_id).mapped("state"))
        self.assertEqual(notification.notification_status, "sent", "le courriel est bien reparti")

    def test_une_activite_avec_piece_se_solde_sur_un_avis_envoye(self):
        notice = self._breach()
        notice.action_send()
        activite = notice.with_user(self.manager).activity_schedule("mail.mail_activity_data_todo",
                                                                    user_id=self.manager.id)
        piece = self.env["ir.attachment"].with_user(self.manager).create({
            "name": "image.png", "raw": b"png", "res_model": "mail.activity", "res_id": activite.id})
        activite.with_user(self.manager).action_feedback(feedback="Fait", attachment_ids=piece.ids)
        self.assertEqual(piece.res_model, "mail.message")

    def test_l_auteur_complete_son_message_dans_la_transaction(self):
        """Comme bf_email qui corrige le corps d'un courriel qu'il vient de classer."""
        notice = self._breach()
        notice.action_send()
        en_main = notice.with_user(self.manager)
        message = en_main.message_post(body=Markup("<p>Classé</p>"), message_type="email",
                                       subtype_xmlid="mail.mt_comment")
        message.with_user(self.manager).sudo().write({"body": "<p>Classé, corrigé</p>"})
        self.env.cr._breach_fresh_messages = set()  # une autre requête
        with self.assertRaisesRegex(AccessError, "est un registre"):
            message.with_user(self.manager).sudo().write({"body": "<p>Plus tard</p>"})

    def test_copie_et_copie_cachee_restent_destinataires(self):
        notice = self._breach()
        notice.action_send()
        copie = self.env["res.partner"].create({"name": "En copie", "email": "copie@client.invalid"})
        message = notice.with_user(self.manager).message_post(
            body=Markup("<p>Note</p>"), message_type="comment", subtype_xmlid="mail.mt_comment")
        ids = {r["id"] for r in notice.with_context(is_from_composer=True, partner_cc_ids=copie)
               ._notify_get_recipients(message, {})}
        self.assertIn(copie.id, ids)

    def test_une_piece_de_reponse_ne_s_efface_pas(self):
        """Pièce d'une réponse reçue : posée sur l'avis, rattachée au message."""
        notice = self._breach()
        notice.action_send()
        portail = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "RPRP portail", "login": "rprp-portail-essai@essai.invalid", "partner_id": self.rprp.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        piece = self.env["ir.attachment"].create({"name": "reponse.pdf", "raw": b"%PDF",
                                                  "res_model": notice._name, "res_id": notice.id})
        self.env["privacy.breach.notice"].browse(notice.id).message_post(
            body=Markup("<p>Réponse</p>"), message_type="email", author_id=self.rprp.id,
            attachment_ids=piece.ids)
        for qui in (portail, self.manager):
            with self.assertRaisesRegex(AccessError, "est un registre", msg=qui.name):
                piece.with_user(qui).sudo().unlink()
            with self.assertRaisesRegex(AccessError, "est un registre", msg=qui.name):
                piece.with_user(qui).sudo().write({"public": True})

    def test_les_abonnes_se_lisent_dans_la_societe_de_l_avis(self):
        notice = self._breach()
        notice.message_subscribe(partner_ids=self.rprp.ids)
        autre = self.env["res.company"].create({"name": "Autre société abonnés"})
        lecteur_b = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Lecteur B", "login": "lecteur-b-abonnes-essai@essai.invalid",
            "company_id": autre.id, "company_ids": [(6, 0, autre.ids)],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("privacy_consent.group_privacy_user").id])]})
        domaine = [("res_model", "=", notice._name), ("res_id", "=", notice.id)]
        self.assertFalse(self.env["mail.followers"].with_user(lecteur_b).search(domaine))
        self.assertTrue(self.env["mail.followers"].with_user(self.reader).search(domaine))

    def test_une_reaction_est_au_registre(self):
        notice = self._breach()
        notice.action_send()
        message = notice.with_user(self.manager).message_post(
            body=Markup("<p>Note</p>"), message_type="comment", subtype_xmlid="mail.mt_comment")
        with self.assertRaisesRegex(AccessError, "est un registre"):
            message.with_user(self.reader).sudo()._message_reaction(
                "faux", "add", self.reader.partner_id, self.env["mail.guest"])
        message.with_user(self.manager).sudo()._message_reaction(
            "👍", "add", self.manager.partner_id, self.env["mail.guest"])

    def test_bf_email_deplace_un_courriel_et_ses_pieces_vers_l_avis(self):
        """Seul passe le rattachement d'une pièce à l'avis même de son message."""
        notice = self._breach()
        notice.action_send()
        ailleurs = self.org.message_post(body=Markup("<p>Réponse mal classée</p>"), message_type="email",
                                         attachments=[("piece.txt", b"piece")])
        piece = ailleurs.attachment_ids
        self.env.cr._breach_fresh_messages = set()
        ailleurs.with_user(self.manager).sudo().write({"model": notice._name, "res_id": notice.id})
        piece.with_user(self.manager).sudo().write({"res_model": notice._name, "res_id": notice.id})
        with self.assertRaisesRegex(AccessError, "est un registre"):
            piece.with_user(self.manager).sudo().write({"res_model": "res.partner", "res_id": self.org.id})
        with self.assertRaisesRegex(AccessError, "est un registre"):
            self.env["mail.message"].browse(ailleurs.id).with_user(self.manager).sudo().write(
                {"model": "res.partner", "res_id": self.org.id})  # ressortir d'un avis envoyé : non

    def test_un_message_venu_d_ailleurs_n_est_pas_frais_dans_l_avis(self):
        """Créé hors d'un avis dans la même requête, puis déplacé dans un avis envoyé : figé comme les autres."""
        notice = self._breach()
        notice.action_send()
        ailleurs = self.env["mail.message"].with_user(self.manager).sudo().create({
            "model": "res.partner", "res_id": self.org.id, "body": "<p>Ailleurs</p>",
            "message_type": "comment"})  # créé par le gestionnaire, dans cette requête, hors d'un avis
        ailleurs.with_user(self.manager).sudo().write({"model": notice._name, "res_id": notice.id})
        with self.assertRaisesRegex(AccessError, "est un registre"):
            ailleurs.with_user(self.manager).sudo().write({"body": "<p>Réécrit une fois dans l'avis</p>"})

    def test_un_rprp_qui_n_est_plus_designe_ne_s_efface_pas(self):
        """L'avis envoyé tient son RPRP, même quand l'organisation en a désigné un autre depuis."""
        notice = self._breach()
        notice.action_send()
        self.assertEqual(notice.officer_partner_id, self.rprp)
        nouveau = self.env["res.partner"].create({"name": "Nouveau RPRP", "parent_id": self.org.id,
                                                  "email": "nouveau@client-essai.invalid"})
        self.org.write({"privacy_officer_partner_id": nouveau.id})
        with self.assertRaises(psycopg2.errors.ForeignKeyViolation), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():  # restrict : seule la clé de l'avis retient ce partenaire
                self.rprp.unlink()
        self.assertTrue(self.rprp.exists())
