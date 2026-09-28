from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestJetonCampagneClose(HttpCase):
    """Une campagne close n'enregistre plus rien par ses jetons."""

    def setUp(self):
        super().setUp()
        self.template = self.env["bf.phishing.template"].create({
            "name": "Leurre clos",
            "subject": "Bonjour",
            "landing_mode": "credential",
            "capture_field_lengths": True,
        })
        partner = self.env["res.partner"].create({
            "name": "Carole", "email": "carole@example.com"})
        self.campaign = self.env["bf.phishing.campaign"].create({
            "name": "Campagne close",
            "template_id": self.template.id,
            "recipient_partner_ids": [(6, 0, partner.ids)],
        })
        self.campaign.action_prepare()
        self.result = self.campaign.result_ids[0]

    def _visiter_tout(self):
        token = self.result.token
        nb_messages = len(self.result.message_ids)
        for url in ("/phish/%s", "/phish/%s/open.png",
                    "/phish/%s/report", "/phish/%s/lesson"):
            resp = self.url_open(url % token)
            self.assertEqual(resp.status_code, 200)
        self.result.invalidate_recordset()
        self.assertEqual(self.result.state, "pending")
        self.assertFalse(self.result.opened_datetime)
        self.assertFalse(self.result.clicked_datetime)
        self.assertFalse(self.result.reported)
        self.assertEqual(len(self.result.message_ids), nb_messages)

    def test_campagne_terminee(self):
        self.campaign.state = "done"
        self._visiter_tout()
        # La page d'atterrissage est la page neutre, pas le faux formulaire.
        resp = self.url_open("/phish/%s" % self.result.token)
        self.assertNotIn("password", resp.text)

    def test_campagne_annulee(self):
        self.campaign.state = "cancelled"
        self._visiter_tout()

    def test_leurre_archive(self):
        self.template.active = False
        self._visiter_tout()

    def test_campagne_ouverte_enregistre_encore(self):
        self.campaign.state = "running"
        self.url_open("/phish/%s" % self.result.token)
        self.result.invalidate_recordset()
        self.assertEqual(self.result.state, "clicked")


@tagged("post_install", "-at_install")
class TestPolitiqueContenuPagesPubliques(HttpCase):
    """Les pages publiques rendent le HTML brut des leurres : aucun script."""

    def setUp(self):
        super().setUp()
        self.template = self.env["bf.phishing.template"].create({
            "name": "Leurre CSP",
            "subject": "Bonjour",
            "landing_mode": "credential",
            "landing_html": "<p class='marque'>Marque</p>",
            "teachable_html": "<p class='lecon'>Leçon</p>",
        })
        partner = self.env["res.partner"].create({
            "name": "Denis", "email": "denis@example.com"})
        campaign = self.env["bf.phishing.campaign"].create({
            "name": "Campagne CSP",
            "template_id": self.template.id,
            "recipient_partner_ids": [(6, 0, partner.ids)],
        })
        campaign.action_prepare()
        self.token = campaign.result_ids[0].token

    def _verifier(self, url, attendu):
        resp = self.url_open(url)
        self.assertEqual(resp.status_code, 200)
        csp = resp.headers.get("Content-Security-Policy", "")
        for directive in ("script-src 'none'", "object-src 'none'",
                          "base-uri 'none'", "form-action 'self'"):
            self.assertIn(directive, csp, url)
        self.assertIn(attendu, resp.text, url)

    def test_pages_portent_la_politique(self):
        self._verifier("/phish/%s" % self.token, "marque")
        self._verifier("/phish/%s/lesson" % self.token, "lecon")
        self._verifier("/phish/%s/report" % self.token, "Bien jou")
        self._verifier("/phish/jeton-inconnu", "simulation")
