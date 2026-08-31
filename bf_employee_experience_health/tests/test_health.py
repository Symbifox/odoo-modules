from odoo.exceptions import AccessError, UserError
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

    # ---------------- le libre-service ----------------

    def _run_my_allergies(self, user):
        """Déclencher l'action de menu comme le ferait le client web."""
        action = self.env.ref("bf_employee_experience_health.action_my_allergy")
        return action.with_user(user).run()

    def test_a_plain_employee_sees_the_self_service_menu(self):
        """🔴 Le pont vie privée pose `requires_express_opt_in` et écrit que
        déclarer une allergie est volontaire. Avant ce menu, la personne avait
        tous les droits ORM sur sa déclaration et AUCUNE surface pour les
        exercer : les RH saisissaient à sa place."""
        menus = self.env["ir.ui.menu"].with_user(self.user_a).load_menus(False)
        noms = {v["name"] for v in menus.values()
                if isinstance(v, dict) and v.get("name")}
        self.assertIn("Mes allergies", noms)
        self.assertNotIn("Santé et sécurité", noms,
                         "les écrans de l'administration restent hors de vue")

    def test_the_self_service_action_is_scoped_to_the_caller(self):
        action = self._run_my_allergies(self.user_a)
        self.assertEqual(action["res_model"], "bf.ex.allergy")
        self.assertEqual(action["domain"], [("employee_id", "=", self.emp_a.id)])
        self.assertEqual(action["context"]["default_employee_id"], self.emp_a.id)

    def test_two_people_get_two_different_scopes(self):
        """La contre-épreuve du libre-service : le même menu ne rend pas le
        même périmètre à deux personnes."""
        a = self._run_my_allergies(self.user_a)
        b = self._run_my_allergies(self.user_b)
        self.assertNotEqual(a["domain"], b["domain"])
        self.assertEqual(b["domain"], [("employee_id", "=", self.emp_b.id)])

    def test_a_user_without_an_employee_record_gets_a_clear_message(self):
        orphan = self.env["res.users"].create({
            "name": "Sans fiche", "login": "sansfiche_health_test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        with self.assertRaises(UserError):
            self._run_my_allergies(orphan)

    def test_the_self_service_form_does_not_carry_the_employee_field(self):
        """Sur sa propre déclaration, le champ « Employé » est du bruit : il
        vaut toujours soi. Le défaut du contexte le remplit."""
        form = self.env.ref("bf_employee_experience_health.view_my_allergy_form")
        self.assertNotIn('name="employee_id"', form.arch)

    def test_the_anaphylaxis_flag_stays_out_of_reach_of_the_staff(self):
        """⚠️ Contre-épreuve d'une tentation : ouvrir `ex_allergy_ids` au
        personnel n'exposerait aucune allergie, la règle d'accès vidant la liste
        chez un collègue. Mais `ex_has_anaphylaxis` calculerait alors « non »
        sur tout le monde, et un drapeau de sécurité qui répond « non » faute de
        droit de lecture est pire qu'un drapeau absent."""
        champs = self.env["hr.employee"].with_user(self.user_a).fields_get(
            allfields=["ex_allergy_ids", "ex_has_anaphylaxis"])
        self.assertFalse(champs, "les deux champs restent réservés à l'administration")
