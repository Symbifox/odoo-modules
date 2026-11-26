"""Bibliothèque de lettres de gestion courante.

Ce que la bibliothèque promet, essai par essai :
  - un usager qui n'est PAS éditeur de modèles applique chaque modèle livré
    (le cœur ne lui rend que sept expressions, sur la chaîne entière) ;
  - chaque lettre existe en français et en anglais, et la lettre n'offre que
    ceux de sa langue ; la fusion en lot écrit à chacun dans la sienne ;
  - une montée remet un modèle livré à sa version, sans toucher à une copie
    du locataire ni à une lettre déjà écrite ;
  - aucun modèle ne nomme Blue Fox, et ceux qui touchent l'emploi ou la
    résiliation portent leur réserve.
"""
import re

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import convert_file
from odoo.tools.rendering_tools import parse_inline_template

from odoo.addons.bf_letter_writer.models.letter_document import _has_placeholder

MODULE = "bf_letter_writer"
LIBRARY_FILES = ("data/letter_library_fr.xml", "data/letter_library_en.xml")
# Les familles qui se lisent comme un avis juridique.
GUARDED_CODES = {
    "hiring_confirmation", "probation_end", "employment_certificate",
    "recommendation", "salary_increase", "complaint_ack",
    "payment_reminder", "agreement_termination",
}
RESERVES = {"fr": "n'est pas un avis juridique", "en": "is not legal advice"}


@tagged("post_install", "-at_install")
class TestBibliotheque(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Lang = cls.env["res.lang"]
        for code in ("fr_CA", "en_US"):
            Lang._activate_lang(code)
        cls.writer = new_test_user(
            cls.env, login="qa_redacteur", groups="base.group_user",
            name="QA Rédaction",
        )
        cls.partner_fr = cls.env["res.partner"].create(
            {"name": "QA Destinataire FR", "lang": "fr_CA"})
        cls.partner_en = cls.env["res.partner"].create(
            {"name": "QA Recipient EN", "lang": "en_US"})
        data = cls.env["ir.model.data"].search(
            [("module", "=", MODULE), ("model", "in", ("letter.template", "letter.quicktext"))])
        cls.shipped_templates = cls.env["letter.template"].browse(
            data.filtered(lambda d: d.model == "letter.template").mapped("res_id")).exists()
        cls.shipped_blocks = cls.env["letter.quicktext"].browse(
            data.filtered(lambda d: d.model == "letter.quicktext").mapped("res_id")).exists()
        cls.library = cls.shipped_templates.filtered("code")

    def _expressions(self, text):
        # Comme le cœur (_has_unsafe_expression_template_inline_template) : inst[1].
        return [inst[1] for inst in parse_inline_template(str(text or "")) if inst[1]]

    # -- le témoin : l'essai mesure-t-il quelque chose ? -------------------
    def test_00_redacteur_nest_pas_editeur_de_modeles(self):
        """Sans ce témoin, tous les essais de rendu passent pour rien."""
        self.assertFalse(self.writer.has_group("mail.group_mail_template_editor"))
        self.assertGreaterEqual(len(self.library), 24)

    def test_01_hors_palette_leve_chez_le_redacteur(self):
        """Contre-épreuve : un champ hors palette lève bien, chez cet usager."""
        template = self.env["letter.template"].create({
            "name": "QA hors palette",
            "body_html": "<p>{{ object.partner_id.email }}</p>",
        })
        letter = self.env["letter.document"].with_user(self.writer).create(
            {"partner_id": self.partner_fr.id, "template_id": template.id})
        with self.assertRaises(AccessError):
            letter.action_apply_template()

    # -- la palette --------------------------------------------------------
    def test_10_chaque_expression_livree_est_dans_la_palette(self):
        """Lu EN BASE, pas au fichier : c'est la base que le rendu lit."""
        allowed = set(self.env["letter.document"].mail_allowed_qweb_expressions())
        for record in [*self.shipped_templates, *self.shipped_blocks]:
            fields_ = ("subject", "body_html") if record._name == "letter.template" else ("body_html",)
            for fname in fields_:
                for expr in self._expressions(record[fname]):
                    self.assertIn(expr.strip(), allowed,
                                  f"{record.display_name}.{fname} : {expr!r} hors palette")

    def test_11_redacteur_applique_chaque_modele_livre(self):
        Letter = self.env["letter.document"].with_user(self.writer)
        for template in self.shipped_templates:
            partner = self.partner_en if template.language == "en" else self.partner_fr
            letter = Letter.create({"partner_id": partner.id, "template_id": template.id})
            letter.action_apply_template()
            body = str(letter.body_html)
            self.assertNotIn("{{", body, template.name)
            if "{{ object.company_id.name }}" in str(template.body_html):
                self.assertIn(letter.company_id.name, body, template.name)

    def test_12_redacteur_insere_chaque_texte_rapide(self):
        letter = self.env["letter.document"].with_user(self.writer).create(
            {"partner_id": self.partner_fr.id})
        Picker = self.env["letter.quicktext.picker"].with_user(self.writer)
        # Aucun bloc livré ne porte de champ : sans celui-ci, l'essai ne rendrait rien.
        merged = self.env["letter.quicktext"].create({
            "name": "QA bloc fusionné", "body_html": "<p>De {{ object.company_id.name }}</p>"})
        for block in [*self.shipped_blocks, merged]:
            Picker.create({"letter_id": letter.id, "quicktext_id": block.id}).action_insert()
        self.assertNotIn("{{", str(letter.body_html))
        self.assertIn(f"De {letter.company_id.name}", str(letter.body_html))

    def test_13_la_date_secrit_en_toutes_lettres_dans_la_langue(self):
        letter = self.env["letter.document"].create(
            {"partner_id": self.partner_fr.id, "letter_date": "2026-11-25"})
        self.assertEqual(letter.letter_date_display, "25 novembre 2026")
        letter.lang = "en_US"
        self.assertEqual(letter.letter_date_display, "November 25, 2026")

    # -- les langues -------------------------------------------------------
    def test_20_chaque_lettre_existe_dans_les_deux_langues(self):
        by_code = {}
        for template in self.library:
            by_code.setdefault(template.code, set()).add(template.language)
        for code, languages in by_code.items():
            self.assertEqual(languages, {"fr", "en"}, code)
        self.assertGreaterEqual(len(by_code), 12)
        for language in ("fr", "en"):
            self.assertGreaterEqual(
                len(self.shipped_blocks.filtered(lambda b: b.language == language)), 12, language)

    def test_21_rien_de_livre_sans_langue(self):
        self.assertFalse(self.shipped_templates.filtered(lambda t: not t.language))
        self.assertFalse(self.shipped_blocks.filtered(lambda b: not b.language))

    def test_22_variante_dans_la_langue_du_destinataire(self):
        fr = self.env.ref(f"{MODULE}.template_fr_rappel_paiement")
        en = self.env.ref(f"{MODULE}.template_en_payment_reminder")
        self.assertEqual(fr._bf_variant_for("en_CA"), en)
        self.assertEqual(en._bf_variant_for("fr_CA"), fr)
        self.assertEqual(fr._bf_variant_for("fr_FR"), fr)
        self.assertEqual(fr._bf_variant_for("de_DE"), fr)

    def test_23_fusion_en_lot_ecrit_a_chacun_dans_sa_langue(self):
        fr = self.env.ref(f"{MODULE}.template_fr_convocation_rencontre")
        wizard = self.env["letter.merge.wizard"].with_user(self.writer).create({
            "template_id": fr.id,
            "recipient_ids": [(6, 0, (self.partner_fr | self.partner_en).ids)],
        })
        action = wizard.action_create_letters()
        letters = self.env["letter.document"].search(action["domain"])
        by_partner = {letter.partner_id: letter for letter in letters}
        self.assertEqual(by_partner[self.partner_fr].template_id.language, "fr")
        self.assertEqual(by_partner[self.partner_en].template_id.language, "en")
        self.assertIn("We invite you", str(by_partner[self.partner_en].body_html))

    def test_24_modele_anglais_rend_la_lettre_anglaise(self):
        en = self.env.ref(f"{MODULE}.template_en_meeting_notice")
        letter = self.env["letter.document"].create({"partner_id": self.partner_fr.id})
        self.assertEqual(letter.lang_family, "fr")
        letter.template_id = en
        letter.action_apply_template()
        self.assertEqual(letter.lang_family, "en")

    def test_24b_lettre_neuve_offre_la_bibliotheque_avant_le_destinataire(self):
        """Le champ Modèle est AU-DESSUS du destinataire : une lettre neuve sans
        langue n'offrait que les modèles sans langue."""
        letter = self.env["letter.document"].with_user(self.writer).new({})  # le formulaire, avant tout choix
        self.assertFalse(letter.lang)
        self.assertIn(letter.lang_family, ("fr", "en"))
        offered = self.env["letter.template"].search(
            ["|", ("language", "=", False), ("language", "=", letter.lang_family)])
        self.assertGreaterEqual(len(offered & self.library), 13)

    def test_24c_la_variante_preferee_est_le_modele_livre(self):
        en = self.env.ref(f"{MODULE}.template_en_payment_reminder")
        fr = self.env.ref(f"{MODULE}.template_fr_rappel_paiement")
        en.copy({"name": "AAA ma copie anglaise"})
        fr_copy = fr.copy({"name": "AAA ma copie"})
        # Ni l'ordre des noms, ni une copie, ne prennent la place du livré.
        self.assertEqual(en._bf_variant_for("fr_CA"), fr)
        self.assertEqual(fr._bf_variant_for("en_US"), en)
        # La copie du client garde son type : un destinataire anglais reçoit
        # quand même la version anglaise.
        self.assertEqual(fr_copy.code, fr.code)
        self.assertEqual(fr_copy._bf_variant_for("en_US"), en)

    def test_25_lettre_generale_rangee_en_francais(self):
        general = self.env.ref(f"{MODULE}.template_general")
        self.assertEqual((general.language, general.code), ("fr", "general"))
        self.assertEqual(general._bf_variant_for("en_US"),
                         self.env.ref(f"{MODULE}.template_en_general"))

    # -- les montées -------------------------------------------------------
    def test_30_montee_remet_le_modele_livre_et_epargne_le_reste(self):
        template = self.env.ref(f"{MODULE}.template_fr_demande_documents")
        shipped_body = str(template.body_html)
        copy = template.copy({"name": "Ma demande de documents"})
        copy.body_html = "<p>Version du locataire</p>"
        letter = self.env["letter.document"].create(
            {"partner_id": self.partner_fr.id, "template_id": template.id})
        letter.action_apply_template()
        written = str(letter.body_html)

        template.body_html = "<p>Retouche faite sur le modèle livré</p>"
        for path in LIBRARY_FILES:
            convert_file(self.env, MODULE, path, {}, mode="update", noupdate=False)
        self.env.invalidate_all()

        self.assertEqual(str(template.body_html), shipped_body)
        self.assertEqual(str(copy.body_html), "<p>Version du locataire</p>")
        self.assertEqual(str(letter.body_html), written)
        self.assertFalse(copy.get_external_id()[copy.id])

    def test_14_ecrire_le_corps_passe_le_suivi(self):
        """Le suivi (tracking) se finalise au flush du curseur, en precommit : un champ HTML
        suivi y lève « Unsupported tracking on field body_html (type html) ». Sans le flush,
        un essai (ou un parcours annulé) ne voit rien."""
        letter = self.env["letter.document"].with_user(self.writer).create(
            {"partner_id": self.partner_fr.id})
        self.env.cr.flush()
        letter.with_context(tracking_disable=False, mail_notrack=False).body_html = "<p>Corps écrit</p>"
        letter.flush_recordset()
        self.env.cr.flush()
        self.assertIn("Corps écrit", str(letter.body_html))

    # -- le contenu --------------------------------------------------------
    def test_40_aucun_modele_ne_nomme_blue_fox(self):
        for record in [*self.shipped_templates, *self.shipped_blocks]:
            text = " ".join(str(record[f] or "") for f in ("name", "body_html"))
            if record._name == "letter.template":
                text += " " + str(record.subject or "")
            self.assertNotRegex(text.lower(), r"blue\s*fox|symbifox", record.display_name)

    def test_41_les_familles_sensibles_portent_leur_reserve(self):
        guarded = self.library.filtered(lambda t: t.code in GUARDED_CODES)
        self.assertEqual(len(guarded), 2 * len(GUARDED_CODES))
        for template in guarded:
            self.assertIn(RESERVES[template.language], template.note or "", template.name)

    def test_42_ni_article_de_loi_ni_delai_chiffre(self):
        motif = re.compile(
            r"\bart(icle|\.)\s*\d|\b\d+\s*(jours?|semaines?|mois|days?|weeks?|months?)\b",
            re.IGNORECASE)
        for record in [*self.library, *self.shipped_blocks]:
            body = str(record.body_html or "")
            self.assertNotRegex(body, motif, record.display_name)

    def test_43_pas_de_tiret_cadratin(self):
        for record in [*self.library, *self.shipped_blocks]:
            if record.get_external_id()[record.id].split(".")[1].startswith(
                    ("template_fr_", "template_en_", "quicktext_fr_", "quicktext_en_")):
                self.assertNotIn("—", str(record.body_html) + str(record.name))

    def test_44_une_lettre_avertit_tant_quun_crochet_reste(self):
        template = self.env.ref(f"{MODULE}.template_fr_attestation_emploi")
        letter = self.env["letter.document"].create(
            {"partner_id": self.partner_fr.id, "template_id": template.id})
        letter.action_apply_template()
        self.assertTrue(letter.merge_warning)
        letter.body_html = "<p>Attestation complète, rien à remplir.</p>"
        self.assertFalse(letter.merge_warning)
        # Ce qu'une vraie lettre met entre crochets ne déclenche rien.
        letter.body_html = "<p>Il écrit [sic] et cite [...] puis [12].</p>"
        self.assertFalse(letter.merge_warning)
        # Le titre est aussi l'objet du courriel.
        letter.name = "Rappel : facture [numéro de facture]"
        self.assertTrue(letter.merge_warning)

    def test_45_chaque_crochet_livre_est_vu(self):
        """Un passage entre crochets que la détection ne voit pas part tel quel."""
        for record in [*self.library, *self.shipped_blocks]:
            texts = [str(record.body_html or "")]
            if record._name == "letter.template":
                texts.append(record.subject or "")
            for text in texts:
                for inner in re.findall(r"\[([^\[\]]*)\]", text):
                    self.assertTrue(_has_placeholder(f"[{inner}]"),
                                    f"{record.display_name} : [{inner}]")
