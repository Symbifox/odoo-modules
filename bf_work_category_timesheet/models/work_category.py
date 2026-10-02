from odoo import fields, models


class AccountAnalyticLine(models.Model):
    _name = "account.analytic.line"
    _inherit = ["account.analytic.line", "bf.work.category.mixin"]

    _bf_work_category_sources = (("record", "task_id"), ("record", "project_id"))


class TimesheetsAnalysisReport(models.Model):
    # Its search view inherits the timesheet one, category filter and grouping
    # included: the report carries the line's category so they work there too.
    _inherit = "timesheets.analysis.report"

    work_category_id = fields.Many2one("project.tags", string="Work category", readonly=True)

    def _select(self):
        return super()._select() + ", A.work_category_id AS work_category_id"
