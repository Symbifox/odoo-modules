import logging
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_MSGID_RE = re.compile(r"<[^>]+>")


class BfEmail(models.Model):
    _inherit = "bf.email"

    def _find_target_by_headers(self):
        """Walk In-Reply-To + References, return (model, res_id, reason) or
        (False, 0, 'none'). Skips messages on the current email itself."""
        self.ensure_one()
        msg = self.mail_message_id
        if not msg:
            return False, 0, "none"
        current_msg_id = msg.message_id or ""
        parent = msg.parent_id
        if parent and parent.model and parent.res_id:
            if parent.model != self.res_model or parent.res_id != self.res_id:
                return parent.model, parent.res_id, "parent"

        candidates = []
        in_reply_to = (self.in_reply_to or "").strip()
        for mid in _MSGID_RE.findall(in_reply_to):
            candidates.append((mid, "header_match"))
        refs_field = msg.mapped("parent_id.message_id") if parent else []
        for mid in refs_field:
            if mid:
                candidates.append((mid, "reference_match"))
        domain = [
            ("message_id", "in", [mid for mid, _r in candidates if mid and mid != current_msg_id]),
        ]
        if not domain[0][2]:
            return False, 0, "none"
        hits = self.env["mail.message"].search(domain, limit=20)
        for hit in hits:
            if not hit.model or not hit.res_id:
                continue
            if hit.model == self.res_model and hit.res_id == self.res_id:
                continue
            reason = next(
                (r for mid, r in candidates if mid == hit.message_id), "header_match"
            )
            return hit.model, hit.res_id, reason
        return False, 0, "none"

    def action_open_reroute_wizard(self):
        self.ensure_one()
        if not self.mail_message_id:
            raise UserError(_("Ce courriel n'a pas de mail.message associ\u00e9."))
        model, res_id, reason = self._find_target_by_headers()
        suggested_display = False
        if model and res_id:
            target = self.env[model].browse(res_id)
            if target.exists():
                suggested_display = f"{target.display_name} [{model},{res_id}]"
            else:
                model, res_id, reason = False, 0, "none"
        current_display = False
        if self.res_model and self.res_id:
            try:
                cur = self.env[self.res_model].browse(self.res_id)
                if cur.exists():
                    current_display = f"{cur.display_name} [{self.res_model},{self.res_id}]"
            except Exception:
                pass
        vals = {
            "email_id": self.id,
            "current_display": current_display,
            "suggested_model": model or False,
            "suggested_res_id": res_id or 0,
            "suggested_display": suggested_display,
            "suggested_reason": reason,
        }
        if model and res_id:
            vals["target_reference"] = f"{model},{res_id}"
        wizard = self.env["bf.mail.reroute.wizard"].create(vals)
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.mail.reroute.wizard",
            "res_id": wizard.id,
            "views": [[False, "form"]],
            "target": "new",
        }
