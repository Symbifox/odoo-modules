import base64
import csv
import io

from odoo import _, fields, models
from odoo.exceptions import AccessError


def cell(value):
    """🔴 A parent types their own address on the portal: a cell starting with = + - or @ would
    run as a formula when the office opens the export in a spreadsheet."""
    value = "" if value is None or value is False else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


class SchoolRl24Compute(models.TransientModel):
    _name = "bf.school.rl24.compute"
    _description = "Compute the child-care statements of a year"

    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    year = fields.Integer(
        "Calendar year", required=True,
        # The slips are due by the end of February for the year before.
        default=lambda s: fields.Date.context_today(s).year - 1)

    def _check_company(self):
        if self.company_id not in self.env.user.company_ids:
            raise AccessError(_("You do not work for %s.", self.company_id.name))

    def action_compute(self):
        self.ensure_one()
        self._check_company()
        self.env["bf.school.rl24"].check_access("write")
        statements = self.env["bf.school.rl24"]._school_compute(self.company_id, self.year)
        return {
            "type": "ir.actions.act_window", "res_model": "bf.school.rl24",
            "name": _("Relevé 24, %s", self.year), "view_mode": "list,form",
            "domain": [("id", "in", statements.ids)],
        }

    def action_export(self):
        """One line per payer and child, for the software that produces the official slips."""
        self.ensure_one()
        self._check_company()
        statements = self.env["bf.school.rl24"].search([
            ("company_id", "=", self.company_id.id), ("year", "=", self.year), ("state", "=", "issued")])
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([_("Year"), _("Payer"), _("Street"), _("City"), _("Province"), _("Postal code"),
                         _("Child"), _("Child's birth date"), _("Child-care fees paid")])
        for s in statements:
            payer = s.partner_id
            writer.writerow([cell(v) for v in (
                s.year, payer.name, ", ".join(filter(None, [payer.street, payer.street2])),
                payer.city, payer.state_id.code, payer.zip,
                s.student_id.name, s.student_id.student_birthdate, "%.2f" % s.amount)])
        attachment = self.env["ir.attachment"].create({
            "name": "releve24-%s-%s.csv" % (self.year, self.company_id.id),
            "datas": base64.b64encode(buffer.getvalue().encode("utf-8-sig")),
            "mimetype": "text/csv", "res_model": self._name, "res_id": self.id,
        })
        return {"type": "ir.actions.act_url", "target": "self",
                "url": "/web/content/%s?download=true" % attachment.id}
