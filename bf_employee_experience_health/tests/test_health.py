from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestHealth(TransactionCase):
    """⚠️ Une allergie est un renseignement de santé. Les contre-épreuves de ce
    fichier sont la seule preuve que la règle d'accès masque vraiment."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        group_user = cls.env.ref("base.group_user")
        group_hr = cls.env.ref("hr.group_hr_user")

        cls.user_a = cls.env["res.users"].create({
            "name": "Aline", "login": "aline_health_test",
            "groups_id": [(6, 0, [group_user.id])],
        })
        cls.user_b = cls.env["res.users"].create({
            "name": "Bruno", "login": "bruno_health_test",
            "groups_id": [(6, 0, [group_user.id])],
        })
        cls.user_hr = cls.env["res.users"].create({
            "name": "RH", "login": "rh_health_test",
            "groups_id": [(6, 0, [group_user.id, group_hr.id])],
        })
        cls.emp_a = cls.env["hr.employee"].create({
            "name": "Aline", "company_id": cls.company.id, "user_id": cls.user_a.id,
        })
        cls.emp_b = cls.env["hr.employee"].create({
            "name": "Bruno", "company_id": cls.company.id, "user_id": cls.user_b.id,
        })
        cls.peanut = cls.env.ref("bf_employee_experience_health.allergen_peanut")
        cls.latex = cls.env.ref("bf_employee_experience_health.allergen_latex")

        cls.allergy_a = cls.env["bf.ex.allergy"].create({
            "employee_id": cls.emp_a.id, "allergen_id": cls.peanut.id,
            "severity": "anaphylaxis",
        })
        cls.allergy_b = cls.env["bf.ex.allergy"].create({
            "employee_id": cls.emp_b.id, "allergen_id": cls.latex.id,
            "severity": "severe",
        })

    def _visible(self, user):
        self.env.invalidate_all()
        return self.env["bf.ex.allergy"].with_user(user).search([])

    def test_canadian_priority_allergens_are_loaded(self):
        allergens = self.env["bf.ex.allergen"].search([])
        names = set(allergens.mapped("name"))
        for expected in ("Arachides", "Noix", "Lait", "Œufs", "Sulfites", "Moutarde"):
            self.assertIn(expected, names)

    def test_colleague_allergy_is_not_readable(self):
        visible = self._visible(self.user_a)
        self.assertIn(self.allergy_a, visible)
        self.assertNotIn(self.allergy_b, visible,
                         "l'allergie d'un collègue est un renseignement de santé")

    def test_mutation_proves_the_rule_is_what_masks(self):
        rule = self.env.ref("bf_employee_experience_health.rule_allergy_own")
        original = rule.domain_force
        try:
            rule.domain_force = "[(1, '=', 1)]"
            self.assertIn(self.allergy_b, self._visible(self.user_a),
                          "domaine neutralisé : l'allergie du collègue devrait apparaître")
        finally:
            rule.domain_force = original
        self.assertNotIn(self.allergy_b, self._visible(self.user_a))

    def test_hr_sees_everything(self):
        visible = self._visible(self.user_hr)
        self.assertIn(self.allergy_a, visible)
        self.assertIn(self.allergy_b, visible)

    def test_catering_list_carries_no_names(self):
        """C'est ce qu'on transmet à un traiteur. Faire circuler la liste
        nominative revient à diffuser un dossier de santé."""
        self.env["bf.ex.allergy"].create({
            "employee_id": self.emp_b.id, "allergen_id": self.peanut.id,
            "severity": "mild",
        })
        rows = self.env["bf.ex.allergy"].catering_constraints(company=self.company)
        blob = str(rows)
        self.assertNotIn("Aline", blob)
        self.assertNotIn("Bruno", blob)
        peanut_row = next(r for r in rows if r["allergen"] == "Arachides")
        self.assertEqual(peanut_row["people"], 2)
        self.assertEqual(peanut_row["severity"], "anaphylaxis",
                         "la gravité retenue est la plus élevée du groupe")

    def test_catering_list_excludes_non_food(self):
        rows = self.env["bf.ex.allergy"].catering_constraints(company=self.company)
        self.assertNotIn("Latex", [r["allergen"] for r in rows])

    def test_catering_list_is_refused_to_a_plain_employee(self):
        """`catering_constraints` est publique, donc appelable par RPC. Les deux
        épreuves au-dessus vérifiaient la FORME du résultat, jamais QUI a le
        droit de le demander."""
        with self.assertRaises(AccessError):
            self.env["bf.ex.allergy"].with_user(self.user_b).catering_constraints(
                company=self.company)

    def test_catering_list_cannot_be_narrowed_to_one_colleague(self):
        """La fuite : un groupe d'une personne n'est pas anonyme, c'est un nom.
        Bruno visait Aline seule et récupérait « Arachides, 1 personne,
        Anaphylaxie » — la déclaration que la règle d'accès lui interdit de
        lire."""
        with self.assertRaises(AccessError):
            self.env["bf.ex.allergy"].with_user(self.user_b).catering_constraints(
                employee_ids=[self.emp_a.id])

    def test_catering_action_is_refused_to_a_plain_employee(self):
        """L'action du menu passe par la même méthode ; un menu caché n'est pas
        une barrière, l'action serveur reste déclenchable."""
        with self.assertRaises(AccessError):
            self.env["bf.ex.allergy"].with_user(self.user_b).action_catering_list()

    def test_hr_still_gets_the_catering_list(self):
        rows = self.env["bf.ex.allergy"].with_user(self.user_hr).catering_constraints(
            company=self.company)
        self.assertIn("Arachides", [r["allergen"] for r in rows])
        self.assertNotIn("Aline", str(rows))

    def test_no_duplicate_declaration(self):
        from psycopg2 import IntegrityError
        with self.assertRaises(IntegrityError):
            with self.cr.savepoint():
                self.env["bf.ex.allergy"].create({
                    "employee_id": self.emp_a.id, "allergen_id": self.peanut.id,
                    "severity": "mild",
                })

    def test_anaphylaxis_flag_on_the_employee(self):
        self.assertTrue(self.emp_a.ex_has_anaphylaxis)
        self.assertFalse(self.emp_b.ex_has_anaphylaxis)

    def test_plain_user_cannot_write_the_allergen_catalogue(self):
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.peanut.with_user(self.user_a).write({"name": "Renommé"})
