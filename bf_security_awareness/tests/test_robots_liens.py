"""🔴 Un robot d'analyse de liens n'est pas une personne qui clique.

Les passerelles de courriel (Safe Links, Proofpoint, Mimecast…) et les aperçus
de liens ouvrent chaque adresse d'un message avant la personne, en GET ou en
HEAD. Chaque visite comptait comme un clic (état « clicked », formation
assignée, note au fil) et le lien « Signaler » comme un signalement. Une
campagne entière pouvait ainsi afficher 100 % de clics sans qu'un humain ait
rien fait, et le risque de chaque personne s'en trouvait faussé.
"""
from odoo.tests import HttpCase, tagged

NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) "
              "Gecko/20100101 Firefox/130.0")
ROBOTS = (
    "python-requests/2.31.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "HeadlessChrome/120.0.0.0 Safari/537.36",
    "Mozilla/4.0 (compatible; ms-office; MSOffice 16)",
    "Microsoft Office Existence Discovery",
    "Mozilla/5.0 (compatible; Barracuda Sentinel)",
    "",
)


@tagged("post_install", "-at_install")
class TestRobotsDeLiens(HttpCase):

    def setUp(self):
        super().setUp()
        template = self.env["bf.phishing.template"].create({
            "name": "Leurre robots", "subject": "Bonjour",
            "landing_mode": "awareness",
        })
        partner = self.env["res.partner"].create({
            "name": "Denise", "email": "denise@example.com"})
        self.campaign = self.env["bf.phishing.campaign"].create({
            "name": "Campagne robots", "template_id": template.id,
            "recipient_partner_ids": [(6, 0, partner.ids)],
        })
        self.campaign.action_prepare()
        self.campaign.state = "running"
        self.result = self.campaign.result_ids[0]

    def _get(self, chemin, agent):
        return self.url_open(chemin % self.result.token,
                             headers={"User-Agent": agent})

    def test_un_robot_ne_clique_pas(self):
        for agent in ROBOTS:
            with self.subTest(agent=agent):
                reponse = self._get("/phish/%s", agent)
                self.assertEqual(reponse.status_code, 200)
                self.result.invalidate_recordset()
                self.assertFalse(self.result.clicked_datetime,
                                 "clic enregistré pour « %s »" % agent)

    def test_une_requete_head_ne_clique_pas(self):
        self.opener.head(self.base_url() + "/phish/%s" % self.result.token,
                         headers={"User-Agent": NAVIGATEUR}, timeout=10)
        self.result.invalidate_recordset()
        self.assertFalse(self.result.clicked_datetime)

    def test_un_robot_ne_signale_pas(self):
        self._get("/phish/%s/report", ROBOTS[0])
        self.result.invalidate_recordset()
        self.assertFalse(self.result.reported)

    def test_un_navigateur_clique_et_signale(self):
        """Le témoin : une personne derrière un vrai navigateur compte."""
        self._get("/phish/%s", NAVIGATEUR)
        self._get("/phish/%s/report", NAVIGATEUR)
        self.result.invalidate_recordset()
        self.assertEqual(self.result.state, "clicked")
        self.assertTrue(self.result.clicked_datetime)
        self.assertTrue(self.result.reported)
