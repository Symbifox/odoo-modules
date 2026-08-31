from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .common import ExCase


@tagged("post_install", "-at_install")
class TestAccess(ExCase):
    """⚠️ La moitié sensible du module.

    Savoir qui a pris quoi en dit long : aide aux employés, assurance, congé de
    maladie. La conception a tranché : le registre nominatif se lit par la
    personne concernée et par l'administration des avantages, pas par le
    gestionnaire direct ni par le reste de l'entreprise.

    Chaque règle qui masque porte ici une MUTATION : on remplace son domaine par
    `[(1, '=', 1)]` et on vérifie que la fuite apparaît. Sans cette contre-épreuve,
    un test vert ne dit pas si c'est bien la règle qui masque.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref("base.group_user")
        group_hr = cls.env.ref("hr.group_hr_user")

        cls.user_alice = cls.env["res.users"].create({
            "name": "Alice", "login": "alice_ex_test",
            "groups_id": [(6, 0, [group_user.id])],
        })
        cls.user_bob = cls.env["res.users"].create({
            "name": "Bob", "login": "bob_ex_test",
            "groups_id": [(6, 0, [group_user.id])],
        })
        cls.user_hr = cls.env["res.users"].create({
            "name": "Administration", "login": "hr_ex_test",
            "groups_id": [(6, 0, [group_user.id, group_hr.id])],
        })

        cls.alice = cls._employee("Alice", department=cls.dept_ti, user=cls.user_alice)
        cls.bob = cls._employee("Bob", department=cls.dept_ti, user=cls.user_bob)

        cls.benefit = cls._benefit("Programme d'aide aux employés", category="health")
        cls.usage_alice = cls.env["bf.ex.usage"].create({
            "employee_id": cls.alice.id, "benefit_id": cls.benefit.id,
            "date": cls.today, "amount": 120.0,
        })
        cls.usage_bob = cls.env["bf.ex.usage"].create({
            "employee_id": cls.bob.id, "benefit_id": cls.benefit.id,
            "date": cls.today, "amount": 340.0,
        })
        cls.right_bob = cls.env["bf.ex.entitlement"].create({
            "employee_id": cls.bob.id, "benefit_id": cls.benefit.id,
            "source": "manual", "reason": "Pour l'essai.",
        })

    def _visible_usage(self, user):
        """Ce que cette personne voit, cache vidé.

        ⚠️ Le cache ORM est par transaction : une lecture faite plus tôt en
        `sudo` réchaufferait le cache et rendrait ce parcours faux.
        """
        self.env.invalidate_all()
        return self.env["bf.ex.usage"].with_user(user).search([])

    def test_employee_sees_only_their_own_usage(self):
        visible = self._visible_usage(self.user_alice)
        self.assertIn(self.usage_alice, visible)
        self.assertNotIn(self.usage_bob, visible,
                         "le registre d'usage d'un collègue ne se lit pas")

    def test_mutation_proves_the_usage_rule_is_what_masks(self):
        rule = self.env.ref("bf_employee_experience.rule_usage_own")
        original = rule.domain_force
        try:
            rule.domain_force = "[(1, '=', 1)]"
            visible = self._visible_usage(self.user_alice)
            self.assertIn(
                self.usage_bob, visible,
                "domaine neutralisé : la ligne du collègue devrait apparaître. "
                "Si elle n'apparaît pas, ce n'est pas cette règle qui masque.",
            )
        finally:
            rule.domain_force = original
        self.assertNotIn(self.usage_bob, self._visible_usage(self.user_alice))

    def test_hr_officer_sees_everything(self):
        self.env.invalidate_all()
        visible = self.env["bf.ex.usage"].with_user(self.user_hr).search([])
        self.assertIn(self.usage_alice, visible)
        self.assertIn(self.usage_bob, visible)

    def test_employee_sees_only_their_own_entitlement(self):
        self.env.invalidate_all()
        visible = self.env["bf.ex.entitlement"].with_user(self.user_alice).search([])
        self.assertNotIn(self.right_bob, visible)

    def test_mutation_proves_the_entitlement_rule_is_what_masks(self):
        rule = self.env.ref("bf_employee_experience.rule_entitlement_own")
        original = rule.domain_force
        try:
            rule.domain_force = "[(1, '=', 1)]"
            self.env.invalidate_all()
            visible = self.env["bf.ex.entitlement"].with_user(self.user_alice).search([])
            self.assertIn(self.right_bob, visible)
        finally:
            rule.domain_force = original

    def test_catalogue_is_readable_by_everyone_but_not_writable(self):
        """Le catalogue complet se voit : c'est un argument de rétention.

        « Après un an, tu y auras droit » ne se dit pas si l'avantage est caché.
        """
        self.env.invalidate_all()
        as_alice = self.benefit.with_user(self.user_alice)
        self.assertTrue(as_alice.name)
        with self.assertRaises(AccessError):
            as_alice.write({"name": "Renommé par Alice"})

    def test_employee_cannot_touch_eligibility_rules(self):
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.env["bf.ex.eligibility.rule"].with_user(self.user_alice).search([])

    def test_employee_cannot_read_indicators(self):
        """Les indicateurs agrègent des départs et des salaires."""
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.env["bf.ex.indicator"].with_user(self.user_alice).search([])
