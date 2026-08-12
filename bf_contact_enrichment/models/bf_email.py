import email.utils

from odoo import _, models
from odoo.exceptions import UserError

from ..tools import matching


class BfEmail(models.Model):
    _inherit = "bf.email"

    def action_bf_create_or_enrich_contact(self):
        """Create (or find) the sender's contact, then open the enrich wizard.

        - If the email already resolves to a partner, enrich that one.
        - Else search by sender address; if found, enrich it.
        - Else create a lightweight res.partner from the From header (using the
          display name when present — never a surname invented from the local
          part) and open the signature enrichment to fill the rest.
        """
        self.ensure_one()
        display, addr = email.utils.parseaddr(self.email_from or "")
        display = (display or "").strip()
        addr = (addr or "").strip()

        partner = self.partner_id
        if not partner and addr:
            partner = matching.find_partner_match(
                self.env, name=display or None, email=addr
            )

        created = False
        if not partner:
            if not addr:
                raise UserError(
                    _("Aucune adresse d'expéditeur exploitable sur ce courriel.")
                )
            partner = self.env["res.partner"].create({
                "name": display or addr,
                "email": addr,
            })
            created = True
            if "partner_id" in self._fields and not self.partner_id:
                self.partner_id = partner.id
            partner.message_post(
                body=_("Contact créé depuis un courriel entrant.")
            )

        return {
            "type": "ir.actions.act_window",
            "name": _("Enrichir le contact") if not created
                    else _("Compléter le nouveau contact"),
            "res_model": "bf.contact.enrich.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_partner_id": partner.id,
                "default_source": "signature",
            },
        }
