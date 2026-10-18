"""🔴 Odoo ne doit pas visiter lui-même ses propres liens piégés.

À l'envoi, mass_mailing raccourcit chaque lien du courriel rendu par
destinataire, et `link.tracker.create()` va chercher la page pour en lire le
titre (aperçu de liens d'Odoo : agent Firefox 91, en-tête `Odoo-Link-Preview`).
Le serveur ouvrait ainsi chaque `/phish/<jeton>` : un faux clic, une formation
assignée et un faux signalement possibles pour chaque destinataire. En essai,
ce GET vers le serveur lui-même restait bloqué et chaque essai HTTP suivant
attendait 10 s.

Deux défenses, prouvées ici chacune avec son témoin :
1. le contrôleur traite l'en-tête `Odoo-Link-Preview` comme un robot ;
2. `link.tracker` donne un titre fixe à un lien piégé de cette base, sans
   requête réseau.
"""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from .test_phishing_flow import PhishingFlowCommon

# Le chemin réseau de `link.tracker._get_title_from_url`.
RECUPERATION = "odoo.addons.mail.tools.link_preview.get_link_preview_from_url"

# Les en-têtes exacts de l'aperçu de liens d'Odoo.
FIREFOX_91 = "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0"
APERCU_ODOO = {"User-Agent": FIREFOX_91, "Odoo-Link-Preview": "True"}


@tagged("post_install", "-at_install")
class TestTitreDesLiensPieges(PhishingFlowCommon):

    def setUp(self):
        super().setUp()
        self.campaign.action_prepare()
        self.result = self.campaign.result_ids[0]

    def test_un_lien_piege_n_est_pas_recupere(self):
        with patch(RECUPERATION, return_value=False) as recuperation:
            lien = self.env["link.tracker"].create({
                "url": self.result.landing_url})
            signaler = self.env["link.tracker"].create({
                "url": self.result.report_url})
        recuperation.assert_not_called()
        self.assertTrue(lien.title)
        self.assertNotIn(self.result.token, lien.title)
        self.assertEqual(signaler.title, lien.title)

    def test_un_lien_etranger_est_recupere(self):
        """Le témoin : hors de nos liens piégés, Odoo cherche le titre comme avant,
        même pour un chemin `/phish/` sur un autre hôte."""
        with patch(RECUPERATION, return_value=False) as recuperation:
            self.env["link.tracker"].create([
                {"url": "https://example.com/x"},
                {"url": "https://example.com/phish/%s" % self.result.token},
            ])
        self.assertEqual(recuperation.call_count, 2)

    def test_l_envoi_ne_recupere_aucun_lien_piege(self):
        """Le vrai chemin : lancer la campagne raccourcit le lien piégé sans
        jamais aller le chercher."""
        with patch(RECUPERATION, return_value=False) as recuperation:
            self.campaign.action_launch()
        # D'autres liens du courriel (pas les nôtres) peuvent être récupérés.
        recuperes = [appel.args[0] for appel in recuperation.call_args_list]
        self.assertFalse([url for url in recuperes if "/phish/" in url])
        self.assertEqual(self.result.state, "sent")
        lien = self.env["link.tracker"].search(
            [("url", "=", self.result.landing_url)])
        self.assertTrue(lien, "le lien piégé n'a pas été raccourci à l'envoi")
        self.assertNotEqual(lien[0].title, self.result.landing_url)


@tagged("post_install", "-at_install")
class TestApercuOdooNeCliquePas(HttpCase):

    def setUp(self):
        super().setUp()
        channel = self.env["slide.channel"].create({
            "name": "Cours aperçu", "channel_type": "training",
            "enroll": "invite", "visibility": "connected",
        })
        template = self.env["bf.phishing.template"].create({
            "name": "Leurre aperçu", "subject": "Bonjour",
            "landing_mode": "awareness",
            "training_channel_id": channel.id,
        })
        partner = self.env["res.partner"].create({
            "name": "Hélène", "email": "helene@example.com"})
        self.campaign = self.env["bf.phishing.campaign"].create({
            "name": "Campagne aperçu", "template_id": template.id,
            "recipient_partner_ids": [(6, 0, partner.ids)],
        })
        self.campaign.action_prepare()
        # 🔴 Sans ce garde-fou, une régression de la défense 2 ferait faire à
        # l'envoi un GET vers ce serveur, bloqué sur le verrou du registre.
        with patch(RECUPERATION, return_value=False):
            self.campaign.action_launch()
        self.result = self.campaign.result_ids[0]
        self.assertEqual(self.result.state, "sent")

    def _get(self, chemin, en_tetes):
        reponse = self.url_open(chemin % self.result.token, headers=en_tetes)
        self.assertEqual(reponse.status_code, 200)
        self.result.invalidate_recordset()
        return reponse

    def test_l_apercu_d_odoo_ne_clique_pas(self):
        nb_messages = len(self.result.message_ids)
        self._get("/phish/%s", APERCU_ODOO)
        self.assertFalse(self.result.clicked_datetime)
        self.assertEqual(self.result.state, "sent")
        self.assertFalse(self.result.training_assignment_id)
        self.assertEqual(len(self.result.message_ids), nb_messages)
        # Le témoin : le même agent, sans l'en-tête d'Odoo, compte.
        self._get("/phish/%s", {"User-Agent": FIREFOX_91})
        self.assertTrue(self.result.clicked_datetime)
        self.assertEqual(self.result.state, "clicked")
        self.assertTrue(self.result.training_assignment_id)

    def test_l_apercu_d_odoo_ne_signale_pas(self):
        self._get("/phish/%s/report", APERCU_ODOO)
        self.assertFalse(self.result.reported)
        self._get("/phish/%s/report", {"User-Agent": FIREFOX_91})
        self.assertTrue(self.result.reported)

    def test_l_apercu_d_odoo_n_ouvre_pas(self):
        self._get("/phish/%s/open.png", APERCU_ODOO)
        self.assertFalse(self.result.opened_datetime)
        self._get("/phish/%s/open.png", {"User-Agent": FIREFOX_91})
        self.assertTrue(self.result.opened_datetime)
