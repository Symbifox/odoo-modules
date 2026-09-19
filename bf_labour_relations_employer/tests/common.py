from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class EmployerCase(TransactionCase):
    """Une unité, une convention, sa règle de cotisation, et du monde dedans."""

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
            "certificate_date": cls.today - relativedelta(years=6),
        })
        cls.agreement = cls.env["bf.labour.agreement"].create({
            "unit_id": cls.unit.id,
            "date_start": cls.today - relativedelta(years=2),
            "date_end": cls.today + relativedelta(years=1),
            "state": "in_force",
        })
        cls.rule = cls.env["bf.labour.dues.rule"].create({
            "agreement_id": cls.agreement.id,
            "date_start": cls.today - relativedelta(years=2),
            "rate_percent": 2.0,
            "amount_fixed": 1.0,
            "basis": "gross",
            "period": "pay",
        })

    @classmethod
    def _member(cls, name, years, covered=True, is_member=True):
        employee = cls.env["hr.employee"].create({
            "name": name, "company_id": cls.company.id,
        })
        cls.env["bf.labour.membership"].create({
            "employee_id": employee.id,
            "unit_id": cls.unit.id,
            "date_start": cls.today - relativedelta(years=years),
            "seniority_date": cls.today - relativedelta(years=years),
            "covered": covered,
            "is_member": is_member,
        })
        return employee

    @classmethod
    def _posted_list(cls):
        record = cls.env["bf.labour.seniority.list"].create({
            "unit_id": cls.unit.id,
            "reference_date": cls.today,
        })
        record.action_build()
        record.action_post()
        return record
