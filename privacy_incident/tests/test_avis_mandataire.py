"""L'avis d'un mandataire devient une fiche du registre."""
import base64
import hashlib

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAvisMandataire(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processor = cls.env["res.partner"].create({"name": "Hébergeur Essai", "is_company": True})
        cls.officer = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "RPRP Essai", "login": "rprp-essai@essai.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("privacy_consent.group_privacy_officer").id])]})
        cls.Incident = cls.env["privacy.incident"].with_user(cls.officer)
        cls.pdf = b"%PDF avis"
        cls.pdf_sha = hashlib.sha256(cls.pdf).hexdigest()

    def _data(self, **extra):
        data = {
            "ref": "AV-2026-0007", "version": 1, "received_at": "2026-10-07 18:30:00",
            "sha256": self.pdf_sha, "pdf": self.pdf, "nature": "unauthorized_access",
            "circumstances": "Mot de passe exposé.", "pi_description": "Coordonnées des employés.",
            "subject_count": 12, "subject_count_quebec": 12, "facts": "Données chiffrées : non.",
        }
        data.update(extra)
        return data

    def test_avis_devient_fiche_declaree(self):
        incident = self.Incident._create_from_processor_notice(self.processor, self._data())
        self.assertEqual(incident.state, "draft")
        self.assertTrue(incident.declared_by_processor)
        self.assertEqual(incident.processor_partner_id, self.processor)
        self.assertEqual(str(incident.awareness_date), "2026-10-07",
                         "la prise de connaissance est la réception de l'avis")
        self.assertEqual(incident.incident_type, "unauthorized_access")
        self.assertEqual(incident.subject_count, 12)
        self.assertFalse(incident.partner_id, "le registre de la société elle-même : aucun portail ne le voit")
        self.assertTrue(incident.processor_notice_intact)
        self.assertEqual(incident.serious_harm_risk, "undetermined", "l'évaluation revient au responsable")
        self.assertEqual(incident.processor_notice_sha256, self.pdf_sha)
        self.assertEqual(base64.b64decode(incident.processor_notice_pdf), self.pdf)

    def test_empreinte_annoncee_fausse_refusee(self):
        with self.assertRaisesRegex(UserError, "empreinte"):
            self.Incident._create_from_processor_notice(self.processor, self._data(sha256="0" * 64))

    def test_empreinte_calculee_quand_on_consigne_a_la_main(self):
        incident = self.Incident.create({
            "title": "Avis reçu par courriel", "processor_partner_id": self.processor.id,
            "processor_notice_pdf": base64.b64encode(self.pdf), "processor_notice_sha256": "faux"})
        self.assertEqual(incident.processor_notice_sha256, self.pdf_sha, "calculée sur les octets, pas recopiée")

    def test_une_fiche_saisie_a_la_main_reste_coherente(self):
        incident = self.Incident.create({"title": "Avis reçu par courriel",
                                         "processor_partner_id": self.processor.id})
        incident.processor_notice_pdf = base64.b64encode(self.pdf)
        self.assertEqual(incident.processor_notice_sha256, self.pdf_sha)
        with self.assertRaisesRegex(UserError, "indiquez aussi sa version"):
            incident.processor_notice_pdf = base64.b64encode(b"%PDF autre")
        with self.assertRaisesRegex(UserError, "PDF consigné"):
            incident.processor_notice_sha256 = "0" * 64
        incident.processor_partner_id = self.env["res.partner"].create({"name": "Corrigé", "is_company": True})
        incident.write({"processor_notice_version": 2, "processor_notice_pdf": base64.b64encode(b"%PDF v2")})
        self.assertEqual(incident.processor_notice_sha256, hashlib.sha256(b"%PDF v2").hexdigest(),
                         "une mise à jour reçue par courriel se consigne à la main")
        with self.assertRaisesRegex(UserError, "verrou"):
            incident.processor_notice_locked = True

    def test_une_fiche_federee_est_verrouillee(self):
        incident = self.Incident.sudo()._create_from_processor_notice(self.processor, self._data()).with_user(self.officer)
        self.assertTrue(incident.processor_notice_locked)
        for vals in ({"processor_notice_pdf": base64.b64encode(b"%PDF autre"), "processor_notice_version": 9},
                     {"processor_notice_ref": "AUTRE"}, {"processor_notice_sha256": "0" * 64}):
            with self.assertRaisesRegex(UserError, "fédération"):
                incident.write(vals)
        incident.write({"circumstances": "Notre lecture."})  # ses propres champs restent à lui

    def test_un_verrou_ne_se_forge_pas_a_la_creation(self):
        incident = self.Incident.create({"title": "Essai", "processor_notice_locked": True})
        self.assertFalse(incident.processor_notice_locked)

    def test_mise_a_jour_ne_touche_pas_l_evaluation(self):
        incident = self.Incident._create_from_processor_notice(self.processor, self._data())
        incident.write({"circumstances": "Ma propre lecture des faits.", "serious_harm_risk": "no"})
        premiere = incident.processor_notice_received_at
        v2 = b"%PDF avis v2"
        incident.sudo()._apply_processor_update(self._data(
            version=2, pdf=v2, sha256=hashlib.sha256(v2).hexdigest(), received_at="2026-10-09 10:00:00",
            facts="Exfiltration : aucune trace."))
        self.assertEqual(incident.processor_notice_version, 2)
        self.assertEqual(incident.processor_notice_sha256, hashlib.sha256(v2).hexdigest())
        self.assertEqual(incident.processor_notice_received_at, premiere, "la première réception ne bouge plus")
        self.assertEqual(str(incident.processor_notice_last_received_at), "2026-10-09 10:00:00")
        self.assertEqual(incident.processor_facts, "Exfiltration : aucune trace.")
        self.assertEqual(incident.circumstances, "Ma propre lecture des faits.")
        self.assertEqual(incident.serious_harm_risk, "no")

    def test_ne_mute_pas_les_valeurs_de_l_appelant(self):
        vals = {"title": "Essai", "processor_notice_pdf": base64.b64encode(self.pdf)}
        self.Incident.create(vals)
        self.assertEqual(set(vals), {"title", "processor_notice_pdf"})

    def test_une_version_perimee_est_ignoree(self):
        incident = self.Incident._create_from_processor_notice(self.processor, self._data(version=3))
        self.assertFalse(incident.sudo()._apply_processor_update(self._data(version=2, sha256="b" * 64, pdf=None)))
        self.assertEqual(incident.processor_notice_version, 3)
        self.assertEqual(incident.processor_notice_sha256, self.pdf_sha)

    def test_une_mise_a_jour_sans_pdf_ne_garde_pas_l_ancien(self):
        incident = self.Incident._create_from_processor_notice(self.processor, self._data())
        incident.sudo()._apply_processor_update(self._data(version=2, pdf=None, sha256="c" * 64))
        self.assertFalse(incident.processor_notice_pdf, "le PDF de la v1 ne reste pas avec l'empreinte de la v2")
        self.assertEqual(incident.processor_notice_sha256, "c" * 64)

    def test_le_pdf_ne_se_remplace_pas_par_la_porte_de_service(self):
        """🔴 Un champ binaire se réécrit par ir.attachment sans passer par la fiche."""
        incident = self.Incident.sudo()._create_from_processor_notice(self.processor, self._data())
        piece = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "privacy.incident"), ("res_field", "=", "processor_notice_pdf"),
            ("res_id", "=", incident.id)])
        self.assertTrue(piece)
        with self.assertRaisesRegex(UserError, "depuis la fiche"):
            piece.with_user(self.officer).write({"raw": b"autre"})
        with self.assertRaisesRegex(UserError, "depuis la fiche"):
            piece.with_user(self.officer).unlink()
        self.assertTrue(incident.processor_notice_intact)

    def test_la_meme_version_ne_remplace_pas_le_pdf(self):
        incident = self.Incident.create({"title": "Courriel", "processor_notice_version": 1,
                                         "processor_notice_pdf": base64.b64encode(self.pdf)})
        with self.assertRaisesRegex(UserError, "version"):
            incident.write({"processor_notice_version": 1, "processor_notice_pdf": base64.b64encode(b"%PDF autre")})

    def test_une_empreinte_ne_s_efface_pas_et_la_version_monte(self):
        incident = self.Incident.create({"title": "Courriel", "processor_notice_version": 2,
                                         "processor_notice_pdf": base64.b64encode(self.pdf)})
        with self.assertRaisesRegex(UserError, "ne s'efface pas"):
            incident.processor_notice_sha256 = False
        with self.assertRaisesRegex(UserError, "version"):
            incident.write({"processor_notice_version": 1, "processor_notice_pdf": base64.b64encode(b"%PDF v1")})

    def test_aucune_piece_ne_se_rattache_au_pdf(self):
        incident = self.Incident.sudo()._create_from_processor_notice(self.processor, self._data())
        Attachment = self.env["ir.attachment"].with_user(self.officer)
        with self.assertRaisesRegex(UserError, "depuis la fiche"):
            Attachment.create({"name": "vieux.pdf", "raw": b"%PDF vieux", "res_model": "privacy.incident",
                               "res_id": incident.id, "res_field": "processor_notice_pdf"})
        vieille = Attachment.create({"name": "vieux.pdf", "raw": b"%PDF vieux"})
        with self.assertRaisesRegex(UserError, "depuis la fiche"):
            vieille.write({"res_model": "privacy.incident", "res_id": incident.id, "res_field": "processor_notice_pdf"})

    def test_les_faits_d_une_fiche_federee_sont_verrouilles(self):
        incident = self.Incident.sudo()._create_from_processor_notice(self.processor, self._data()).with_user(self.officer)
        with self.assertRaisesRegex(UserError, "fédération"):
            incident.processor_facts = "Réécrits"

    def test_effacer_le_pdf_ou_baisser_la_version_est_refuse(self):
        incident = self.Incident.create({"title": "Courriel", "processor_notice_version": 2,
                                         "processor_notice_pdf": base64.b64encode(self.pdf)})
        with self.assertRaisesRegex(UserError, "ne s'efface pas"):
            incident.processor_notice_pdf = False
        with self.assertRaisesRegex(UserError, "ne redescend pas"):
            incident.processor_notice_version = 1

    def test_une_piece_ne_se_rattache_pas_par_un_defaut(self):
        incident = self.Incident.create({"title": "Sans PDF"})
        Attachment = self.env["ir.attachment"].with_user(self.officer).with_context(
            default_res_field="processor_notice_pdf", default_res_model="privacy.incident",
            default_res_id=incident.id)
        piece = Attachment.create({"name": "faux.pdf", "raw": b"%PDF faux"})
        # Mesuré : Odoo n'applique pas `default_res_field` à une pièce jointe. La pièce reste
        # détachée du champ, et le PDF de la fiche n'a pas changé. (La vérification après
        # création, dans la garde, reste un filet si ce comportement changeait.)
        self.assertFalse(piece.sudo().res_field)
        self.assertFalse(incident.processor_notice_pdf)

    def test_la_fusion_ne_change_pas_le_mandataire_d_un_avis_consigne(self):
        incident = self.Incident.sudo()._create_from_processor_notice(self.processor, self._data())
        createur = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Création de contacts 4", "login": "cc4-essai@essai.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("base.group_partner_manager").id])]})
        autre = self.env["res.partner"].create({"name": "Autre hébergeur", "is_company": True})
        with self.assertRaisesRegex(UserError, "mandataire"):
            self.env["base.partner.merge.automatic.wizard"].with_user(createur)._merge(
                [self.processor.id, autre.id], autre)
        self.assertEqual(incident.processor_partner_id, self.processor)

    def test_le_mandataire_d_un_avis_consigne_ne_s_efface_pas(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        self.Incident.sudo()._create_from_processor_notice(self.processor, self._data())
        refuse = False
        try:
            with mute_logger("odoo.sql_db"), self.cr.savepoint():
                self.processor.sudo().unlink()
        except (UserError, IntegrityError):
            refuse = True
        self.assertTrue(refuse)

    def test_les_notes_internes_ne_se_lisent_pas_du_portail(self):
        org = self.env["res.partner"].create({"name": "Organisation portail", "is_company": True})
        contact = self.env["res.partner"].create({"name": "Contact portail", "parent_id": org.id,
                                                  "email": "portail@org.invalid"})
        portail = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Portail", "login": "portail-essai@essai.invalid", "partner_id": contact.id,
            "groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        incident = self.Incident.create({"title": "Incident de l'organisation", "partner_id": org.id,
                                         "internal_notes": "à ne pas montrer"})
        self.assertTrue(incident.with_user(portail).read(["title"]))  # il lit son registre
        with self.assertRaises(AccessError):
            incident.with_user(portail).read(["internal_notes"])

    def test_le_formulaire_s_ouvre_en_bin_size(self):
        """Le client web lit en `bin_size` : l'intégrité se juge sur les octets, pas sur « 44 Kb »."""
        incident = self.Incident.sudo()._create_from_processor_notice(self.processor, self._data()).with_user(self.officer)
        web = incident.with_context(bin_size=True)
        self.assertTrue(web.read(["processor_notice_intact", "processor_notice_pdf"])[0]["processor_notice_intact"])
        # L'écriture aussi : une fiche saisie à la main (la fiche fédérée est verrouillée), même empreinte.
        manuelle = self.Incident.create({"title": "Avis reçu par courriel", "processor_partner_id": self.processor.id,
                                         "processor_notice_pdf": base64.b64encode(self.pdf)})
        manuelle.with_context(bin_size=True).write({"processor_notice_sha256": self.pdf_sha})

    def test_un_transfert_ne_recoit_pas_le_registre(self):
        if "forwarding_partner_id" not in self.env["res.partner"]._fields:
            self.skipTest("mail_partner_forwarding absent")
        incident = self.Incident.sudo()._create_from_processor_notice(self.processor, self._data())
        abonne = self.env["res.partner"].create({"name": "Abonné", "email": "abonne@registre.invalid"})
        espion = self.env["res.partner"].create({"name": "Transfert", "email": "transfert@tiers.invalid"})
        abonne.forwarding_partner_id = espion
        incident.message_subscribe(partner_ids=abonne.ids)
        message = incident.message_post(body="<p>Note</p>", message_type="comment",
                                        subtype_xmlid="mail.mt_comment")
        ids = {r["id"] for r in incident._notify_get_recipients(message, {})}
        self.assertIn(abonne.id, ids)
        self.assertNotIn(espion.id, ids)
