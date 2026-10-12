"""The printed mileage log: the business trips of the finished route days, grouped by worker
and vehicle, with totals.

For each trip, what Revenu Québec and the CRA ask a log to show: date, where from, where to
(the customers visited are the purpose), kilometres and the odometer. ⚠️ It is not the whole
register of an automobile the employer provides (days it was available, personal kilometres,
odometer at the start and the end of the year): the README says what to add.
"""
from itertools import groupby

from odoo import api, models


class ReportMileageLog(models.AbstractModel):
    _name = "report.bf_route.report_mileage_log"
    _description = "Mileage log"

    @api.model
    def _get_report_values(self, docids, data=None):
        # ⚠️ The reader's rights on the days, then the labels in sudo: a worker reads their own
        # days, but not the fleet (no Fleet group) nor a route they replace someone on.
        Day = self.env["bf.route.day"]
        days = Day.browse(docids)
        days.check_access("read")
        Day = Day.sudo()
        days = days.sudo().filtered(lambda d: d.state == "done").sorted(
            lambda d: (d.user_id.name or "", d.vehicle_id.display_name or "", d.date, d.id))
        groups = []
        for (user, vehicle), members in groupby(days, key=lambda d: (d.user_id, d.vehicle_id)):
            members = Day.concat(*members)
            groups.append({
                "user": user,
                "vehicle": vehicle,
                "days": members,
                "total": sum(members.mapped("distance_actual_km")),
                "first": members[0].date,
                "last": members[-1].date,
            })
        return {"doc_ids": days.ids, "doc_model": "bf.route.day", "docs": days, "groups": groups}
