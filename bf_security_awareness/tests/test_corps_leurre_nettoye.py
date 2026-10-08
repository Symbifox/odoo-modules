"""Corps du courriel d'exercice nettoyé à l'écriture (mode courriel sortant
d'Odoo), sans perdre le lien suivi ni la mise en forme."""
from odoo.tests import tagged

from .test_phishing_flow import PhishingFlowCommon

PIEGE = '<img src="x" onerror="alert(1)"/><script>alert(2)</script><a href="javascript:alert(3)">j</a>'
MISE_EN_FORME = (
    '<!--[if mso]><table width="600"><tr><td><![endif]-->'
    '<table width="600" cellpadding="0" style="width:600px;font-family:Arial;">'
    '<tr><td bgcolor="#eeeeee" style="padding:12px;">Texte</td></tr></table>'
    '<p style="text-align:center;"><a t-att-href="object.landing_url" '
    'style="background:#1a73e8;color:#fff;padding:12px 22px;">Bouton</a></p>'
    '<!--[if mso]></td></tr></table><![endif]-->'
)


@tagged("post_install", "-at_install")
class TestCorpsLeurreNettoye(PhishingFlowCommon):

    def test_corps_nettoye_lien_et_forme_gardes(self):
        self.template.email_body = MISE_EN_FORME + PIEGE
        body = str(self.template.email_body)
        for bout in ("onerror", "<script", "alert(2)", "javascript:"):
            self.assertNotIn(bout, body)
        for garde in ('t-att-href="object.landing_url"', "[if mso]", 'bgcolor="#eeeeee"',
                      "background:#1a73e8", 'cellpadding="0"', "text-align:center"):
            self.assertIn(garde, body)

        # Le rendu réel, destinataire par destinataire, dans l'envoi de masse.
        self.campaign.action_prepare()
        result = self.campaign.result_ids
        mailing = self.campaign.mailing_id
        rendered = str(mailing._render_field("body_html", result.ids)[result.id])
        self.assertIn('href="%s"' % result.landing_url, rendered)
        self.assertIn("background:#1a73e8", rendered)
        # (Les commentaires conditionnels Outlook tombent au rendu QWeb de l'envoi de
        # masse, avec ou sans ce nettoyage : comportement d'Odoo, pas de ce module.)
        self.assertNotIn("onerror", rendered)
        self.assertIn(result.open_pixel_url, rendered.replace("&amp;", "&"))


@tagged("post_install", "-at_install")
class TestMigrationCorpsLeurres(PhishingFlowCommon):
    """La montée nettoie les corps de leurre existants, dans chaque langue."""

    def test_corps_existant_nettoye_et_journalise(self):
        import importlib.util
        import json
        import os
        chemin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations",
                              "18.0.2.0.3", "post-migrate.py")
        spec = importlib.util.spec_from_file_location("bf_sa_mig_nettoyage", chemin)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        self.env.cr.execute("UPDATE bf_phishing_template SET email_body = %s::jsonb WHERE id = %s",
                            [json.dumps({"en_US": MISE_EN_FORME + PIEGE, "fr_FR": "<p>fr</p>" + PIEGE}),
                             self.template.id])
        with self.assertLogs("bf_sa_mig_nettoyage", level="WARNING") as journal:
            mig.migrate(self.env.cr, "18.0.2.0.2")
        self.assertIn(f"modèle=bf.phishing.template id={self.template.id}", "\n".join(journal.output))
        self.env.cr.execute("SELECT email_body FROM bf_phishing_template WHERE id = %s", [self.template.id])
        corps = self.env.cr.fetchone()[0]
        for langue in ("en_US", "fr_FR"):
            self.assertNotIn("onerror", corps[langue])
            self.assertNotIn("<script", corps[langue])
        self.assertIn('t-att-href="object.landing_url"', corps["en_US"])
        self.assertIn("[if mso]", corps["en_US"])
