from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class Claim(models.Model):
    """Une demande d'avantage.

    La porte d'entrée est le DROIT, pas un solde de points : sans droit ouvert
    le jour de la demande, elle ne part pas. C'est ce qui sépare ce module d'un
    catalogue de récompenses.
    """

    _name = "bf.ex.claim"
    _description = "Demande d'avantage"
    _inherit = ["mail.thread"]
    _order = "date_request desc, id desc"

    name = fields.Char(string="Référence", compute="_compute_name", store=True)
    employee_id = fields.Many2one(
        "hr.employee", string="Employé", required=True, ondelete="cascade", index=True,
        default=lambda self: self.env.user.employee_id,
    )
    benefit_id = fields.Many2one(
        "bf.ex.benefit", string="Avantage", required=True, ondelete="restrict", index=True,
        domain="[('approval_required', '=', True)]",
    )
    company_id = fields.Many2one(
        "res.company", related="benefit_id.company_id", store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    date_request = fields.Date(
        string="Demandé le", required=True, default=fields.Date.context_today,
    )
    quantity = fields.Float(string="Quantité", default=1.0)
    amount = fields.Monetary(string="Montant demandé", currency_field="currency_id")
    motivation = fields.Text(string="Motivation")
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("submitted", "Soumise"),
            ("approved", "Approuvée"),
            ("refused", "Refusée"),
            ("consumed", "Consommée"),
        ],
        string="État", default="draft", required=True, tracking=True,
    )
    approver_id = fields.Many2one("res.users", string="Approbateur", readonly=True)
    date_processed = fields.Datetime(string="Traitée le", readonly=True)
    refusal_reason = fields.Text(string="Motif du refus", tracking=True)
    usage_id = fields.Many2one("bf.ex.usage", string="Usage produit", readonly=True)

    @api.depends("employee_id", "benefit_id", "date_request")
    def _compute_name(self):
        for claim in self:
            claim.name = "%s / %s / %s" % (
                claim.employee_id.name or "?",
                claim.benefit_id.name or "?",
                claim.date_request or "?",
            )

    @api.constrains("benefit_id")
    def _check_approval_required(self):
        for claim in self:
            if not claim.benefit_id.approval_required:
                raise ValidationError(_(
                    "« %s » ne passe pas par une demande : il s'applique dès que "
                    "le droit est ouvert.", claim.benefit_id.name,
                ))

    def _entitlement(self):
        self.ensure_one()
        return self.env["bf.ex.entitlement"].sudo().search([
            ("employee_id", "=", self.employee_id.id),
            ("benefit_id", "=", self.benefit_id.id),
            ("date_start", "<=", self.date_request),
            "|", ("date_end", "=", False), ("date_end", ">=", self.date_request),
        ], limit=1)

    def action_submit(self):
        for claim in self:
            if not claim._entitlement():
                raise UserError(_(
                    "%(employee)s n'a aucun droit ouvert à « %(benefit)s » "
                    "le %(date)s. Un avantage se demande parce qu'on y a droit.",
                    employee=claim.employee_id.name,
                    benefit=claim.benefit_id.name,
                    date=claim.date_request,
                ))
            claim.state = "submitted"
        return True

    def _allowed_approvers(self):
        """Qui peut trancher cette demande, selon le mode de l'avantage."""
        self.ensure_one()
        benefit = self.benefit_id
        users = self.env["res.users"].browse()
        if benefit.approver_mode in ("responsible", "both"):
            users |= benefit.responsible_id
        if benefit.approver_mode in ("manager", "both"):
            users |= self.employee_id.parent_id.user_id
        return users

    def action_approve(self):
        for claim in self:
            if claim.state != "submitted":
                raise UserError(_("Seule une demande soumise s'approuve."))
            claim.write({
                "state": "approved",
                "approver_id": self.env.uid,
                "date_processed": fields.Datetime.now(),
            })
        return True

    def action_refuse(self):
        for claim in self:
            if not (claim.refusal_reason or "").strip():
                raise UserError(_(
                    "Un refus exige un motif écrit. La personne a le droit de "
                    "savoir pourquoi."
                ))
            claim.write({
                "state": "refused",
                "approver_id": self.env.uid,
                "date_processed": fields.Datetime.now(),
            })
        return True

    def action_consume(self):
        """Transformer une demande approuvée en ligne d'usage confirmée."""
        Usage = self.env["bf.ex.usage"]
        for claim in self:
            if claim.state != "approved":
                raise UserError(_("Seule une demande approuvée se consomme."))
            usage = Usage.create({
                "employee_id": claim.employee_id.id,
                "benefit_id": claim.benefit_id.id,
                "date": fields.Date.context_today(claim),
                "quantity": claim.quantity,
                "amount": claim.amount,
                "source": "manual",
            })
            usage.action_confirm()
            claim.write({"state": "consumed", "usage_id": usage.id})
        return True
