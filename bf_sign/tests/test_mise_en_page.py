"""Les courriels de signature passent par la mise en page commune.

Les quatre gabarits et le courriel du code de vérification portaient leur propre
coquille (enveloppe, en-tête foncé au logo et au titre, filet, carte, pied). Ils
ne gardent que leur contenu ; `bf_onboarding_base.bf_mail_layout`, que
bluefox_branding remplace par la sienne, les habille. L'invitation, la relance et
le code partent en `mail.mail` nu (le jeton de signature ne doit jamais rester
dans un message) : ils sont habillés en code, par `models/mail_layout.py`.
"""

import base64
import html
import importlib.util
import io
import re
from pathlib import Path
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from .common import BaseNeuve

GABARITS = {
    "mail_template_sign_request": "Signature requise",
    "mail_template_sign_reminder": "Signature en attente",
    "mail_template_sign_completed": "Document signé",
    "mail_template_sign_refused": "Signature refusée",
}
COMMUNE = "bf_onboarding_base.bf_mail_layout"
# La carte de la mise en page commune (copie de secours et originale) : une, pas deux.
CARTE = "box-shadow:0 4px 24px"
# Ce que seule l'ancienne coquille portait, dans un courriel envoyé.
ANCIEN_ENTETE = re.compile(r"border-radius:10px 10px 0 0|/brand/logo/\d+/brand")
# Dans un corps stocké.
TRACES = ("max-width:600px", "border-radius:10px 10px 0 0", "/brand/logo/")
RACINE = Path(__file__).resolve().parent.parent


def _migration():
    chemin = RACINE / "migrations" / "18.0.3.27.0" / "post-migrate.py"
    spec = importlib.util.spec_from_file_location("bf_sign_migration_3_27_0", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norme(corps):
    corps = re.sub(r"<!--.*?-->", "", html.unescape(corps or "").replace("\xa0", " "), flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", corps)).strip()


def _pdf():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, "Document d'essai")
    c.showPage()
    c.save()
    return buf.getvalue()


@tagged("post_install", "-at_install", "bf_sign")
class TestMiseEnPage(BaseNeuve, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.name = "Société Essai"

    def setUp(self):
        super().setUp()
        # Les courriels restent en file : on lit le corps qui serait parti.
        patcher = patch("odoo.addons.mail.models.mail_mail.MailMail.send", lambda s, *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _demande(self, nom_signataire="Signataire Essai"):
        demande = self.env["bf.sign.request"].create({
            "name": "Contrat d'essai",
            "document_file": base64.b64encode(_pdf()),
            "document_filename": "essai.pdf",
        })
        signataire = self.env["bf.sign.signer"].create({
            "request_id": demande.id, "name": nom_signataire, "email": "signataire@example.com"})
        return demande, signataire

    def _nouveaux(self, avant):
        return self.env["mail.mail"].sudo().search([("id", "not in", avant)])

    def _valeurs(self, template):
        self.env.flush_all()
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        return self.env.cr.fetchone()[0] or {}

    def test_les_quatre_gabarits_pointent_la_mise_en_page_commune(self):
        for xmlid in GABARITS:
            with self.subTest(gabarit=xmlid):
                self.assertEqual(self.env.ref("bf_sign." + xmlid).email_layout_xmlid, COMMUNE)

    def test_aucune_langue_stockee_ne_garde_sa_coquille(self):
        for xmlid, titre in GABARITS.items():
            for lang, corps in self._valeurs(self.env.ref("bf_sign." + xmlid)).items():
                with self.subTest(gabarit=xmlid, lang=lang):
                    for trace in TRACES:
                        self.assertNotIn(trace, corps)
                    self.assertFalse(re.search(
                        r"(?<![\w-])(background|border-left|border-right):", corps))
                    self.assertIn(">%s</p>" % titre, html.unescape(corps), "surtitre")

    def test_l_invitation_nue_est_habillee_une_fois(self):
        demande, signataire = self._demande()
        avant = self.env["mail.mail"].sudo().search([]).ids
        demande._email_signer(signataire)
        courriel = self._nouveaux(avant)
        self.assertEqual(len(courriel), 1)
        corps = courriel.body_html
        self.assertEqual(corps.count(CARTE), 1, "une carte, la commune")
        self.assertFalse(ANCIEN_ENTETE.search(corps))
        self.assertIn(">Signature requise</p>", corps)
        self.assertIn(signataire._signing_url(), html.unescape(corps))
        self.assertFalse(courriel.model, "le jeton ne doit pas rester rattaché à un document")

    def test_un_gabarit_sans_mise_en_page_n_est_pas_habille_en_code(self):
        """Comme ``send_mail`` : un corps refait à la main, que la migration laisse sans
        mise en page, porte son propre habillage et ne doit pas en recevoir un second."""
        self.env.ref("bf_sign.mail_template_sign_request").email_layout_xmlid = False
        demande, signataire = self._demande()
        avant = self.env["mail.mail"].sudo().search([]).ids
        demande._email_signer(signataire)
        courriel = self._nouveaux(avant)
        self.assertEqual(len(courriel), 1)
        self.assertNotIn(CARTE, courriel.body_html)

    def test_le_code_de_verification_est_habille_et_echappe(self):
        demande, signataire = self._demande(nom_signataire="<b>Piège</b>")
        demande.name = "<a href=x>Contrat</a>"
        avant = self.env["mail.mail"].sudo().search([]).ids
        signataire._otp_email("123456")
        courriel = self._nouveaux(avant)
        self.assertEqual(len(courriel), 1)
        corps = courriel.body_html
        self.assertEqual(corps.count(CARTE), 1)
        self.assertFalse(ANCIEN_ENTETE.search(corps))
        self.assertIn(">Code de vérification</p>", corps)
        self.assertIn("123456", corps)
        self.assertNotIn("<b>Piège</b>", corps)
        self.assertNotIn("<a href=x>", corps)
        self.assertIn("&lt;b&gt;Piège&lt;/b&gt;", corps)

    def test_le_refus_passe_par_send_mail_et_la_mise_en_page(self):
        demande, _signataire = self._demande()
        avant = self.env["mail.mail"].sudo().search([]).ids
        self.env.ref("bf_sign.mail_template_sign_refused").send_mail(demande.id, force_send=False)
        courriel = self._nouveaux(avant)
        self.assertEqual(len(courriel), 1)
        self.assertEqual(courriel.body_html.count(CARTE), 1)
        self.assertFalse(ANCIEN_ENTETE.search(courriel.body_html))

    def test_la_migration_rend_la_source(self):
        """L'outil de la migration, appliqué à l'ancien corps, rend la source neuve."""
        migration = _migration()
        avant = (RACINE / "tests" / "data" / "sign_request_avant.html").read_text(encoding="utf-8")
        nouveau, ok = migration.retirer_coquille(avant)
        self.assertTrue(ok)
        source = self._valeurs(self.env.ref("bf_sign.mail_template_sign_request"))["en_US"]
        self.assertEqual(_norme(nouveau), _norme(source))
        self.assertEqual(migration.retirer_coquille(source), (None, False),
                         "rejouée, la migration retoucherait une valeur déjà découpée")

    def test_la_migration_decoupe_chaque_langue_et_pose_la_mise_en_page(self):
        migration = _migration()
        template = self.env.ref("bf_sign.mail_template_sign_request")
        avant = (RACINE / "tests" / "data" / "sign_request_avant.html").read_text(encoding="utf-8")
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s, 'fr_CA', %s),"
            " email_layout_xmlid = NULL WHERE id = %s", [avant, avant, template.id])
        template.invalidate_recordset()
        migration.migrate(self.env.cr, "18.0.3.26.4")
        valeurs = self._valeurs(template)
        self.assertEqual(set(valeurs), {"en_US", "fr_CA"})
        for corps in valeurs.values():
            self.assertNotIn("border-radius:10px 10px 0 0", corps)
        template.invalidate_recordset()
        self.assertEqual(template.email_layout_xmlid, COMMUNE)

    def test_un_corps_refait_a_la_main_reste_tel_quel(self):
        migration = _migration()
        template = self.env.ref("bf_sign.mail_template_sign_reminder")
        maison = '<div style="background-color:#f2f2f2;"><p>Notre relance, notre habillage.</p></div>'
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s),"
            " email_layout_xmlid = NULL WHERE id = %s", [maison, template.id])
        template.invalidate_recordset()
        migration.migrate(self.env.cr, "18.0.3.26.4")
        self.assertEqual(self._valeurs(template), {"en_US": maison})
        template.invalidate_recordset()
        self.assertFalse(template.email_layout_xmlid)
