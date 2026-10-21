"""Les courriels d'hébergement passent par la mise en page commune.

Les sept gabarits portaient leur propre coquille (fond, carte, en-tête foncé au logo et
au titre, filet, pied de marque). Ils ne gardent que leur contenu ;
`bf_onboarding_base.bf_mail_layout`, que bluefox_branding remplace par la sienne, les
habille à l'envoi.

Depuis la bascule de la source en anglais, la source des corps est ANGLAISE (fichiers de données) et leur
français vit au catalogue (`i18n/fr_CA.po`, entrées `model:mail.template,body_html:…`,
corps traduits en bloc ; `fr.po` en est une copie). Les essais qui portaient sur le
corps français le lisent donc dans le msgstr du catalogue.
"""
import html
import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

import polib

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


CORPS_AU_CATALOGUE = "model:mail.template,body_html:hosting_management."
VIDES = ("br", "hr", "img")
# Replis de marque retirés des corps par la même bascule (curation : plus de « Blue Fox »
# ni d'adresse de BF en dur dans un gabarit publié). Les instantanés `*_avant.html`
# gardent les anciens, comme les bases qui n'ont pas encore migré.
REPLIS_RETIRES = (
    ("company.name or 'Blue Fox'", "company.name or 'de soutien'"),
    # Repli du bouton d'appel : le site de la société, sinon l'adresse de la base.
    ("or 'https://bluefoxconsultant.com'", "or object.get_base_url()"),
    # Lignée sans repli de marque (la signature lisait `company.name` seul) : même repli neutre.
    ('t-out="company.name"></t>', "t-out=\"company.name or 'de soutien'\"></t>"),
)


def _corps_du_catalogue(fichier):
    """{gabarit: entrée} des corps de courriel d'un catalogue (.po ou .pot)."""
    corps = {}
    for entree in polib.pofile(str(fichier)):
        if entree.obsolete:
            continue
        for occurrence, _ligne in entree.occurrences:
            if occurrence.startswith("model:mail.template,body_html:"):
                cle = occurrence.split(":", 1)[1]
                if occurrence.startswith(CORPS_AU_CATALOGUE):
                    cle = occurrence[len(CORPS_AU_CATALOGUE):]
                corps[cle] = entree
    return corps


def _francais(xmlid):
    """Corps français livré d'un gabarit : son msgstr dans `i18n/fr_CA.po`."""
    return _corps_du_catalogue(RACINE / "i18n" / "fr_CA.po")[xmlid].msgstr


def _canon(corps):
    """`_norme`, plus une seule écriture du même HTML.

    Le catalogue porte le corps tel qu'Odoo le sérialise (`<t …></t>`, caractères
    littéraux, `&gt;` dans les attributs) ; la source et les instantanés l'écrivent à la
    main (`<t …/>`, `&#233;`, `>` littéral). Fermer les balises auto-fermantes et
    résoudre les entités, des deux côtés, ne laisse que les écarts de contenu.
    """
    def fermer(m):
        nom, attributs = m.group(1), m.group(2)
        return "<%s%s>" % (nom, attributs) if nom in VIDES else "<%s%s></%s>" % (nom, attributs, nom)
    return _norme(html.unescape(re.sub(r"<(\w+)\b([^<>]*?)\s*/>", fermer, corps)))


@tagged("post_install", "-at_install")
class TestMiseEnPageCommune(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_US"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["hosting_management"], ["fr_CA"], overwrite=True)
        cls.client = cls.env["res.partner"].create({
            "name": "Client Essai", "email": "client@test.invalid", "lang": "fr_CA"})
        # en_US, la langue source : le corps anglais ne dépend d'aucune valeur stockée.
        cls.client_en = cls.env["res.partner"].create({
            "name": "Test Customer", "email": "customer@test.invalid", "lang": "en_US"})

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

    def _rapport_mensuel(self, client, mois):
        """Courriel du rapport mensuel rendu pour `client`, dans sa langue.

        Le gabarit porte `<field name="lang">{{ object.lang }}</field>` :
        il se rend dans la langue du client, quelle que soit celle de l'expéditeur. L'essai
        envoie donc depuis un contexte dans L'AUTRE langue : le corps attendu ne peut venir
        que du champ `lang` du gabarit.
        """
        gabarit = self.env.ref("hosting_management.mail_template_client_monthly_report")
        avant = self.env["mail.mail"].sudo().search([]).ids
        expediteur = "en_US" if client.lang == "fr_CA" else "fr_CA"
        gabarit.with_context(lang=expediteur, report_month=mois).send_mail(
            client.id, force_send=False)
        courriel = self.env["mail.mail"].sudo().search([("id", "not in", avant)])
        self.assertEqual(len(courriel), 1)
        self.assertEqual(courriel.body_html.count(CARTE), 1, "une carte, la commune")
        # Un seul en-tête au logo (celui de la mise en page), plus de slogan d'ancienne coquille.
        self.assertEqual(courriel.body_html.count("/brand/logo/"), 1)
        self.assertNotIn("Solutions TI", courriel.body_html)
        return courriel

    def test_le_gabarit_client_est_habille_une_fois(self):
        courriel = self._rapport_mensuel(self.client, "septembre")
        self.assertIn(">Rapport mensuel</p>", courriel.body_html)
        self.assertIn("septembre", courriel.body_html)
        self.assertNotIn(">Monthly report</p>", courriel.body_html)

    def test_le_gabarit_client_anglais_est_habille_une_fois(self):
        courriel = self._rapport_mensuel(self.client_en, "September")
        self.assertIn(">Monthly report</p>", courriel.body_html)
        self.assertIn("September", courriel.body_html)
        self.assertNotIn(">Rapport mensuel</p>", courriel.body_html)

    def test_la_migration_rend_la_source(self):
        """La migration rend, des instantanés français, le corps français LIVRÉ.

        Avant la bascule en anglais, ce corps était la source du fichier de données ; il est désormais
        le msgstr du catalogue fr_CA (la source est anglaise). Seuls les replis de marque
        retirés par le lot (`REPLIS_RETIRES`) sont reportés sur le rendu, chacun seulement
        si le français livré a bien perdu l'ancien et porte le nouveau : aucun autre écart
        n'est admis.
        """
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
                self.assertEqual(migration.retirer_coquille(nouveau), (None, False))
                rendu, francais = _canon(nouveau), _canon(_francais(xmlid))
                for ancien, actuel in REPLIS_RETIRES:
                    if ancien in rendu:
                        self.assertNotIn(ancien, francais)
                        self.assertIn(actuel, francais)
                        rendu = rendu.replace(ancien, actuel)
                self.assertEqual(rendu, francais)
                # La source du fichier de données est bien l'anglaise, pas ce français.
                self.assertNotEqual(_canon(_source(fichier, xmlid)), rendu)

    def test_la_mention_du_pied_reste(self):
        """La mention du pied, en anglais dans la source, en français au catalogue."""
        corps = _source("hosting_backup_email_template.xml", "email_template_backup_report")
        self.assertIn("Report generated automatically by your hosting module.", corps)
        francais = html.unescape(_francais("email_template_backup_report"))
        self.assertIn(_migration().MENTIONS[0], francais)
        self.assertIn("Rapport généré automatiquement par votre module d'hébergement.", francais)

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
        """Aucun corps du catalogue ne recopie une ancienne coquille.

        Changement de règle à la bascule en anglais : tant que la source était française, la
        règle était « aucun corps au catalogue » (une traduction n'aurait pu que recopier
        une coquille). La source étant désormais anglaise, le français des sept corps vit
        AU catalogue, traduit en bloc. L'intention se vérifie donc sur chaque corps :
        ni le msgid ni le msgstr n'ont de coquille, chaque corps vise un des sept gabarits
        connus, et les .po traduisent les sept.
        """
        migration = _migration()
        for fichier in (RACINE / "i18n").glob("*.po*"):
            if fichier.suffix not in (".po", ".pot"):
                continue
            with self.subTest(fichier=fichier.name):
                corps = _corps_du_catalogue(fichier)
                self.assertTrue(set(corps) <= set(GABARITS), sorted(set(corps) - set(GABARITS)))
                for xmlid, entree in corps.items():
                    self.assertFalse(migration.a_une_coquille(entree.msgid), (fichier.name, xmlid))
                    self.assertFalse(migration.a_une_coquille(entree.msgstr), (fichier.name, xmlid))
                if fichier.suffix == ".po":
                    self.assertEqual(set(corps), set(GABARITS))
                    for xmlid, entree in corps.items():
                        self.assertTrue(entree.msgstr.strip(), (fichier.name, xmlid))
                        self.assertNotIn("fuzzy", entree.flags, (fichier.name, xmlid))
