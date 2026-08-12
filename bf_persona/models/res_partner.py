from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    persona_id = fields.Many2one(
        "contact.persona",
        compute="_compute_persona_id",
        compute_sudo=True,
        string="Persona",
    )
    has_persona = fields.Boolean(
        compute="_compute_persona_id", compute_sudo=True, store=False,
    )
    persona_summary = fields.Text(
        related="persona_id.claude_context_summary",
        string="Résumé persona",
        readonly=True,
    )

    @api.depends()  # invalidated by create/write on contact.persona below
    def _compute_persona_id(self):
        Persona = self.env["contact.persona"].sudo()
        personas = Persona.search([("partner_id", "in", self.ids)])
        by_partner = {p.partner_id.id: p for p in personas}
        for partner in self:
            persona = by_partner.get(partner.id)
            partner.persona_id = persona.id if persona else False
            partner.has_persona = bool(persona)

    def action_open_persona(self):
        self.ensure_one()
        return self.env["contact.persona"].action_get_or_create_for_partner(self.id)

    def action_launch_persona_skill(self):
        """Pop the Claude chat with /persona refresh <contact> pre-filled."""
        self.ensure_one()
        prompt = f"/persona refresh {self.display_name or self.name or '?'}"
        return {
            "type": "ir.actions.client",
            "tag": "claude_chat_launch",
            "params": {"prompt": prompt, "autosend": False},
        }
