"""Ce que le module écrit se lit dans la langue de l'usager.

La liste de service composait son message hors traduction, avec les libellés de
gravité de la source ; l'action « Mes allergies » écrivait son nom et son refus
dans le code d'une action serveur, qu'aucun catalogue n'atteint.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLangueDesAllergies(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Lang = cls.env["res.lang"]
        Lang._activate_lang("fr_CA")
        Lang._activate_lang("en_US")
        user = cls.env["res.users"].create({
            "name": "Langue allergies", "login": "langue_allergies_test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        employee = cls.env["hr.employee"].create({
            "name": "Langue allergies", "company_id": cls.env.company.id, "user_id": user.id,
        })
        cls.env["bf.ex.allergy"].create({
            "employee_id": employee.id,
            "allergen_id": cls.env.ref("bf_employee_experience_health.allergen_peanut").id,
            "severity": "anaphylaxis",
        })
        cls.sans_fiche = cls.env["res.users"].create({
            "name": "Sans fiche", "login": "sans_fiche_allergies_test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def _liste(self, lang):
        action = self.env["bf.ex.allergy"].with_context(lang=lang).action_catering_list()
        return action["params"]["message"]

    def test_la_liste_de_service_suit_la_langue(self):
        francais = self._liste("fr_CA")
        anglais = self._liste("en_US")
        self.assertIn("personne(s)", francais)
        self.assertIn("person(s)", anglais)
        gravite = dict(self.env["bf.ex.allergy"]._fields["severity"]._description_selection(
            self.env["bf.ex.allergy"].with_context(lang="fr_CA").env))["anaphylaxis"]
        self.assertIn(gravite, francais)
        self.assertNotEqual(francais, anglais)

    def test_le_refus_sans_fiche_suit_la_langue(self):
        for lang, attendu in (("fr_CA", "Aucune fiche employé"), ("en_US", "No employee record")):
            usager = self.sans_fiche.with_context(lang=lang)
            with self.assertRaises(UserError) as refus:
                self.env["bf.ex.allergy"].with_user(usager).with_context(lang=lang) \
                    .action_open_my_allergies()
            self.assertIn(attendu, str(refus.exception))
