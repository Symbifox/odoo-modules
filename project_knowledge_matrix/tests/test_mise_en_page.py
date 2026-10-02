"""Les courriels de la matrice de connaissances passent par la mise en page commune.

Les quatre gabarits de documents portaient leur propre coquille : logo de l'éditeur
codé en dur dans l'en-tête, slogan et politique de confidentialité de l'éditeur
au pied, adresse de contact factice dans le contenu, quel que soit le locataire.
L'assistant d'envoi et le rapport planifié recopiaient la même coquille en code.
Tous passent désormais par `bf_onboarding_base.bf_mail_layout`, que
bluefox_branding remplace par la sienne. Les gabarits sont `noupdate` chez
certains locataires : la migration 18.0.13.3.3 découpe chaque langue stockée.
"""

import html
import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

GABARITS = {
    "mail_template_document_distribution": "Documentation",
    "mail_template_document_reminder": "Rappel",
    "mail_template_document_update_available": "Mise à jour",
    "mail_template_document_dashboard_report": "Rapport documentaire",
}
COMMUNE = "bf_onboarding_base.bf_mail_layout"
# La carte de la mise en page commune (copie de secours et originale) : une, pas deux.
CARTE = "box-shadow:0 4px 24px"
# Ce que seule l'ancienne coquille portait.
TRACES = ('width="600"', "website/1/logo/", "border-radius:12px 12px 0 0",
          "Solutions éthiques et souveraines")
# Dans un courriel ENVOYÉ, la mise en page commune porte elle-même une carte de
# 600 px et le slogan de la société : seules ces marques-ci sont l'ancienne coquille.
TRACES_ENVOI = ("website/1/logo/", "border-radius:12px 12px 0 0;\">\n", "service@example.com")
RACINE = Path(__file__).resolve().parent.parent


def _migration():
    chemin = RACINE / "migrations" / "18.0.13.3.3" / "post-migrate.py"
    spec = importlib.util.spec_from_file_location("pkm_migration_13_3_3", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norme(corps):
    # Le chargement XML d'Odoo retire les commentaires : la source en base n'en a plus.
    corps = re.sub(r"<!--.*?-->", "", html.unescape(corps or "").replace("\xa0", " "), flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", corps)).strip()


def _avant():
    return (RACINE / "tests" / "data" / "document_distribution_avant.html").read_text(encoding="utf-8")


@tagged("post_install", "-at_install")
class TestMiseEnPage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.company
        cls.societe.write({"name": "Société Essai", "email": "contact@societe-essai.example"})
        type_doc = cls.env["project.document.type"].create({"name": "Type essai", "code": "ESSAI-MEP"})
        doc = cls.env["project.document"].create({
            "name": "Document essai", "code": "ESSAI-MEP-1", "type_id": type_doc.id, "state": "active"})
        version = cls.env["project.document.version"].create({
            "document_id": doc.id, "version_number": "1.0"})
        cls.client = cls.env["res.partner"].create({
            "name": "Client Essai", "email": "client@example.invalid"})
        cls.distribution = cls.env["project.document.distribution"].create({
            "version_id": version.id, "recipient_type": "partner",
            "partner_id": cls.client.id, "state": "pending"})

    def _gabarit(self, xmlid):
        return self.env.ref("project_knowledge_matrix." + xmlid)

    def _valeurs(self, template):
        self.env.flush_all()
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        return self.env.cr.fetchone()[0] or {}

    def _envoyer(self, xmlid):
        avant = self.env["mail.mail"].sudo().search([]).ids
        self._gabarit(xmlid).send_mail(self.distribution.id, force_send=False)
        return self.env["mail.mail"].sudo().search([("id", "not in", avant)])

    def test_les_quatre_gabarits_pointent_la_mise_en_page_commune(self):
        for xmlid in GABARITS:
            with self.subTest(gabarit=xmlid):
                self.assertEqual(self._gabarit(xmlid).email_layout_xmlid, COMMUNE)

    def test_aucune_langue_stockee_ne_garde_sa_coquille(self):
        for xmlid, titre in GABARITS.items():
            for lang, corps in self._valeurs(self._gabarit(xmlid)).items():
                with self.subTest(gabarit=xmlid, lang=lang):
                    for trace in TRACES:
                        self.assertNotIn(trace, corps)
                    self.assertNotIn("service@example.com", corps)
                    self.assertFalse(re.search(
                        r"(?<![\w-])(background|border-left|border-right):", corps))
                    self.assertIn(">%s</p>" % titre, html.unescape(corps), "surtitre")

    def test_aucune_traduction_ne_rapporte_l_ancienne_coquille(self):
        """Les `.po` portaient le corps entier de ces gabarits, ancienne coquille et logo
        de l'éditeur compris : une base installée en français les recevait tels quels, et
        la mise en page commune les aurait habillés deux fois. Sans entrée, la langue
        retombe sur la source, déjà en français."""
        for fichier in (RACINE / "i18n").glob("*.po*"):
            texte = fichier.read_text(encoding="utf-8")
            for xmlid in GABARITS:
                with self.subTest(fichier=fichier.name, gabarit=xmlid):
                    self.assertNotIn(
                        "model:mail.template,body_html:project_knowledge_matrix.%s\n" % xmlid, texte)

    def test_l_envoi_d_un_document_porte_une_carte_et_l_adresse_de_la_societe(self):
        courriel = self._envoyer("mail_template_document_distribution")
        self.assertEqual(len(courriel), 1)
        corps = courriel.body_html
        self.assertEqual(corps.count(CARTE), 1, "une carte, la commune")
        for trace in TRACES_ENVOI:
            self.assertNotIn(trace, corps)
        self.assertNotIn("utm_medium=email", corps)
        self.assertIn("mailto:contact@societe-essai.example", corps)

    def test_sans_adresse_la_phrase_de_contact_disparait(self):
        self.societe.email = False
        corps = self._gabarit("mail_template_document_distribution")._render_field(
            "body_html", self.distribution.ids)[self.distribution.id]
        self.assertNotIn("nous contacter", corps)
        self.assertNotIn("mailto:", corps)

    def test_l_assistant_et_le_rapport_planifie_prennent_la_mise_en_page(self):
        from odoo.addons.project_knowledge_matrix.wizard.matrix_send_wizard import render_branded_body
        matrice = self.env["project.knowledge.matrix"].create({"name": "Matrice essai"})
        corps = str(render_branded_body(self.societe, "<p>Contenu</p>", record=matrice))
        self.assertEqual(corps.count(CARTE), 1)
        self.assertIn(">Matrice de connaissances</p>", corps)
        self.assertIn("<p>Contenu</p>", corps)
        self.assertNotIn(">Matrice de connaissances</td>", corps, "l'ancien en-tête titré")
        for trace in TRACES_ENVOI:
            self.assertNotIn(trace, corps)

    def test_le_rapport_planifie_est_signe_par_la_societe(self):
        projet = self.env["project.project"].create({"name": "Projet essai"})
        matrice = self.env["project.knowledge.matrix"].create({
            "name": "Matrice", "project_id": projet.id,
            "recipient_ids": [(6, 0, self.client.ids)]})
        with patch.object(type(matrice), "_get_pdf_binary", lambda s: b"%PDF-1.4"), \
                patch("odoo.addons.mail.models.mail_mail.MailMail.send", lambda s, *a, **k: None):
            matrice._send_report_to_recipients()
        courriel = self.env["mail.mail"].search(
            [("recipient_ids", "in", self.client.ids)], order="id desc", limit=1)
        corps = str(courriel.body_html).replace("<br/>", "<br>")
        self.assertIn("Cordialement,<br>Société Essai", corps)
        self.assertNotIn("Cordialement,<br>Blue Fox", corps)

    def test_la_migration_rend_la_source(self):
        """L'outil de la migration, appliqué à l'ancien corps, rend la source neuve."""
        migration = _migration()
        nouveau, ok = migration.retirer_coquille(_avant())
        self.assertTrue(ok)
        source = self._valeurs(self._gabarit("mail_template_document_distribution"))["en_US"]
        self.assertEqual(_norme(nouveau), _norme(source))
        self.assertEqual(migration.retirer_coquille(source), (None, False),
                         "rejouée, la migration retoucherait une valeur déjà découpée")

    def test_la_migration_finit_les_langues_que_la_mise_a_jour_n_a_pas_ecrites(self):
        """Là où le gabarit n'est pas `noupdate`, la mise à jour a déjà écrit la source
        anglaise et la mise en page ; les autres langues gardent leur coquille."""
        migration = _migration()
        template = self._gabarit("mail_template_document_distribution")
        source = self._valeurs(template)["en_US"]
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s, 'fr_CA', %s),"
            " email_layout_xmlid = NULL WHERE id = %s", [source, _avant(), template.id])
        template.invalidate_recordset()
        migration.migrate(self.env.cr, "18.0.13.3.2")
        valeurs = self._valeurs(template)
        self.assertEqual(valeurs["en_US"], source)
        self.assertEqual(_norme(valeurs["fr_CA"]), _norme(source))
        template.invalidate_recordset()
        self.assertEqual(template.email_layout_xmlid, COMMUNE)

    def test_un_corps_refait_a_la_main_reste_tel_quel(self):
        migration = _migration()
        template = self._gabarit("mail_template_document_reminder")
        maison = '<div style="background-color:#f2f2f2;"><p>Notre rappel, notre habillage.</p></div>'
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s),"
            " email_layout_xmlid = NULL WHERE id = %s", [maison, template.id])
        template.invalidate_recordset()
        migration.migrate(self.env.cr, "18.0.13.3.2")
        self.assertEqual(self._valeurs(template), {"en_US": maison})
        template.invalidate_recordset()
        self.assertFalse(template.email_layout_xmlid)
