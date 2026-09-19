from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class UnionCase(TransactionCase):
    """Une unité où tout le monde cotise, mais où tout le monde n'a pas adhéré.

    C'est le décor qui compte : si les couverts et les membres coïncidaient,
    aucun essai de ce greffon ne prouverait quoi que ce soit.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["res.company"])
        cls.company = cls.env["res.company"].create({"name": "Employeur (essai)"})
        cls.env.user.company_ids |= cls.company

        cls.union = cls.env["bf.labour.union"].create({"name": "Syndicat d'essai"})
        cls.unit = cls.env["bf.labour.unit"].create({
            "name": "Unité d'essai",
            "company_id": cls.company.id,
            "union_id": cls.union.id,
            "state": "certified",
            "certificate_date": cls.today - relativedelta(years=7),
        })
        cls.agreement = cls.env["bf.labour.agreement"].create({
            "unit_id": cls.unit.id,
            "date_start": cls.today - relativedelta(years=1),
            "date_end": cls.today + relativedelta(years=2),
            "state": "in_force",
        })

    @classmethod
    def _membership(cls, name, covered=True, is_member=True, years=3):
        employee = cls.env["hr.employee"].create({
            "name": name, "company_id": cls.company.id,
        })
        return cls.env["bf.labour.membership"].create({
            "employee_id": employee.id,
            "unit_id": cls.unit.id,
            "date_start": cls.today - relativedelta(years=years),
            "seniority_date": cls.today - relativedelta(years=years),
            "covered": covered,
            "is_member": is_member,
        })
