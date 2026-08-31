from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDigestSection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hr.employee"])
        cls.company = cls.env.company
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Digest d'essai", "company_id": cls.company.id,
        })
        cls.employee = cls.env["hr.employee"].create({
            "name": "Sujet", "company_id": cls.company.id,
        })

    def _benefit(self, name, **kw):
        vals = {"name": name, "company_id": self.company.id, "category": "wellness",
                "cost_model": "per_employee_year", "cost_amount": 100.0}
        vals.update(kw)
        return self.env["bf.ex.benefit"].create(vals)

    def _right(self, benefit):
        return self.env["bf.ex.entitlement"].create({
            "employee_id": self.employee.id, "benefit_id": benefit.id,
            "source": "manual", "reason": "Pour l'essai.",
        })

    def test_quiet_day_stays_quiet(self):
        """Rien à faire, aucune section. Un digest qui parle tous les jours
        pour ne rien dire finit par ne plus être lu."""
        self.assertEqual(self.config._render_ex_section(self.env.user), "")

    def test_toggle_silences_the_section(self):
        benefit = self._benefit("Gym")
        self._right(benefit)
        self.assertIn("Avantages", self.config._render_ex_section(self.env.user))
        self.config.include_employee_experience = False
        self.assertEqual(self.config._render_ex_section(self.env.user), "")

    def test_unused_benefit_shows_up(self):
        benefit = self._benefit("Massage")
        self._right(benefit)
        html = self.config._render_ex_section(self.env.user)
        self.assertIn("Massage", html)
        self.assertIn("depuis un an", html)

    def test_pending_claim_shows_up(self):
        benefit = self._benefit("Formation", approval_required=True, cost_model="per_use")
        self._right(benefit)
        claim = self.env["bf.ex.claim"].create({
            "employee_id": self.employee.id, "benefit_id": benefit.id,
        })
        claim.action_submit()
        html = self.config._render_ex_section(self.env.user)
        self.assertIn("attente", html)
        self.assertIn("Sujet", html)

    def test_usage_without_right_shows_up(self):
        """Soit une erreur de saisie, soit une règle à revoir. Quelqu'un doit voir."""
        benefit = self._benefit("Physio", cost_model="per_use")
        line = self.env["bf.ex.usage"].create({
            "employee_id": self.employee.id, "benefit_id": benefit.id, "date": self.today,
        })
        line.action_confirm()
        html = self.config._render_ex_section(self.env.user)
        self.assertIn("sans droit ouvert", html)

    def test_section_is_spliced_inside_its_own_row(self):
        """⚠️ Le marqueur « Divider » est ENTRE deux <tr>. Une section nue y
        serait sortie de la table par l'analyseur HTML et flotterait au-dessus
        de la carte. Elle doit porter son propre <tr>."""
        envelope = "<table><tr><td>haut</td></tr><!-- Divider --><tr><td>bas</td></tr></table>"
        out = self.config._splice_ex_section(envelope, "<h3>Avantages</h3>")
        index = out.index("<h3>Avantages</h3>")
        self.assertIn("<tr><td", out[:index][-120:],
                      "la section doit être enveloppée dans sa propre ligne")
        self.assertIn("</td></tr><!-- Divider -->", out)

    def test_splice_is_a_no_op_without_marker_or_section(self):
        self.assertEqual(self.config._splice_ex_section("<p>rien</p>", ""), "<p>rien</p>")
        self.assertEqual(
            self.config._splice_ex_section("<p>sans marqueur</p>", "<h3>x</h3>"),
            "<p>sans marqueur</p>",
        )
