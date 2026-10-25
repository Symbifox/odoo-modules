"""Les deux voies LinkedIn de l'assistant d'enrichissement.

Le pont est simulé : ce qui s'éprouve ici n'est ni la recherche ni RocketReach,
c'est ce que l'assistant en fait. Trois choses comptent — la charge envoyée au
pont (un crédit ne se dépense pas par inadvertance), la fusion des deux passes
quand la recherche est relayée par la lecture, et la discipline de remplissage
qui ne doit écraser aucun champ déjà renseigné.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

BRIDGE = "odoo.addons.bf_contact_enrichment.tools.bridge.call_bridge"
# Le dernier geste avant la socket : ce qui est au-dessus, dont l'estampille du
# locataire, tourne pour de vrai.
GATEWAY = "odoo.addons.bf_ai_bridge.tools.transport.post"
_CHARGE = 2  # position de payload dans transport.post(socket, endpoint, payload, …)

URL = "https://www.linkedin.com/in/maude-exemple"

SEARCH = {
    "linkedin_url": URL,
    "full_name": "Maude Fortier",
    "function": "Directrice des opérations",
    "company": "Ateliers Rivard",
    "city": "Montréal",
    "country": "Canada",
    "candidates": ["Maude Fortier - Analyste chez Autre Inc - "
                   "https://www.linkedin.com/in/maude-exemple-2"],
    "confidence": 88,
    "source": "websearch",
}

# Ce que rapporte la lecture payante en plus : le courriel et le téléphone,
# marqués professionnels par le pont. Le cellulaire et le lieu de la personne
# arrivent aussi, et ne doivent jamais entrer.
LOOKUP = {
    "linkedin_url": URL,
    "full_name": "Maude Fortier",
    "function": "Directrice générale",
    "company": "Ateliers Rivard",
    "email": "maude@ateliers-rivard.test",
    "email_type": "professional",
    "phone": "+1 514 555 0142",
    "phone_type": "professional",
    "mobile": "+1 514 555 0188",
    "city": "Laval",
    "country": "Canada",
    "street": "12 rue Privée",
    "zip": "H7A 1A1",
    "confidence": 90,
    "source": "rocketreach",
}


@tagged("post_install", "-at_install")
class TestLinkedInEnrich(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create({
            "name": "Maude Fortier",
            "company_name": "Ateliers Rivard",
            "function": "Coordonnatrice",  # déjà renseigné : ne doit pas bouger
        })

    def _wizard(self, **vals):
        return self.env["bf.contact.enrich.wizard"].create(
            dict({"partner_id": self.partner.id, "source": "linkedin"}, **vals))

    def test_recherche_remplit_url_et_homonymes(self):
        """La voie gratuite part avec ce qu'on sait déjà et rapporte l'URL."""
        wizard = self._wizard(linkedin_mode="search")
        with patch(BRIDGE, return_value={"data": SEARCH}) as call:
            wizard.action_analyze()
        endpoint, payload = call.call_args[0][1], call.call_args[0][2]
        self.assertEqual(endpoint, "/enrich/linkedin")
        self.assertEqual(payload["mode"], "search")
        self.assertEqual(payload["name"], "Maude Fortier")
        self.assertEqual(payload["company"], "Ateliers Rivard")
        self.assertNotIn("linkedin_url", payload)  # rien à dépenser ici

        self.assertEqual(wizard.state, "review")
        self.assertEqual(wizard.proposed_linkedin_url, URL)
        self.assertIn("maude-exemple-2", wizard.linkedin_candidates)

    def test_appliquer_enregistre_url_sans_ecraser(self):
        """L'URL se pose sur la fiche ; la fonction déjà remplie survit."""
        wizard = self._wizard(linkedin_mode="search")
        with patch(BRIDGE, return_value={"data": SEARCH}):
            wizard.action_analyze()
        wizard.action_apply()
        self.assertEqual(self.partner.x_linkedin_url, URL)
        self.assertEqual(self.partner.function, "Coordonnatrice")

    def test_lecture_relaie_la_recherche_et_fusionne(self):
        """Le relais envoie l'URL trouvée, et garde ce que la recherche savait."""
        wizard = self._wizard(linkedin_mode="search")
        with patch(BRIDGE, return_value={"data": SEARCH}):
            wizard.action_analyze()
        with patch(BRIDGE, return_value={"data": LOOKUP}) as call:
            wizard.action_linkedin_lookup()
        payload = call.call_args[0][2]
        self.assertEqual(payload["mode"], "url")
        self.assertEqual(payload["linkedin_url"], URL)

        self.assertEqual(wizard.proposed_email, "maude@ateliers-rivard.test")
        self.assertEqual(wizard.proposed_phone, "+1 514 555 0142")
        self.assertEqual(wizard.proposed_function, "Directrice générale")
        self.assertEqual(wizard.confidence, 90)

    def test_mode_url_sans_url_refuse(self):
        """Pas d'URL, pas d'appel : un crédit ne se dépense pas à vide."""
        wizard = self._wizard(linkedin_mode="url")
        with patch(BRIDGE) as call:
            with self.assertRaises(UserError):
                wizard.action_analyze()
        call.assert_not_called()

    def test_url_connue_preselectionne_la_lecture(self):
        """Une fiche qui porte déjà une URL ouvre l'assistant sur la lecture."""
        self.partner.x_linkedin_url = URL
        wizard = self.env["bf.contact.enrich.wizard"].with_context(
            default_partner_id=self.partner.id,
            default_source="linkedin",
        ).create({})
        self.assertEqual(wizard.linkedin_mode, "url")
        self.assertEqual(wizard.linkedin_url_input, URL)

    def test_confiance_en_texte_ne_fait_pas_tomber_l_assistant(self):
        """Une confiance rendue en texte se range dans l'entier sans casser."""
        wizard = self._wizard(linkedin_mode="search")
        with patch(BRIDGE, return_value={"data": dict(SEARCH, confidence="88")}):
            wizard.action_analyze()
        self.assertEqual(wizard.confidence, 88)

    def test_erreur_du_pont_remonte(self):
        """Une erreur du pont s'affiche, elle ne passe pas pour un résultat vide."""
        wizard = self._wizard(linkedin_mode="search")
        with patch(BRIDGE, return_value={"error": "LinkedIn search timed out"}):
            with self.assertRaises(UserError):
                wizard.action_analyze()
        self.assertEqual(wizard.state, "draft")

    def test_la_recherche_part_sur_le_locataire_de_cette_base(self):
        """🔴 Le locataire est estampé, jamais écrit au site d'appel.

        La lignée 1.5 portait "org": "bf" dans les cinq appels : sur un autre
        locataire, la recherche partait au nom de Blue Fox. Le contrôle porte sur
        la charge qui quitterait la base, pas sur la réponse, qui serait juste.
        """
        self.env["ir.config_parameter"].sudo().set_param("bf_ai_bridge.tenant", "banc")
        wizard = self._wizard(linkedin_mode="search")
        with patch(GATEWAY, return_value={"data": SEARCH}) as envoi:
            wizard.action_analyze()
        charge = envoi.call_args[0][_CHARGE]
        self.assertEqual(charge["org"], "banc")
        self.assertEqual(charge["mode"], "search")



@tagged("post_install", "-at_install")
class TestLinkedInProfessionnelSeulement(TransactionCase):
    """🔴 d'un tiers, rien que des coordonnées professionnelles.

    LinkedIn et RocketReach sont des tiers. L'art. 1 al. 5 de la Loi sur le
    privé (Québec) sort des sections II et III les seules coordonnées du lieu
    de travail. Tout le reste (cellulaire, courriel personnel, ville, pays,
    adresse) ne doit pas même être rangé dans l'assistant.
    """

    PERSO = ("proposed_mobile", "proposed_city", "proposed_country",
             "proposed_street", "proposed_zip")

    def setUp(self):
        super().setUp()
        self.partner = self.env["res.partner"].create({"name": "Maude Fortier"})

    def _analyse(self, data, mode="url"):
        wizard = self.env["bf.contact.enrich.wizard"].create({
            "partner_id": self.partner.id, "source": "linkedin",
            "linkedin_mode": mode, "linkedin_url_input": URL,
        })
        with patch(BRIDGE, return_value={"data": data}):
            wizard.action_analyze()
        return wizard

    def test_lecture_n_range_rien_de_personnel(self):
        wizard = self._analyse(LOOKUP)
        for champ in self.PERSO:
            self.assertFalse(wizard[champ], champ)
        self.assertEqual(wizard.proposed_email, "maude@ateliers-rivard.test")

    def test_appliquer_n_ecrit_rien_de_personnel(self):
        wizard = self._analyse(LOOKUP)
        wizard.action_apply()
        self.assertFalse(self.partner.mobile)
        self.assertFalse(self.partner.city)
        self.assertFalse(self.partner.country_id)
        self.assertFalse(self.partner.street)
        self.assertFalse(self.partner.zip)
        self.assertEqual(self.partner.email, "maude@ateliers-rivard.test")
        self.assertEqual(self.partner.phone, "+1 514 555 0142")

    def test_courriel_personnel_ecarte(self):
        data = dict(LOOKUP, email="maude.f@gmail.test", email_type="personal")
        self.assertFalse(self._analyse(data).proposed_email)

    def test_pont_qui_ne_marque_rien_ne_fait_rien_passer(self):
        """Un pont d'avant le marquage : ni courriel ni téléphone."""
        data = {k: v for k, v in LOOKUP.items()
                if k not in ("email_type", "phone_type")}
        wizard = self._analyse(data)
        self.assertFalse(wizard.proposed_email)
        self.assertFalse(wizard.proposed_phone)
        self.assertEqual(wizard.proposed_function, "Directrice générale")

    def test_telephone_mobile_marque_ecarte(self):
        data = dict(LOOKUP, phone_type="mobile")
        self.assertFalse(self._analyse(data).proposed_phone)

    def test_recherche_ne_range_pas_le_lieu(self):
        wizard = self._analyse(SEARCH, mode="search")
        self.assertFalse(wizard.proposed_city)
        self.assertFalse(wizard.proposed_country)
        self.assertEqual(wizard.proposed_linkedin_url, URL)
        self.assertEqual(wizard.proposed_company, "Ateliers Rivard")

    def test_marque_professionnelle_ignoree_en_recherche(self):
        """🔴 En recherche, le JSON vient d'un modèle qui lit le web : une
        marque « professional » n'y prouve rien (relecture adverse, M1)."""
        data = dict(SEARCH, email="maude.f@ateliers-rivard.test",
                    email_type="professional", phone="+1 514 555 0188",
                    phone_type="professional")
        wizard = self._analyse(data, mode="search")
        self.assertFalse(wizard.proposed_email)
        self.assertFalse(wizard.proposed_phone)

    def test_courriel_generique_ecarte_meme_marque(self):
        data = dict(LOOKUP, email="maude.exemple@gmail.com")
        self.assertFalse(self._analyse(data).proposed_email)
