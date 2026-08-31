"""An `.ics` report type, so a mail template can carry a per-event invitation.

Attaching the `.ics` to the composer through the context (`default_attachment_ids`)
does not survive: `mail.compose.message._compute_attachment_ids` depends on
`template_id` and resets attachments whenever the template changes — including
right after the composer opens. The supported way to hand the composer a file
computed from the record is `mail.template.report_template_ids`, which is
rendered per record both when the composer opens and again at sending time.

Reports only know how to emit PDF, HTML and text, so this adds an `ics` type.
It renders nothing through QWeb: an iCalendar stream has its own folding and
CRLF rules, and core already produces a correct one with vobject in
`calendar.event._get_ics_file()`. We just hand that back with the right
extension, which is what gives the attachment its `text/calendar` mimetype and
makes mail clients offer "add to calendar".
"""

from odoo import _, fields, models
from odoo.exceptions import UserError


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    report_type = fields.Selection(
        selection_add=[("qweb-ics", "iCalendar (.ics)")],
        ondelete={"qweb-ics": "cascade"},
    )

    def _render_qweb_ics(self, report_ref, res_ids, data=None):
        """Return (ics bytes, "ics") for the given calendar events."""
        report = self._get_report(report_ref)
        if report.model != "calendar.event":
            raise UserError(_(
                "The iCalendar report type only applies to calendar events, "
                "not to %(model)s."
            ) % {"model": report.model})
        events = self.env["calendar.event"].browse(res_ids).exists()
        ics_files = events._get_ics_file()
        # vobject is an optional dependency of core; without it _get_ics_file
        # returns {} and there is simply nothing to attach.
        content = b"".join(ics_files[event.id] for event in events if event.id in ics_files)
        return content, "ics"
