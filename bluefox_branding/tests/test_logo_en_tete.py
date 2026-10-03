"""L'en-tête foncé des courriels demande le logo de marque.

`report_brand_logo` (« Logo sur fond foncé ») existe pour ces en-têtes, et aucun ne le
lisait : ils demandaient le logo ordinaire, souvent foncé, illisible sur la couleur
foncée de la marque. Ils demandent la variante `brand` de la route, qui retombe sur le
logo ordinaire quand la société n'a pas de logo de marque.
"""
import importlib.util
from pathlib import Path

from odoo.tests import TransactionCase, tagged

from odoo.addons.bluefox_branding.hooks import _extract_templates_from_xml

RACINE = Path(__file__).resolve().parent.parent
AVANT = 't-attf-src="/brand/logo/{{ company.id }}"'
APRES = 't-attf-src="/brand/logo/{{ company.id }}/brand"'


def _migration():
    chemin = RACINE / "migrations" / "18.0.3.25.1" / "post-migrate.py"
    spec = importlib.util.spec_from_file_location("bluefox_branding_logo_en_tete", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged("post_install", "-at_install")
class TestLogoEnTete(TransactionCase):

    def test_l_avis_de_retard_du_crochet_demande_le_logo_de_marque(self):
        from odoo.addons.bluefox_branding.hooks import _LATE_INVOICE_BODY
        self.assertIn('/brand/logo/{{ company.id }}/brand"', _LATE_INVOICE_BODY)
        self.assertNotIn("/web/image/res.company/", _LATE_INVOICE_BODY)

    def test_la_mise_en_page_demande_le_logo_de_marque(self):
        arch = (RACINE / "data" / "mail_layout_override.xml").read_text(encoding="utf-8")
        self.assertIn('/brand/logo/{{ company.id }}/brand"', arch)
        self.assertNotIn(AVANT, arch)

    def test_les_gabarits_surcharges_demandent_le_logo_de_marque(self):
        for fichier in ("mail_template_overrides.xml", "mail_template_overrides_en.xml"):
            for xmlid, champs in _extract_templates_from_xml(fichier).items():
                corps = champs.get("body_html") or ""
                with self.subTest(fichier=fichier, gabarit=xmlid):
                    self.assertNotIn(AVANT, corps)
                    if "/brand/logo/" in corps:
                        self.assertIn(APRES, corps)

    def test_la_migration_remplace_l_adresse_dans_chaque_langue(self):
        xmlids = sorted(_extract_templates_from_xml())
        gabarit = next(t for t in (self.env.ref(x, raise_if_not_found=False) for x in xmlids) if t)
        autre = self.env["mail.template"].create({
            "name": "Hors surcharge", "model_id": self.env.ref("base.model_res_partner").id,
            "body_html": "<p><img %s/></p>" % AVANT})
        self.env.flush_all()
        corps = '<p><img %s/> essai</p>' % AVANT
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object('en_US', %s, 'fr_CA', %s) "
            "WHERE id = %s", [corps, corps, gabarit.id])
        _migration().migrate(self.env.cr, "18.0.3.25.0")
        self.env.cr.execute("SELECT body_html FROM mail_template WHERE id = %s", [gabarit.id])
        for lang, valeur in self.env.cr.fetchone()[0].items():
            with self.subTest(lang=lang):
                self.assertIn(APRES, valeur)
                self.assertNotIn(AVANT, valeur)
        # Un gabarit d'un autre module garde son adresse : il change avec son propre lot.
        self.env.cr.execute("SELECT body_html->>'en_US' FROM mail_template WHERE id = %s", [autre.id])
        self.assertIn(AVANT, self.env.cr.fetchone()[0])
        # L'« Avis de retard » du crochet, sans xmlid, passe aussi au logo de marque.
        retard = self.env["mail.template"].create({
            "name": "Avis de retard sur facture", "model_id": self.env.ref("account.model_account_move").id,
            "body_html": "<p>x</p>"})
        self.env.flush_all()
        corps_retard = '<p><img t-attf-src="/web/image/res.company/{{ company.id }}/logo"/></p>'
        self.env.cr.execute("UPDATE mail_template SET body_html = jsonb_build_object('fr_CA', %s) "
                            "WHERE id = %s", [corps_retard, retard.id])
        _migration().migrate(self.env.cr, "18.0.3.25.0")
        self.env.cr.execute("SELECT body_html->>'fr_CA' FROM mail_template WHERE id = %s", [retard.id])
        self.assertIn(APRES, self.env.cr.fetchone()[0])
        # Rejouée, elle ne touche plus rien.
        _migration().migrate(self.env.cr, "18.0.3.25.0")
        self.env.cr.execute("SELECT body_html->>'en_US' FROM mail_template WHERE id = %s", [gabarit.id])
        self.assertEqual(self.env.cr.fetchone()[0].count("/brand/logo/{{ company.id }}/brand"), 1)
