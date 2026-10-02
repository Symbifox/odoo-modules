"""Les six courriels de consentement passent par la mise en page commune.

Chacun portait deux coquilles complètes, une par langue (fond, carte, en-tête
au logo et au titre, filets, pied, bandeau du bas). Ils ne gardent que le
contenu ; `bf_onboarding_base.bf_mail_layout`, que bluefox_branding remplace
par la sienne, les habille à l'envoi. Les gabarits sont en `noupdate` : la
migration 18.0.5.3.0 découpe chaque langue stockée avec le même outil que la
source, tout ou rien par gabarit.
"""

import html
import importlib.util
import json
import re
from pathlib import Path

from odoo.tests import TransactionCase, tagged

GABARITS = {
    "mail_template_consent_request": ("Consent request", "Demande de consentement"),
    "mail_template_consent_expiring": ("Expiry notice", "Avis d'expiration"),
    "mail_template_consent_reminder_1": ("Consent reminder", "Rappel de consentement"),
    "mail_template_consent_reminder_2": ("Final reminder", "Dernier rappel"),
    "mail_template_consent_renewal_confirmation": ("Confirmation", "Confirmation"),
    "mail_template_consent_granted_confirmation": ("Confirmation", "Confirmation"),
}
COMMUNE = "bf_onboarding_base.bf_mail_layout"
# La carte de la mise en page commune (copie de secours et originale) : une, pas deux.
CARTE = "box-shadow:0 4px 24px"
# Ce que seule l'ancienne coquille portait.
TRACES = ("#F8FAFC", 'width="600"', "/brand/logo/", "<!-- Footer -->")
# Dans un courriel ENVOYÉ, la mise en page commune porte elle-même un fond #F8FAFC,
# un pied et un logo : seules ces marques-ci n'appartiennent qu'à l'ancienne coquille.
TRACES_ENVOI = ("<!-- Accent Line -->", "<!-- Bottom Accent -->")
LOGO_ANCIEN = re.compile(r"/brand/logo/\d+/brand")
RACINE = Path(__file__).resolve().parent.parent


def _migration(version="18.0.5.3.0"):
    chemin = RACINE / "migrations" / version / "post-migrate.py"
    spec = importlib.util.spec_from_file_location(
        "privacy_consent_migration_" + version.replace(".", "_"), chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norme(corps):
    corps = html.unescape(corps or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", corps)).strip()


@tagged("post_install", "-at_install")
class TestMiseEnPage(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env["res.lang"]._activate_lang("fr_CA")
        partner = self.env["res.partner"].create(
            {"name": "Personne Essai", "email": "personne@example.invalid"})
        purpose = self.env["privacy.purpose"].search([], limit=1)
        self.consent = self.env["privacy.consent"].create({
            "subject_partner_id": partner.id, "purpose_id": purpose.id})

    def _gabarit(self, xmlid):
        return self.env.ref("privacy_consent." + xmlid)

    def _valeurs(self, template):
        self.env.flush_all()
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [template.id])
        return self.env.cr.fetchone()[0] or {}

    def _rendu(self, xmlid, lang):
        template = self._gabarit(xmlid).with_context(privacy_contact_lang=lang, lang=lang)
        return template._render_field(
            "body_html", self.consent.ids, compute_lang=True)[self.consent.id]

    def test_les_six_gabarits_pointent_la_mise_en_page_commune(self):
        for xmlid in GABARITS:
            with self.subTest(gabarit=xmlid):
                self.assertEqual(self._gabarit(xmlid).email_layout_xmlid, COMMUNE)

    def test_aucune_langue_stockee_ne_garde_sa_coquille(self):
        for xmlid, (titre_en, titre_fr) in GABARITS.items():
            for lang, corps in self._valeurs(self._gabarit(xmlid)).items():
                with self.subTest(gabarit=xmlid, lang=lang):
                    for trace in TRACES:
                        self.assertNotIn(trace, corps)
                    self.assertFalse(re.search(
                        r"(?<![\w-])(background|border-left|border-right):", corps))
                    for titre in {titre_en, titre_fr}:
                        self.assertIn(">%s</p>" % titre, html.unescape(corps), "surtitre")

    def test_aucun_rendu_ne_mene_aux_preferences(self):
        """Le centre de préférences demande une session : pour la personne qu'on
        écrit, c'était l'écran de connexion. Chaque courriel mène à la
        page à jeton de son consentement."""
        page = "/privacy/consent/%s/%s" % (self.consent.id, self.consent.access_token)
        for xmlid in GABARITS:
            for lang in ("en_US", "fr_CA"):
                with self.subTest(gabarit=xmlid, lang=lang):
                    rendu = self._rendu(xmlid, lang)
                    self.assertNotIn("/my/privacy/preferences", rendu)
                    self.assertEqual(rendu.count(page), 1, "un bouton, la page à jeton")

    def test_la_migration_rend_la_source(self):
        """Les outils des migrations 5.3.0 puis 5.4.0, appliqués à l'ancien corps,
        rendent la source neuve."""
        migration = _migration()
        avant = (RACINE / "tests" / "data" / "consent_request_avant.html").read_text(encoding="utf-8")
        nouveau, n = migration.retirer_coquilles(avant)
        self.assertEqual(n, 2, "une coquille par langue")
        nouveau, manques = _migration("18.0.5.4.0").retoucher("mail_template_consent_request", nouveau)
        self.assertEqual(manques, [])
        source = self._valeurs(self._gabarit("mail_template_consent_request"))["en_US"]
        self.assertEqual(_norme(nouveau), _norme(source))
        self.assertEqual(migration.retirer_coquilles(source), (None, 0),
                         "rejouée, la migration retoucherait une valeur déjà découpée")
        self.assertEqual(_migration("18.0.5.4.0").retoucher("mail_template_consent_request", source),
                         (source, []), "rejouée, la migration 5.4.0 retoucherait la source")

    def test_la_migration_decoupe_chaque_langue_et_pose_la_mise_en_page(self):
        migration = _migration()
        template = self._gabarit("mail_template_consent_request")
        avant = (RACINE / "tests" / "data" / "consent_request_avant.html").read_text(encoding="utf-8")
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s, 'fr_CA', %s),"
            " email_layout_xmlid = NULL WHERE id = %s", [avant, avant, template.id])
        template.invalidate_recordset()
        migration.migrate(self.env.cr, "18.0.5.2.0")
        valeurs = self._valeurs(template)
        self.assertEqual(set(valeurs), {"en_US", "fr_CA"})
        for corps in valeurs.values():
            self.assertNotIn("#F8FAFC", corps)
        template.invalidate_recordset()
        self.assertEqual(template.email_layout_xmlid, COMMUNE)

    def test_un_corps_refait_a_la_main_reste_tel_quel(self):
        """Sans les repères de la coquille, rien ne bouge : ni le corps, ni la mise
        en page (elle habillerait un corps qui porte déjà la sienne)."""
        migration = _migration()
        template = self._gabarit("mail_template_consent_request")
        maison = '<div style="background-color:#f2f2f2;"><p>Notre demande, notre habillage.</p></div>'
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s),"
            " email_layout_xmlid = NULL WHERE id = %s", [maison, template.id])
        template.invalidate_recordset()
        migration.migrate(self.env.cr, "18.0.5.2.0")
        self.assertEqual(self._valeurs(template), {"en_US": maison})
        template.invalidate_recordset()
        self.assertFalse(template.email_layout_xmlid)

    def test_un_envoi_ne_porte_qu_une_carte(self):
        avant = self.env["mail.mail"].sudo().search([]).ids
        self._gabarit("mail_template_consent_request").send_mail(self.consent.id, force_send=False)
        courriel = self.env["mail.mail"].sudo().search([("id", "not in", avant)])
        self.assertEqual(len(courriel), 1)
        self.assertEqual(courriel.body_html.count(CARTE), 1, "une carte, la commune")
        for trace in TRACES_ENVOI:
            self.assertNotIn(trace, courriel.body_html)
        self.assertFalse(LOGO_ANCIEN.search(courriel.body_html))
        self.assertNotIn("utm_medium=email", courriel.body_html)

    def test_la_migration_5_4_rend_la_source_de_chaque_gabarit(self):
        """Les six corps 5.3.0 tels que stockés chez BF : la migration 5.4.0 en fait la
        source neuve, dans chaque langue stockée, et rejouée ne fait plus rien.
        Les gabarits sont en noupdate : c'est elle seule qui les retouche."""
        migration = _migration("18.0.5.4.0")
        avants = json.loads((RACINE / "tests" / "data" / "gabarits_18_0_5_3_0.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(set(avants), set(GABARITS))
        for xmlid, avant in avants.items():
            with self.subTest(gabarit=xmlid):
                template = self._gabarit(xmlid)
                source = self._valeurs(template)["en_US"]
                self.assertIn("/my/privacy/preferences", avant)
                self.env.cr.execute(
                    "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s, 'fr_CA', %s)"
                    " WHERE id = %s", [avant, avant, template.id])
                template.invalidate_recordset()
                migration.migrate(self.env.cr, "18.0.5.3.0")
                valeurs = self._valeurs(template)
                self.assertEqual(set(valeurs), {"en_US", "fr_CA"})
                for corps in valeurs.values():
                    self.assertEqual(_norme(corps), _norme(source))
                migration.migrate(self.env.cr, "18.0.5.3.0")
                self.assertEqual(self._valeurs(template), valeurs, "rejouée")

    def test_la_migration_5_4_repointe_un_lien_hors_des_reperes(self):
        """Un corps refait à la main qui mène encore aux préférences mène à la page
        à jeton ; un corps refait à la main sans ce lien reste tel quel."""
        migration = _migration("18.0.5.4.0")
        template = self._gabarit("mail_template_consent_request")
        maison = ('<div><p>Notre demande.</p><a href="{{ object.get_base_url() }}'
                  '/my/privacy/preferences">Mes choix</a></div>')
        sans_lien = '<div><p>Notre demande, sans lien.</p></div>'
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s, 'fr_CA', %s)"
            " WHERE id = %s", [maison, sans_lien, template.id])
        template.invalidate_recordset()
        migration.migrate(self.env.cr, "18.0.5.3.0")
        valeurs = self._valeurs(template)
        self.assertEqual(valeurs["fr_CA"], sans_lien)
        self.assertNotIn("/my/privacy/preferences", valeurs["en_US"])
        self.assertIn("/privacy/consent/{{ object.id }}/{{ object.access_token }}", valeurs["en_US"])
        self.assertIn("Mes choix", valeurs["en_US"])
