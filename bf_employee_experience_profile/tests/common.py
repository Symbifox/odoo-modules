from odoo import fields
from odoo.tests.common import TransactionCase


class ProfileCase(TransactionCase):
    """Décor commun : une société, trois référentiels livrés, des personnes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hr.employee"])
        cls.company = cls.env.company
        cls.behaviour4 = cls.env.ref(
            "bf_employee_experience_profile.framework_behaviour4")
        cls.types16 = cls.env.ref(
            "bf_employee_experience_profile.framework_types16")
        cls.bigfive = cls.env.ref(
            "bf_employee_experience_profile.framework_bigfive")
        cls.style_d = cls.env.ref("bf_employee_experience_profile.type_behaviour4_d")
        cls.style_s = cls.env.ref("bf_employee_experience_profile.type_behaviour4_s")
        cls.type_istj = cls.env.ref("bf_employee_experience_profile.type_types16_istj")

    @classmethod
    def _employee(cls, name, user=None, manager=None, departure=None):
        return cls.env["hr.employee"].create({
            "name": name,
            "company_id": cls.company.id,
            "user_id": user.id if user else False,
            "parent_id": manager.id if manager else False,
            "departure_date": departure or False,
        })

    @classmethod
    def _user(cls, name, login, groups=("base.group_user",)):
        return cls.env["res.users"].create({
            "name": name, "login": login,
            "groups_id": [(6, 0, [cls.env.ref(g).id for g in groups])],
        })

    def _profile(self, employee, framework=None, **kw):
        vals = {
            "employee_id": employee.id,
            "framework_id": (framework or self.types16).id,
        }
        if vals["framework_id"] == self.types16.id and "type_id" not in kw:
            vals["type_id"] = self.type_istj.id
        vals.update(kw)
        return self.env["bf.ex.profile"].create(vals)

    def _scored(self, employee, framework, scores):
        """Une déclaration chiffrée : {type: valeur}."""
        return self.env["bf.ex.profile"].create({
            "employee_id": employee.id,
            "framework_id": framework.id,
            "score_ids": [
                (0, 0, {"type_id": item.id, "value": value})
                for item, value in scores.items()
            ],
        })
