"""Une seule mise en page pour tous les courriels maison.

Chaque module pointe ses gabarits vers `bf_onboarding_base.bf_mail_layout`. Quand
`bluefox_branding` est installé, il en remplace le contenu par sa propre mise en
page, qui fait foi ; sans lui, c'est la copie de secours livrée ici qui s'applique.

Les tests passent par `mail.template.send_mail`, la voie réelle : c'est elle qui
applique `email_layout_xmlid` et fournit au rendu son contexte minimal (pas de
`has_button_access`, pas de `tracking_values`). Un rendu direct par `ir.qweb`
fournirait d'autres valeurs et ne prouverait rien sur l'envoi.
"""

from odoo.tests import TransactionCase, tagged

LAYOUT = "bf_onboarding_base.bf_mail_layout"
MARQUEUR = "CORPS-26238-MARQUEUR"


@tagged('post_install', '-at_install')
class TestMailLayout(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({"report_brand_primary": "#123456", "report_brand_dark": "#654321"})
        cls.partner = cls.env["res.partner"].create({
            "name": "Destinataire essai", "email": "destinataire@example.com",
        })
        cls.template = cls.env["mail.template"].create({
            "name": "Essai mise en page commune",
            "model_id": cls.env.ref("base.model_res_partner").id,
            "subject": "Essai",
            "email_to": "{{ object.email }}",
            "body_html": f"<p>{MARQUEUR}</p>",
            "email_layout_xmlid": LAYOUT,
        })

    def _envoyer(self, layout=None):
        mail = self.env["mail.mail"].browse(
            self.template.send_mail(self.partner.id, email_layout_xmlid=layout or False)
        )
        return mail.body_html

    def test_le_gabarit_passe_par_la_mise_en_page(self):
        corps = self._envoyer()
        self.assertIn(MARQUEUR, corps)
        # La marque vient de res.company, pas du gabarit.
        self.assertIn("#123456", corps)
        self.assertIn(self.company.name, corps)
        self.assertIn(f"/brand/logo/{self.company.id}", corps)

    def test_bluefox_branding_fait_foi_quand_il_est_installe(self):
        if not self.env.ref("bluefox_branding.bf_mail_layout", raise_if_not_found=False):
            self.skipTest("bluefox_branding absent : la copie de secours s'applique")
        # Installé, le relais remplace la copie : même rendu octet pour octet que sa
        # propre mise en page.
        self.assertEqual(self._envoyer(), self._envoyer("bluefox_branding.bf_mail_layout"))

    def test_la_copie_de_secours_rend_seule(self):
        relais = self.env.ref("bluefox_branding.bf_mail_layout_relais", raise_if_not_found=False)
        if relais:
            # Le relais coupé, c'est la copie qui rend, comme chez un locataire sans
            # bluefox_branding.
            relais.active = False
            self.env.registry.clear_cache("templates")
            self.addCleanup(self.env.registry.clear_cache, "templates")
        corps = self._envoyer()
        self.assertIn(MARQUEUR, corps)
        self.assertIn("#123456", corps)
        self.assertIn("#654321", corps)
        self.assertIn(f"/brand/logo/{self.company.id}", corps)
