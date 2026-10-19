from lxml import etree

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

    def _open_benefit_screens(self, user, benefit):
        """Ce que le client web lit vraiment : les champs de la vue, AVEC données.

        `get_views` seul ne prouve rien : il ne calcule aucun champ. C'est la
        lecture (`web_search_read`, `web_read`) qui déclenche les calculs.
        """
        Benefit = self.env["bf.ex.benefit"].with_user(user)
        views = Benefit.get_views([(False, "list"), (False, "form")])["views"]
        shown = {}
        for vtype in ("list", "form"):
            arch = etree.fromstring(views[vtype]["arch"])
            names = {node.get("name") for node in arch.iter("field")}
            shown[vtype] = names
            spec = {
                name: {} for name in names
                if name in Benefit._fields and Benefit._fields[name].type not in ("one2many", "many2many")
            }
            if "rule_ids" in names:
                spec["rule_ids"] = {"fields": {"name": {}, "criteria_summary": {}}}
            self.env.invalidate_all()
            if vtype == "list":
                Benefit.web_search_read([("id", "=", benefit.id)], spec)
            else:
                Benefit.browse(benefit.id).web_read(spec)
        return shown

    def test_plain_employee_opens_the_catalogue_with_its_data(self):
        """La liste « Avantages » et la fiche levaient `AccessError`
        sur `bf.ex.eligibility.rule` pour tout le personnel ordinaire.

        Les compteurs du catalogue lisent les règles, que seule l'administration
        des avantages lit. Le menu, lui, est ouvert à tous : le catalogue doit
        s'ouvrir, sans ses compteurs.
        """
        benefit = self._benefit("Catalogue ouvert (essai)")
        self.env["bf.ex.eligibility.rule"].create({"name": "Tous", "benefit_id": benefit.id})
        me = self._employee("Employée ordinaire", user=self.plain)
        self.env["bf.ex.entitlement"].create({
            "employee_id": me.id, "benefit_id": benefit.id,
            "source": "manual", "reason": "Pour l'essai.",
        })
        shown = self._open_benefit_screens(self.plain, benefit)
        for name in ("entitled_count", "user_count", "uptake_rate", "annual_cost", "rule_ids"):
            with self.subTest(field=name):
                self.assertNotIn(name, shown["list"] | shown["form"],
                                 "un compteur du registre entier ne s'affiche pas hors de l'administration")
        self.env.invalidate_all()
        as_plain = benefit.with_user(self.plain)
        self.assertEqual(as_plain.entitled_count, 0,
                         "calculé sur ses seules lignes, le compteur serait faux : il vaut zéro")
        self.assertFalse(as_plain.unused)

    def test_officer_still_reads_the_counters(self):
        benefit = self._benefit("Catalogue compté (essai)")
        self.env["bf.ex.eligibility.rule"].create({"name": "Tous", "benefit_id": benefit.id})
        emp = self._employee("Ayant droit")
        self.env["bf.ex.entitlement"].create({
            "employee_id": emp.id, "benefit_id": benefit.id,
            "source": "manual", "reason": "Pour l'essai.",
        })
        shown = self._open_benefit_screens(self.officer, benefit)
        self.assertIn("uptake_rate", shown["list"])
        self.assertIn("rule_ids", shown["form"])
        self.env.invalidate_all()
        as_officer = benefit.with_user(self.officer)
        self.assertEqual(as_officer.entitled_count, 1)
        self.assertEqual(as_officer.rule_count, 1)
        self.assertTrue(as_officer.unused)

    def test_no_percentage_widget_on_a_0_100_rate(self):
        """La liste des avantages affichait « 8 820 % » d'adhésion.

        Tous les taux du module (`uptake_rate`, `turnover_rate`) valent déjà
        0 à 100, comme l'agrégat du pont vie privée et la tuile du tableau de
        bord qui les relisent. Le widget `percentage` attend une fraction de 0
        à 1 et la multiplie par cent : il n'a rien à faire dans ces vues tant
        qu'aucun champ ne porte de fraction.
        """
        data = self.env["ir.model.data"].search([
            ("module", "=", "bf_employee_experience"), ("model", "=", "ir.ui.view"),
        ])
        hits = [
            (view.xml_id, node.get("name"))
            for view in self.env["ir.ui.view"].browse(data.mapped("res_id"))
            if view.type != "qweb"
            for node in etree.fromstring(view.arch.encode()).iter("field")
            if node.get("widget") == "percentage"
        ]
        self.assertFalse(hits, "widget « percentage » sur un taux 0 à 100 : %s" % hits)

    def test_uptake_rate_reads_on_a_0_100_scale(self):
        """L'échelle est une interface : la tuile et l'agrégat la relisent."""
        benefit = self._benefit("Échelle (essai)")
        users = [self._employee("Échelle %s" % i) for i in range(4)]
        for emp in users:
            self.env["bf.ex.entitlement"].create({
                "employee_id": emp.id, "benefit_id": benefit.id,
                "source": "manual", "reason": "Pour l'essai.",
            })
        for emp in users[:3]:
            self.env["bf.ex.usage"].create({
                "employee_id": emp.id, "benefit_id": benefit.id, "date": self.today,
            }).action_confirm()
        benefit.invalidate_recordset()
        self.assertAlmostEqual(benefit.uptake_rate, 75.0)

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
