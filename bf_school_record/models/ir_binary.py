from odoo import _, api, models
from odoo.exceptions import UserError


class IrBinary(models.AbstractModel):
    _inherit = "ir.binary"

    def _record_to_stream(self, record, field_name):
        """A file of the student's file that leaves by a standard route (`/web/content`, `/web/image`) is logged too.

        The routes of the module log their own downloads; the binary widget of a screen, or an address copied
        from it, goes through here. A record that has the hook `_school_log_download(field)` hears of it, whether the
        route names its field or the attachment under it. The hook writes the line and answers True when the file
        must not be given out (a sealed file that no longer matches its seal): nothing is served then.

        🔴 These routes are declared read-only: on a replica (and under the test server) their cursor refuses a
        write, Odoo logs an error and runs the whole request again. The line goes by a cursor of its own,
        committed at once, so that there is neither the error nor the second run, and so that the line of a
        refusal stays although the request ends in an error.
        """
        stream = super()._record_to_stream(record, field_name)
        owner, owner_field = record, field_name
        if record._name == "ir.attachment":
            attachment = record.sudo()
            if not (attachment.res_model and attachment.res_id and attachment.res_field
                    and attachment.res_model in self.env):
                return stream
            owner = self.env[attachment.res_model].sudo().browse(attachment.res_id).exists()
            owner_field = attachment.res_field
        if getattr(owner, "_school_log_download", None) is None or not owner:
            return stream
        model, ids = owner._name, owner.ids
        if self.env.cr.readonly:
            # As the asset route does: a read-only transaction holds nothing worth keeping, and the test cursor
            # refuses a read/write one opened over it.
            self.env.cr.rollback()
            with self.env.registry.cursor() as cr:
                env = api.Environment(cr, self.env.uid, dict(self.env.context))
                refused = env[model].browse(ids)._school_log_download(owner_field)
        else:
            refused = self.env[model].browse(ids)._school_log_download(owner_field)
        if refused:
            raise UserError(_("This file no longer matches its seal: it is not given out."))
        return stream
