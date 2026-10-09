"""Les courriels d'hébergement passent par la mise en page commune.

Les sept gabarits portaient leur propre coquille (fond, carte, en-tête foncé au logo et
au titre, filet, pied de marque). Ils ne gardent que leur contenu ;
`bf_onboarding_base.bf_mail_layout`, que bluefox_branding remplace par la sienne, les
habille à l'envoi.
"""
import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

RACINE = Path(__file__).resolve().parent.parent
MISE_EN_PAGE = "bf_onboarding_base.bf_mail_layout"
CARTE = "box-shadow:0 4px 24px"
GABARITS = (
    "email_template_backup_report", "mail_template_hosting_digest",
    "mail_template_client_generic", "mail_template_client_monthly_report",
    "mail_template_client_maintenance_notice", "mail_template_client_intervention_report",
    "mail_template_client_welcome",
)


def _migration():
    chemin = RACINE / "migrations" / "18.0.2.62.0" / "post-migrate.py"
    spec = importlib.util.spec_from_file_location("hosting_management_migration_2_62_0", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(fichier, xmlid):
    texte = (RACINE / "data" / fichier).read_text(encoding="utf-8")
    enregistrement = re.search(r'<record id="%s" model="mail.template">(.*?)</record>' % xmlid, texte, re.S)
    return re.search(r'<field name="body_html"[^>]*>(.*?)</field>', enregistrement.group(1), re.S).group(1)


def _norme(corps):
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", corps)).strip()


@tagged("post_install", "-at_install")
class TestMiseEnPageCommune(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env["res.partner"].create({
            "name": "Client Essai", "email": "client@test.invalid", "lang": "fr_CA"})

    def setUp(self):
        super().setUp()
        patcher = patch("odoo.addons.mail.models.mail_mail.MailMail.send", lambda s, *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_les_gabarits_pointent_la_mise_en_page_commune(self):
        for xmlid in GABARITS:
            with self.subTest(gabarit=xmlid):
                self.assertEqual(self.env.ref("hosting_management." + xmlid).email_layout_xmlid,
                                 MISE_EN_PAGE)

    def test_aucune_langue_stockee_ne_garde_sa_coquille(self):
        migration = _migration()
        self.env.flush_all()
        for xmlid in GABARITS:
            gabarit = self.env.ref("hosting_management." + xmlid)
            self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [gabarit.id])
            for lang, corps in (self.env.cr.fetchone()[0] or {}).items():
                with self.subTest(gabarit=xmlid, lang=lang):
                    self.assertFalse(migration.a_une_coquille(corps))

    def test_le_gabarit_client_est_habille_une_fois(self):
        gabarit = self.env.ref("hosting_management.mail_template_client_monthly_report")
        avant = self.env["mail.mail"].sudo().search([]).ids
        gabarit.with_context(report_month="septembre").send_mail(self.client.id, force_send=False)
        courriel = self.env["mail.mail"].sudo().search([("id", "not in", avant)])
        self.assertEqual(len(courriel), 1)
        self.assertEqual(courriel.body_html.count(CARTE), 1, "une carte, la commune")
        # Un seul en-tête au logo (celui de la mise en page), plus de slogan d'ancienne coquille.
        self.assertEqual(courriel.body_html.count("/brand/logo/"), 1)
        self.assertNotIn("Solutions TI", courriel.body_html)
        self.assertIn(">Rapport mensuel</p>", courriel.body_html)
        self.assertIn("septembre", courriel.body_html)

    def test_la_migration_rend_la_source(self):
        migration = _migration()
        for donnee, fichier, xmlid in (
                ("rapport_mensuel_avant.html", "hosting_client_email_templates.xml",
                 "mail_template_client_monthly_report"),
                ("bienvenue_avant.html", "hosting_client_email_templates.xml",
                 "mail_template_client_welcome"),
                ("sauvegarde_avant.html", "hosting_backup_email_template.xml",
                 "email_template_backup_report")):
            with self.subTest(gabarit=xmlid):
                avant = (RACINE / "tests" / "data" / donnee).read_text(encoding="utf-8")
                nouveau, ok = migration.retirer_coquille(avant)
                self.assertTrue(ok)
                self.assertEqual(_norme(nouveau), _norme(_source(fichier, xmlid)))
                self.assertEqual(migration.retirer_coquille(nouveau), (None, False))

    def test_la_mention_du_pied_reste(self):
        corps = _source("hosting_backup_email_template.xml", "email_template_backup_report")
        self.assertIn("Rapport g", corps)
        self.assertIn("automatiquement par votre module", corps)

    def test_un_texte_inconnu_hors_du_contenu_fait_refuser(self):
        migration = _migration()
        avant = (RACINE / "tests" / "data" / "rapport_mensuel_avant.html").read_text(encoding="utf-8")
        avec_inconnu = avant.replace("</table>\n", "</table>\n<p>Texte important hors carte</p>\n", 1)
        self.assertNotEqual(avec_inconnu, avant)
        self.assertEqual(migration.retirer_coquille(avec_inconnu), (None, False))

    def test_un_texte_inconnu_dans_le_pied_fait_refuser(self):
        migration = _migration()
        avant = (RACINE / "tests" / "data" / "rapport_mensuel_avant.html").read_text(encoding="utf-8")
        # Juste avant la dernière fermeture de table : après le contenu, dans le pied.
        i = avant.rindex("</table>")
        avec_inconnu = avant[:i] + "<p>Clause importante du pied</p>\n" + avant[i:]
        self.assertGreater(i, avant.index('<td style="padding:'), "insertion après le contenu")
        self.assertEqual(migration.retirer_coquille(avec_inconnu), (None, False))

    def test_les_traductions_ne_recopient_pas_les_corps(self):
        for fichier in (RACINE / "i18n").glob("*.po*"):
            if fichier.suffix not in (".po", ".pot"):
                continue
            with self.subTest(fichier=fichier.name):
                self.assertNotIn("model:mail.template,body_html",
                                 fichier.read_text(encoding="utf-8"))
