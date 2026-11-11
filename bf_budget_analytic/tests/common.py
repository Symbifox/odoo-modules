from odoo import Command, fields

from odoo.addons.bf_budget.tests.common import BfBudgetCommon


class BfBudgetAnalyticCommon(BfBudgetCommon):
    """Le décor du socle, plus la main-d'œuvre : un projet, deux personnes.

    ⚠️ Les parcours de droits se jouent sous des comptes RÉELS, jamais sous
    uid 1 (qui n'a aucun groupe et voit tout), et jamais après une lecture en
    sudo dans la même transaction sans invalider le cache : le cache de l'ORM
    est par transaction, un affichage en sudo fausserait tout parcours par
    rôle (plan, §11).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        budget_user = cls.env.ref("bf_budget.group_bf_budget_user")
        timesheet_user = cls.env.ref("hr_timesheet.group_hr_timesheet_user")

        # Anne et Bob tiennent des feuilles de temps ; Anne lit aussi le budget.
        cls.user_anne = Users.create(
            {
                "name": "Anne — budget et feuilles de temps",
                "login": "anne.budget@essai.invalid",
                "company_id": cls.company.id,
                "company_ids": [Command.set([cls.company.id])],
                "groups_id": [
                    Command.set(
                        [
                            cls.env.ref("base.group_user").id,
                            budget_user.id,
                            timesheet_user.id,
                        ]
                    )
                ],
            }
        )
        cls.user_bob = Users.create(
            {
                "name": "Bob — feuilles de temps",
                "login": "bob.budget@essai.invalid",
                "company_id": cls.company.id,
                "company_ids": [Command.set([cls.company.id])],
                "groups_id": [
                    Command.set(
                        [cls.env.ref("base.group_user").id, timesheet_user.id]
                    )
                ],
            }
        )
        # Un lecteur de budget SANS aucun droit analytique : l'ACL de lecture
        # d'`account.analytic.line` vient des groupes feuilles de temps, compta
        # analytique ou achats — un interne nu ne l'a pas.
        cls.user_bare = Users.create(
            {
                "name": "Lecteur de budget sans droits analytiques",
                "login": "lecteur.budget@essai.invalid",
                "company_id": cls.company.id,
                "company_ids": [Command.set([cls.company.id])],
                "groups_id": [
                    Command.set([cls.env.ref("base.group_user").id, budget_user.id])
                ],
            }
        )
        cls.employee_anne = cls.env["hr.employee"].create(
            {
                "name": "Anne",
                "user_id": cls.user_anne.id,
                "company_id": cls.company.id,
                "hourly_cost": 40.0,
            }
        )
        cls.employee_bob = cls.env["hr.employee"].create(
            {
                "name": "Bob",
                "user_id": cls.user_bob.id,
                "company_id": cls.company.id,
                "hourly_cost": 60.0,
            }
        )

        # Le compte analytique du projet vit dans le plan que le PROJET utilise
        # (sa colonne n'est pas forcément celle du plan du décor : elle se
        # demande au compte, jamais écrite en dur).
        cls.project = cls.env["project.project"].create(
            {
                "name": "Chantier budget",
                "company_id": cls.company.id,
                "allow_timesheets": True,
                "privacy_visibility": "employees",
            }
        )
        if not cls.project.account_id:
            cls.project._create_analytic_account()
        cls.analytic_work = cls.project.account_id

    @classmethod
    def _timesheet(cls, employee, hours, date=None):
        """Une feuille de temps : Odoo la valorise au taux horaire de l'employé."""
        return cls.env["account.analytic.line"].create(
            {
                "name": "Travail",
                "project_id": cls.project.id,
                "employee_id": employee.id,
                "unit_amount": hours,
                "date": date or fields.Date.context_today(cls.env.user),
                "company_id": cls.company.id,
            }
        )

    @classmethod
    def _labour_line(cls, planned=1000.0):
        """Un budget d'une ligne de main-d'œuvre sur le compte du projet."""
        budget = cls.env["bf.budget"].create(
            {
                "name": "Main-d'œuvre",
                "company_id": cls.company.id,
                "date_start": cls.date_start,
                "date_end": cls.date_end,
            }
        )
        line = cls.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "source": "internal_cost",
                "analytic_account_ids": [Command.set(cls.analytic_work.ids)],
                "amount_planned": planned,
            }
        )
        return budget, line
