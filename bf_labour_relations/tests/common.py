from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class LabourCase(TransactionCase):
    """Décor commun : deux sociétés, dont une seule est syndiquée.

    Le parc mixte est le cas normal, pas le cas limite. Le décor le reproduit
    pour que chaque essai puisse vérifier qu'une société sans unité continue de
    fonctionner.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["res.company"])

        cls.company_union = cls.env["res.company"].create({
            "name": "Établissement syndiqué (essai)",
        })
        cls.company_free = cls.env["res.company"].create({
            "name": "Établissement non syndiqué (essai)",
        })
        cls.env.user.company_ids |= (cls.company_union | cls.company_free)

        cls.union = cls.env["bf.labour.union"].create({
            "name": "Syndicat d'essai",
            "central": "Centrale d'essai",
            "local_number": "999",
        })
        cls.unit = cls.env["bf.labour.unit"].create({
            "name": "Unité d'essai",
            "company_id": cls.company_union.id,
            "union_id": cls.union.id,
            "state": "certified",
            "certificate_date": cls.today - relativedelta(years=5),
            "certificate_number": "AM-0001",
        })
        cls.agreement = cls.env["bf.labour.agreement"].create({
            "unit_id": cls.unit.id,
            "date_start": cls.today - relativedelta(years=2),
            "date_end": cls.today + relativedelta(years=1),
            "state": "in_force",
        })

    @classmethod
    def _employee(cls, name, company=None):
        return cls.env["hr.employee"].create({
            "name": name,
            "company_id": (company or cls.company_union).id,
        })

    @classmethod
    def _membership(cls, employee, **kw):
        vals = {
            "employee_id": employee.id,
            "unit_id": cls.unit.id,
            "date_start": cls.today - relativedelta(years=3),
            "seniority_date": cls.today - relativedelta(years=3),
        }
        vals.update(kw)
        return cls.env["bf.labour.membership"].create(vals)

    @classmethod
    def _grievance(cls, **kw):
        vals = {
            "unit_id": cls.unit.id,
            "subject": "Grief d'essai",
            "kind": "individual",
            "date_filed": cls.today,
        }
        vals.update(kw)
        return cls.env["bf.labour.grievance"].create(vals)
