from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class ExCase(TransactionCase):
    """Décor commun : une société, deux départements, quelques personnes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hr.employee"])
        cls.company = cls.env.company

        cls.dept_ti = cls.env["hr.department"].create({
            "name": "TI (essai)", "company_id": cls.company.id,
        })
        cls.dept_admin = cls.env["hr.department"].create({
            "name": "Administration (essai)", "company_id": cls.company.id,
        })
        cls.calendar_full = cls.env["resource.calendar"].create({
            "name": "Temps plein (essai)", "company_id": cls.company.id,
        })
        cls.calendar_part = cls.env["resource.calendar"].create({
            "name": "Temps partiel (essai)", "company_id": cls.company.id,
        })

    @classmethod
    def _employee(cls, name, department=None, months=None, employee_type="employee",
                  calendar=None, departure=None, user=None):
        """Créer un employé, avec un contrat s'il faut une ancienneté.

        `months` est l'ancienneté voulue. Sans contrat, `first_contract_date`
        reste vide, ce qui est exactement le cas d'une personne dont on ne
        connaît pas la date d'entrée.
        """
        employee = cls.env["hr.employee"].create({
            "name": name,
            "company_id": cls.company.id,
            "department_id": department.id if department else False,
            "employee_type": employee_type,
            "resource_calendar_id": (calendar or cls.calendar_full).id,
            "departure_date": departure or False,
            "user_id": user.id if user else False,
        })
        if months is not None:
            cls.env["hr.contract"].create({
                "name": "Contrat %s" % name,
                "employee_id": employee.id,
                "date_start": cls.today - relativedelta(months=months),
                "wage": 5000.0,
                "state": "open",
                "company_id": cls.company.id,
            })
            employee.invalidate_recordset(["first_contract_date"])
        return employee

    @classmethod
    def _benefit(cls, name, **kw):
        vals = {
            "name": name,
            "company_id": cls.company.id,
            "category": "wellness",
            "cost_model": "per_employee_year",
            "cost_amount": 100.0,
        }
        vals.update(kw)
        return cls.env["bf.ex.benefit"].create(vals)
