from odoo import _, api, models
from odoo.exceptions import AccessError


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.model
    def _school_sealed_field(self, res_model, res_field):
        """Does this field of this model keep a file the school seals, or logs when it changes?

        The owner model says so in `_school_sealed_fields`.
        """
        return bool(res_model and res_field and res_model in self.env
                    and res_field in getattr(self.env[res_model], "_school_sealed_fields", ()))

    def _school_refuse_change(self):
        if self.env.su:
            return
        for attachment in self.sudo():
            if self._school_sealed_field(attachment.res_model, attachment.res_field):
                # 🔴 The binary field's own write (the screen, the buttons) goes through here as the superuser and
                # keeps its trace; an attachment changed by hand (RPC) would replace a sealed file with none.
                raise AccessError(_(
                    "A file kept with a student's file is not replaced or deleted from here: it changes by the "
                    "screen or the button of its record, which keep a trace."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                if self._school_sealed_field(vals.get("res_model"), vals.get("res_field")):
                    raise AccessError(_(
                        "A file kept with a student's file is not replaced or deleted from here: it changes by the "
                        "screen or the button of its record, which keep a trace."))
        return super().create(vals_list)

    def write(self, vals):
        self._school_refuse_change()
        return super().write(vals)

    def unlink(self):
        self._school_refuse_change()
        return super().unlink()
