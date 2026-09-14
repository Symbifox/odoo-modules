# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Les courriels d'hébergement portent la marque de la société, et se lisent.

Relevé sur une vraie alerte « lent » reçue : « 5130 ms » en jaune #ffc107 sur
blanc (1,63:1), l'URL dans un bleu écrit en dur, le logo de la société 1 quelle
que soit la société, « Gestion d'hébergement » et deux liens vers « # » en pied,
et un expéditeur sans nom. La mise en page de marque (`bf_mail_layout` de
`bluefox_branding`) fait foi : couleurs, police, logo et pied viennent de
`res.company`.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..models import hosting_email_template as gabarit


def _luminance(hexa):
    h = hexa.lstrip("#")
    canaux = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    canaux = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in canaux]
    return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]


def _contraste(a, b):
    haut, bas = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (haut + 0.05) / (bas + 0.05)


@tagged("post_install", "-at_install")
class TestCourrielMarque(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.company
        # Des teintes qu'aucun gabarit n'a en dur : leur présence prouve la lecture.
        cls.societe.write({
            "name": "Hébergeur Essai", "email": "alertes@exemple.test",
            "phone": "555-555-0100", "website": "https://exemple.test",
            "report_brand_primary": "#1B6E8A", "report_brand_dark": "#3A2E4F",
            "brand_email_tagline": "Slogan d'essai 4417",
            "brand_privacy_url": "https://exemple.test/confidentialite",
            "brand_terms_url": False, "font": "Open_Sans",
        })

    # -- la palette d'états -------------------------------------------------
    def test_chaque_etat_se_lit_en_texte_et_en_en_tete(self):
        for nom, c in gabarit.ETATS.items():
            self.assertGreaterEqual(_contraste(c["texte"], "#FFFFFF"), 4.5, f"{nom} : texte sur blanc")
            self.assertGreaterEqual(_contraste(c["texte"], c["teinte"]), 4.5, f"{nom} : texte sur sa teinte")
            self.assertGreaterEqual(_contraste(c["entete_texte"], c["entete"]), 4.5, f"{nom} : en-tête")

    def test_la_pastille_lente_n_ecrit_pas_en_jaune_d_accent(self):
        pastille = gabarit.get_status_badge("slow", company=self.societe)
        self.assertNotIn("#ffc107", pastille.lower())
        self.assertIn(gabarit.ETATS["avertissement"]["texte"], pastille)

    # -- l'habillage de marque ----------------------------------------------
    def _enveloppe(self, alert_type=None):
        return gabarit.get_email_wrapper("Titre d'essai", "<p>contenu</p>", alert_type, company=self.societe)

    def test_l_enveloppe_lit_les_couleurs_de_la_societe(self):
        corps = self._enveloppe()
        self.assertIn("#3A2E4F", corps, "le bandeau et le filet du bas en foncé de marque")
        self.assertIn("#1B6E8A", corps, "l'accent et le filet du bas en accent de marque")
        for en_dur in ("#29abe2", "#22303b"):
            self.assertNotIn(en_dur, corps.lower(), f"{en_dur} écrit en dur")

    def test_l_enveloppe_porte_le_logo_et_le_pied_de_la_societe(self):
        corps = self._enveloppe()
        self.assertIn(f"/brand/logo/{self.societe.id}", corps)
        self.assertNotIn("/web/image/res.company/1/logo", corps)
        self.assertNotIn("Gestion d'hébergement", corps)
        self.assertNotIn('href="#"', corps, "aucun lien mort")
        self.assertIn("Hébergeur Essai", corps)
        self.assertIn("Slogan d&#39;essai 4417", corps)
        self.assertIn("https://exemple.test/confidentialite", corps)
        self.assertNotIn(">Conditions<", corps, "pas de lien de conditions sans adresse")

    def test_l_enveloppe_suit_la_police_de_la_societe(self):
        corps = self._enveloppe()
        self.assertIn("'Open Sans'", corps)
        self.assertNotIn("Lexend", corps)

    def test_le_bouton_prend_l_accent_de_marque_brut(self):
        """⚠️ L'accent BRUT sous du blanc : arbitrage du propriétaire de la marque."""
        self.assertIn("#1B6E8A", gabarit.get_button("Voir", "https://exemple.test", company=self.societe))

    # -- l'alerte réelle ----------------------------------------------------
    def test_l_alerte_lente_se_lit_et_part_sous_le_nom_de_la_societe(self):
        self.env["ir.config_parameter"].sudo().set_param("hosting.health_alert_email", "destinataire.alertes@exemple.test")
        partenaire = self.env["res.partner"].create({"name": "Client essai marque"})
        logiciel = self.env["hosting.software"].create(
            {"name": "Logiciel essai marque", "code": "TMARQ", "software_type": "self_hosted"})
        service = self.env["hosting.service"].create({
            "name": "Service essai marque", "partner_id": partenaire.id, "software_id": logiciel.id,
            "server_url": "https://service.exemple.test"})
        dernier = self.env["mail.mail"].search([], order="id desc", limit=1).id or 0
        # ⚠️ `patch.object`, pas une affectation sur la classe : remettre `send` à la
        # main le pose sur la classe du registre, et Odoo refuse l'attribut ajouté.
        with patch.object(type(self.env["mail.mail"]), "send", lambda self_, *a, **k: None):
            service._send_health_alert_email(
                [{"service": service, "response_time_ms": 5130, "threshold_ms": 5000}], "slow")
        courriel = self.env["mail.mail"].search([("id", ">", dernier)], limit=1)
        self.assertTrue(courriel)
        corps = courriel.body_html or ""
        self.assertRegex(corps, r"color:#8A5A00; font-weight:600;'>5130 ms")
        self.assertNotRegex(corps.lower(), r"color:#ffc107[^>]*>\s*5130")
        self.assertRegex(corps, r'href="https://service\.exemple\.test" style="color:#1B6E8A')
        self.assertIn("Hébergeur Essai", courriel.email_from)
        self.assertIn("<alertes@exemple.test>", courriel.email_from)
