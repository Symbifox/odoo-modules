from odoo.tests.common import tagged

from odoo.addons.bf_employee_experience.models.starter_catalogue import STARTER

from .common import ExCase


@tagged("post_install", "-at_install")
class TestViews(ExCase):
    """Charger chaque vue sous un VRAI compte de chaque rôle.

    ⚠️ uid 1 n'a aucun groupe, et Odoo lui sert les vues sans dépouiller les
    attributs `groups`. Une vue qui casse pour un recruteur ou pour un employé
    ordinaire passe donc inaperçue si on ne la charge que en superutilisateur.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref("base.group_user")
        group_hr = cls.env.ref("hr.group_hr_user")
        group_hr_manager = cls.env.ref("hr.group_hr_manager")

        cls.plain = cls.env["res.users"].create({
            "name": "Employée ordinaire", "login": "plain_ex_views",
            "groups_id": [(6, 0, [group_user.id])],
        })
        cls.officer = cls.env["res.users"].create({
            "name": "Administration des avantages", "login": "officer_ex_views",
            "groups_id": [(6, 0, [group_user.id, group_hr.id])],
        })
        cls.manager = cls.env["res.users"].create({
            "name": "Direction RH", "login": "manager_ex_views",
            "groups_id": [(6, 0, [group_user.id, group_hr.id, group_hr_manager.id])],
        })

    def _load(self, user, model, view_types):
        views = [(False, vtype) for vtype in view_types]
        return self.env[model].with_user(user).get_views(views)

    def test_officer_loads_every_view(self):
        for model, types in (
            ("bf.ex.benefit", ["list", "form", "search"]),
            ("bf.ex.eligibility.rule", ["list", "form"]),
            ("bf.ex.entitlement", ["list", "form", "search"]),
            ("bf.ex.usage", ["list", "form", "search"]),
            ("bf.ex.claim", ["list", "form"]),
            ("bf.ex.indicator", ["list", "form"]),
        ):
            with self.subTest(model=model):
                result = self._load(self.officer, model, types)
                self.assertTrue(result["views"])

    def test_hr_manager_loads_every_view(self):
        for model in ("bf.ex.benefit", "bf.ex.entitlement", "bf.ex.usage",
                      "bf.ex.claim", "bf.ex.indicator"):
            with self.subTest(model=model):
                self.assertTrue(self._load(self.manager, model, ["list", "form"])["views"])

    def test_plain_employee_loads_what_they_may_see(self):
        """Le catalogue, ses propres droits, ses propres usages, ses demandes."""
        for model, types in (
            ("bf.ex.benefit", ["list", "form", "search"]),
            ("bf.ex.entitlement", ["list", "form", "search"]),
            ("bf.ex.usage", ["list", "form", "search"]),
            ("bf.ex.claim", ["list", "form"]),
        ):
            with self.subTest(model=model):
                self.assertTrue(self._load(self.plain, model, types)["views"])

    def test_employee_form_inherit_loads(self):
        """Le bouton greffé sur la fiche employé ne doit casser pour personne."""
        for user in (self.officer, self.manager):
            with self.subTest(user=user.name):
                self.assertTrue(self._load(user, "hr.employee", ["form"])["views"])

    def test_actions_point_at_loadable_views(self):
        module = "bf_employee_experience"
        for xmlid in ("action_benefit", "action_rule", "action_entitlement",
                      "action_usage", "action_claim", "action_indicator"):
            action = self.env.ref("%s.%s" % (module, xmlid))
            types = [t for t in action.view_mode.split(",")]
            with self.subTest(action=xmlid):
                result = self.env[action.res_model].with_user(self.officer).get_views(
                    [(False, t) for t in types]
                )
                self.assertTrue(result["views"])


@tagged("post_install", "-at_install")
class TestStarterAndReport(ExCase):
    """Le catalogue de départ et le relevé personnel."""

    def test_starter_catalogue_is_idempotent(self):
        """⚠️ Les données de démonstration appellent la MÊME méthode, donc le
        catalogue peut déjà être là. Le test porte sur le résultat, pas sur le
        nombre de créations."""
        Benefit = self.env["bf.ex.benefit"]
        Benefit._load_starter_catalogue()
        codes = set(Benefit.with_context(active_test=False).search([
            ("company_id", "=", self.company.id),
        ]).mapped("code"))
        expected = {row[0] for row in STARTER}
        self.assertEqual(expected - codes, set(),
                         "le catalogue de départ doit être complet")
        self.assertFalse(Benefit._load_starter_catalogue(),
                         "un second chargement n'ajoute rien")

    def test_starter_catalogue_does_not_overwrite(self):
        Benefit = self.env["bf.ex.benefit"]
        Benefit._load_starter_catalogue()
        assurance = Benefit.search([("code", "=", "ASSUR")], limit=1)
        assurance.cost_amount = 3100.0
        Benefit._load_starter_catalogue()
        self.assertAlmostEqual(assurance.cost_amount, 3100.0,
                               msg="un avantage déjà présent n'est pas écrasé")

    def test_starter_rules_use_portable_criteria_only(self):
        """Aucune règle du catalogue de départ ne peut dépendre d'un département
        ou d'un poste : ils n'existent pas forcément chez le client."""
        Benefit = self.env["bf.ex.benefit"]
        Benefit._load_starter_catalogue()
        rules = self.env["bf.ex.eligibility.rule"].search([
            ("benefit_id.code", "in", [row[0] for row in STARTER]),
        ])
        self.assertTrue(rules)
        for rule in rules:
            self.assertFalse(rule.department_ids)
            self.assertFalse(rule.job_ids)
            self.assertFalse(rule.work_location_ids)
            self.assertFalse(rule.manager_ids)

    def test_starter_catalogue_then_sync_opens_rights(self):
        """Le catalogue chargé doit produire des droits sur un vrai employé."""
        Benefit = self.env["bf.ex.benefit"]
        Benefit._load_starter_catalogue()
        emp = self._employee("Nouvelle recrue", months=18)
        opened, _closed = self.env["bf.ex.entitlement"]._sync_from_rules()
        mine = opened.filtered(lambda e: e.employee_id == emp)
        self.assertTrue(mine, "un employé de 18 mois doit ouvrir des droits")
        codes = set(mine.benefit_id.mapped("code"))
        self.assertIn("PAE", codes, "l'avantage sans condition doit s'ouvrir")
        self.assertIn("REER", codes, "12 mois d'ancienneté requis, la personne en a 18")

    def test_statement_report_renders(self):
        """⚠️ En test, _render_qweb_pdf rend du HTML et non un PDF. On vérifie
        que le gabarit se rend sans exception et cite bien les avantages."""
        Benefit = self.env["bf.ex.benefit"]
        Benefit._load_starter_catalogue()
        emp = self._employee("Relevée", months=24)
        self.env["bf.ex.entitlement"]._sync_from_rules()
        benefit = Benefit.search([("code", "=", "FORM")], limit=1)
        line = self.env["bf.ex.usage"].create({
            "employee_id": emp.id, "benefit_id": benefit.id,
            "date": self.today, "amount": 950.0,
        })
        line.action_confirm()

        report = self.env.ref("bf_employee_experience.action_report_statement")
        content, _kind = report._render_qweb_html(report.report_name, emp.ids)
        text = content.decode() if isinstance(content, bytes) else content
        self.assertIn("Relevé personnel des avantages", text)
        self.assertIn("Relevée", text)
        self.assertIn("950", text)
