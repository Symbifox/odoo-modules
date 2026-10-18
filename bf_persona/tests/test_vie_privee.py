"""Qui lit le persona, et ce qui part vers Gen.

Ce que ces tests protègent :

* le résumé persona recopié sur la fiche contact (`persona_summary`), qu'un
  client du portail lisait sur sa propre fiche, comme tout interne sans le rôle ;
* la cote de payeur, réservée au gestionnaire persona comme les détails
  personnels, et refusée à l'utilisateur persona simple ;
* le bloc envoyé à Gen, qui ne porte plus la cote de payeur ;
* le KPI de délai de paiement tiré de la comptabilité, la même cote sous un
  autre nom, et la cote posée à la création par qui ne peut pas la lire.
"""

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import PersonaCase


@tagged("post_install", "-at_install")
class TestViePrivee(PersonaCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.persona = cls.Persona.create({
            "partner_id": cls.eli.id,
            "payer_quality": "poor",
            "payer_notes": "Paie en retard",
            "tone_summary": "tense",
        })
        cls.portail = new_test_user(
            cls.env, login="ada-portail-essai", groups="base.group_portal",
            partner_id=cls.eli.id,
        )
        cls.interne = new_test_user(
            cls.env, login="bob-interne-essai", groups="base.group_user",
        )
        cls.utilisateur = new_test_user(
            cls.env, login="clara-persona-essai",
            groups="base.group_user,bf_persona.group_persona_user",
        )
        cls.gestionnaire = new_test_user(
            cls.env, login="dora-gestion-essai",
            groups="base.group_user,bf_persona.group_persona_manager",
        )

    def test_resume_de_la_fiche_refuse_sans_le_role(self):
        for user in (self.portail, self.interne):
            # Témoin : la fiche contact elle-même leur est lisible.
            self.eli.with_user(user).read(["name"])
            for champ in ("persona_summary", "persona_id", "has_persona"):
                with self.subTest(user=user.login, champ=champ), self.assertRaises(AccessError) as refus:
                    self.eli.with_user(user).read([champ])
                self.assertIn(champ, str(refus.exception))

    def test_fiche_contact_s_ouvre_sans_le_role(self):
        vue = self.Partner.with_user(self.interne).get_views([(False, "form")])
        # La fiche ne demande aucun des champs réservés : elle s'ouvre comme avant.
        reserves = {"persona_summary", "persona_id", "has_persona"}
        self.assertFalse(reserves & set(vue["models"]["res.partner"]["fields"]))
        for champ in reserves:
            self.assertNotIn(f'name="{champ}"', vue["views"]["form"]["arch"])
        # Témoin : avec le rôle, la fiche demande ceux qu'elle affiche.
        vue = self.Partner.with_user(self.utilisateur).get_views([(False, "form")])
        self.assertTrue({"persona_summary", "has_persona"} <= set(vue["models"]["res.partner"]["fields"]))

    def test_resume_de_la_fiche_lu_avec_le_role(self):
        lu = self.eli.with_user(self.utilisateur).read(["persona_summary"])[0]
        self.assertIn("Persona", lu["persona_summary"])

    def test_cote_de_payeur_au_gestionnaire_seul(self):
        for champ in ("payer_quality", "payer_notes", "avg_payment_delay_days"):
            with self.subTest(champ=champ), self.assertRaises(AccessError) as refus:
                self.persona.with_user(self.utilisateur).read([champ])
            # Le refus vient du champ, pas de l'accès au modèle.
            self.assertIn(champ, str(refus.exception))
        lu = self.persona.with_user(self.gestionnaire).read(["payer_quality", "payer_notes"])[0]
        self.assertEqual(lu["payer_quality"], "poor")
        self.assertEqual(lu["payer_notes"], "Paie en retard")

    def test_recherche_par_payeur_refusee_a_l_utilisateur(self):
        with self.assertRaises(AccessError):
            self.Persona.with_user(self.utilisateur).search([("payer_quality", "=", "poor")])
        trouves = self.Persona.with_user(self.gestionnaire).search([("payer_quality", "=", "poor")])
        self.assertIn(self.persona, trouves)

    def test_vues_s_ouvrent_pour_l_utilisateur_simple(self):
        # Un champ réservé laissé dans une vue sans son groupe empêcherait
        # l'utilisateur persona d'ouvrir la liste, la fiche ou le tableau de bord.
        Persona = self.Persona.with_user(self.utilisateur)
        vues = Persona.get_views([(False, "form"), (False, "list"), (False, "kanban"), (False, "search")])
        for genre, vue in vues["views"].items():
            with self.subTest(vue=genre):
                self.assertNotIn("payer_quality", vue["arch"])
                self.assertNotIn("avg_payment_delay_days", vue["arch"])
        champs = [n for n in vues["models"]["contact.persona"]["fields"]]
        self.persona.with_user(self.utilisateur).read(champs)

    def test_vues_gardent_le_payeur_pour_le_gestionnaire(self):
        vues = self.Persona.with_user(self.gestionnaire).get_views([(False, "form"), (False, "kanban")])
        for genre, vue in vues["views"].items():
            with self.subTest(vue=genre):
                self.assertIn("payer_quality", vue["arch"])

    def test_gen_ne_recoit_pas_la_cote_de_payeur(self):
        resume = self.persona.claude_context_summary
        self.assertNotIn("Payeur", resume)
        self.assertNotIn("mauvais", resume.lower())
        # Témoin : le reste du bloc part toujours.
        self.assertIn("ton: tendu", resume.lower())

    def test_kpi_de_la_comptabilite_au_gestionnaire_seul(self):
        Kpi = self.env["contact.persona.kpi"]
        compta = Kpi.create({"persona_id": self.persona.id, "name": "Délai paiement moyen",
                             "value_float": 45.0, "source": "account"})
        manuel = Kpi.create({"persona_id": self.persona.id, "name": "Heures consommées",
                             "value_float": 12.0, "source": "manual"})
        vus = Kpi.with_user(self.utilisateur).search([("persona_id", "=", self.persona.id)])
        self.assertEqual(vus, manuel)
        lu = self.persona.with_user(self.utilisateur).read(["kpi_ids"])[0]["kpi_ids"]
        self.assertEqual(lu, manuel.ids)
        vus = Kpi.with_user(self.gestionnaire).search([("persona_id", "=", self.persona.id)])
        self.assertEqual(vus, compta | manuel)
        # Ni créer ni fabriquer, par écriture, un indicateur « comptabilité ».
        for geste in (
            lambda: manuel.with_user(self.utilisateur).write({"source": "account"}),
            lambda: Kpi.with_user(self.utilisateur).create(
                {"persona_id": self.persona.id, "name": "x", "source": "account"}),
            lambda: Kpi.with_user(self.utilisateur).with_context(default_source="account").create(
                {"persona_id": self.persona.id, "name": "x"}),
        ):
            with self.assertRaises(AccessError):
                geste()
        self.assertEqual(manuel.source, "manual")
        manuel.with_user(self.utilisateur).write({"value_float": 13.0})
        manuel.with_user(self.gestionnaire).write({"source": "account"})
        self.assertEqual(manuel.source, "account")

    def test_cote_posee_a_la_creation_reservee(self):
        Persona = self.Persona.with_user(self.utilisateur)
        with self.assertRaises(AccessError):
            Persona.create({"partner_id": self.jul.id, "payer_quality": "poor"})
        with self.assertRaises(AccessError):
            Persona.with_context(default_payer_quality="poor").create({"partner_id": self.jul.id})
        # Sans la cote, l'utilisateur persona crée toujours sa fiche.
        Persona.create({"partner_id": self.jul.id, "tone_summary": "warm"})
        self.Persona.with_user(self.gestionnaire).create(
            {"partner_id": self.hm.id, "payer_quality": "good"})
