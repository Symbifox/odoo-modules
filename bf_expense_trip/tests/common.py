# -*- coding: utf-8 -*-
from datetime import date

from odoo import Command
from odoo.tests import new_test_user

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


class TripCommon(TestExpenseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.Trip = cls.env["bf.expense.trip"]
        cls.Rate = cls.env["bf.expense.allowance.rate"]
        cls.allowance = cls.env.ref("bf_expense_trip.product_travel_allowance")
        cls.taxe = cls.env["account.tax"].create({
            "name": "Achat 14,975 %", "amount_type": "percent", "amount": 14.975,
            "type_tax_use": "purchase", "company_id": cls.company.id,
        })
        cls.hotel = cls.env["product.product"].create({
            "name": "Hébergement", "type": "service", "can_be_expensed": True,
            "standard_price": 0.0, "supplier_taxes_id": [Command.set(cls.taxe.ids)],
        })
        cls.parking = cls.env["product.product"].create({
            "name": "Stationnement", "type": "service", "can_be_expensed": True,
            "standard_price": 0.0, "supplier_taxes_id": [Command.set(cls.taxe.ids)],
        })
        cls.lodging_tax = cls.env["product.product"].create({
            "name": "Taxe sur l'hébergement", "type": "service", "can_be_expensed": True,
            "standard_price": 0.0,
        })
        # The boss travels; the assistant prepares; a stranger sees nothing.
        cls.boss_user = new_test_user(cls.env, login="bf_boss", groups="base.group_user",
                                      company_ids=[Command.set(cls.env.companies.ids)])
        cls.boss = cls.env["hr.employee"].create({
            "name": "Patronne", "user_id": cls.boss_user.id, "company_id": cls.company.id,
            "work_contact_id": cls.boss_user.partner_id.id,
        })
        cls.assistant_user = new_test_user(cls.env, login="bf_assistant", groups="base.group_user",
                                           company_ids=[Command.set(cls.env.companies.ids)])
        cls.assistant = cls.env["hr.employee"].create({
            "name": "Adjointe", "user_id": cls.assistant_user.id, "company_id": cls.company.id,
            "work_contact_id": cls.assistant_user.partner_id.id,
        })
        cls.boss.bf_trip_assistant_user_ids = [Command.set(cls.assistant_user.ids)]
        cls.stranger_user = new_test_user(cls.env, login="bf_stranger", groups="base.group_user",
                                          company_ids=[Command.set(cls.env.companies.ids)])
        cls.env["hr.employee"].create({
            "name": "Quelqu'un", "user_id": cls.stranger_user.id, "company_id": cls.company.id})
        cls.start = date(2026, 11, 2)
        cls.end = date(2026, 11, 4)

    def _trip(self, employee=None, start=None, end=None, user=None, **vals):
        Trip = self.Trip.with_user(user) if user else self.Trip
        return Trip.create({
            "name": "Salon à Toronto",
            "employee_id": (employee or self.boss).id,
            "company_id": self.company.id,
            "date_start": start or self.start,
            "date_end": end or self.end,
            **vals,
        })

    def _expense(self, name="Taxi", amount=40.0, day=None, employee=None, user=None, **vals):
        Expense = self.env["hr.expense"]
        if user:
            Expense = Expense.with_user(user)
        employee = employee or self.boss
        return Expense.with_context(default_employee_id=employee.id).create({
            "name": name,
            "employee_id": employee.id,
            "product_id": self.parking.id,
            "total_amount_currency": amount,
            "date": day or self.start,
            "company_id": self.company.id,
            **vals,
        })

    def _rates(self, **amounts):
        values = {"breakfast": 10.0, "lunch": 20.0, "dinner": 30.0, "incidentals": 5.0,
                  "private_lodging": 50.0}
        values.update(amounts)
        return self.Rate.create([{
            "company_id": self.company.id, "kind": kind, "amount": amount,
            "date_from": date(2026, 1, 1), "product_id": self.allowance.id,
        } for kind, amount in values.items()])
