from odoo import _, fields, models
from odoo.exceptions import UserError

from ..charlemagne import fic


class CharlemagneRequest(models.TransientModel):
    """Ask the Ministry for a list, a report or a feedback file."""

    _name = "bf.school.charlemagne.request"
    _description = "Request for a file from Charlemagne"

    batch_id = fields.Many2one("bf.school.charlemagne.batch", required=True)
    code = fields.Selection(lambda self: [(code, "%s: %s" % (code, title)) for code, (title, _spec) in fic.FIC.items()],
                            required=True)
    parameters = fields.Text(
        help="One parameter per line, TAG=value, for example AN_EPREU=2027 and SESN_EPREU=06. The school's code "
             "and the school year are filled in when the request asks for them.")

    def action_add(self):
        self.ensure_one()
        values = {}
        for line in (self.parameters or "").splitlines():
            if not line.strip():
                continue
            if "=" not in line:
                raise UserError(_("Write one parameter per line as TAG=value: %s", line))
            tag, _sep, value = line.partition("=")
            values[tag.strip()] = value.strip()
        self.batch_id.action_add_file_request(self.code, values)
        return {"type": "ir.actions.act_window_close"}
