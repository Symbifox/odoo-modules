from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LetterMergeWizard(models.TransientModel):
    _name = "letter.merge.wizard"
    _description = "Fusion de lettres en lot"

    template_id = fields.Many2one(
        "letter.template", string="Modèle", required=True,
    )
    recipient_ids = fields.Many2many(
        "res.partner", string="Destinataires", required=True,
    )
    letter_date = fields.Date(
        string="Date", default=fields.Date.context_today, required=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Expéditeur",
        required=True,
        default=lambda self: self.env.company,
    )
    auto_finalize = fields.Boolean(
        string="Finaliser automatiquement",
        help="Passe chaque lettre créée à l'état « Finalisée ».",
    )
    created_letter_ids = fields.Many2many(
        "letter.document", string="Lettres créées", readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get("active_model") == "res.partner":
            partner_ids = self.env.context.get("active_ids") or []
            if partner_ids:
                res["recipient_ids"] = [(6, 0, partner_ids)]
        return res

    def action_create_letters(self):
        self.ensure_one()
        if not self.recipient_ids:
            raise UserError(_("Sélectionnez au moins un destinataire."))
        Letter = self.env["letter.document"]
        letters = Letter.browse()
        for partner in self.recipient_ids:
            letter = Letter.create(
                {
                    "name": self.template_id.name,
                    "template_id": self.template_id.id,
                    "partner_id": partner.id,
                    "company_id": self.company_id.id,
                    "letter_date": self.letter_date,
                    "letterhead_style": self.template_id.letterhead_style,
                }
            )
            letter._apply_persona_defaults()
            letter.action_apply_template()
            if self.auto_finalize:
                letter.action_finalize()
            letters |= letter
        self.created_letter_ids = letters
        return {
            "type": "ir.actions.act_window",
            "name": _("Lettres créées"),
            "res_model": "letter.document",
            "view_mode": "list,form",
            "domain": [("id", "in", letters.ids)],
        }
