"""In debug mode, the access error no longer names the refused records.

Odoo 18 gives the "debug mode" group (``base.group_no_one``) to every internal
user, and anyone can open ``/odoo?debug=1``. In debug mode, the error raised by a
record rule lists up to six refused records by their name, read as superuser
(``ir.rule._make_access_error``: ``records[:6].sudo().display_name``). Reading a
guessed id thus gave the subject of a colleague's email, the title of a private
note or the name of a medication.

The error keeps the model, the id and the rules at fault: only the name changes.
The core message is left as is (same lines, company hints, translations) by
putting a neutral name in the cache of the very records it reads. The core drops
that cache itself once the message is written (``records_sudo.invalidate_recordset``).
Administrators (``base.group_system``) keep the names, to diagnose.
"""
from odoo import _, models


class IrRule(models.Model):
    _inherit = "ir.rule"

    def _make_access_error(self, operation, records):
        if self.env.su or self.env.user._is_system():
            return super()._make_access_error(operation, records)
        self = self.with_context(self.env.user.context_get())
        hidden = records[:6].sudo()
        self.env.cache.update(hidden, hidden._fields["display_name"], [_("name hidden")] * len(hidden))
        return super()._make_access_error(operation, records)
