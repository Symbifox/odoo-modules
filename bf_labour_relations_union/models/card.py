from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class Card(models.Model):
    """L'adhésion, avec son histoire.

    Une personne adhère, se retire, réadhère. Un simple booléen sur
    l'appartenance répondrait « membre : oui », jamais « depuis quand », et
    c'est la seconde question qui se pose en assemblée quand un vote est
    contesté.

    🔴 Adhérer ne change PAS la couverture. Une personne couverte qui n'a jamais
    signé cotise quand même (article 47), et une adhésion qui modifierait
    `covered` mélangerait les deux états que le socle sépare exprès.
    """

    _name = "bf.labour.card"
    _description = "Adhésion syndicale"
    _inherit = ["mail.thread"]
    _order = "membership_id, date_signed desc, id desc"

    membership_id = fields.Many2one(
        "bf.labour.membership", string="Appartenance", required=True,
        ondelete="cascade", index=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Personne", related="membership_id.employee_id",
        store=True, readonly=True,
    )
    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité", related="membership_id.unit_id",
        store=True, readonly=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="membership_id.company_id",
        store=True, readonly=True, index=True,
    )
    union_id = fields.Many2one(
        "bf.labour.union", string="Syndicat", related="membership_id.union_id",
        store=True, readonly=True,
    )
    number = fields.Char(string="Numéro de carte")
    date_signed = fields.Date(
        string="Signée le", required=True, default=fields.Date.context_today,
        tracking=True,
    )
    date_withdrawn = fields.Date(string="Retirée le", tracking=True)
    withdrawal_reason = fields.Text(string="Motif du retrait")
    state = fields.Selection(
        [("active", "En vigueur"), ("withdrawn", "Retirée")],
        string="État", compute="_compute_state", store=True,
    )

    @api.depends("date_withdrawn")
    def _compute_state(self):
        for card in self:
            card.state = "withdrawn" if card.date_withdrawn else "active"

    @api.constrains("date_signed", "date_withdrawn")
    def _check_dates(self):
        for card in self:
            if card.date_withdrawn and card.date_withdrawn < card.date_signed:
                raise ValidationError(_(
                    "Un retrait d'adhésion ne précède pas la signature."
                ))

    @api.constrains("membership_id", "date_withdrawn")
    def _check_single_active_card(self):
        """Une seule adhésion en vigueur à la fois par appartenance.

        Deux cartes actives donneraient deux droits de vote à la même personne,
        ce qui se voit au dépouillement et jamais avant.
        """
        for card in self:
            if card.date_withdrawn:
                continue
            others = self.search_count([
                ("membership_id", "=", card.membership_id.id),
                ("date_withdrawn", "=", False),
                ("id", "!=", card.id),
            ])
            if others:
                raise ValidationError(_(
                    "%(nom)s a déjà une adhésion en vigueur. Retirez-la avant "
                    "d'en signer une nouvelle.",
                    nom=card.employee_id.display_name,
                ))

    @api.model_create_multi
    def create(self, vals_list):
        cards = super().create(vals_list)
        # L'adhésion marque l'appartenance comme membre. Elle ne touche JAMAIS
        # `covered` : la couverture vient de l'accréditation, pas d'une carte.
        for card in cards:
            if not card.date_withdrawn:
                card.membership_id.write({
                    "is_member": True,
                    "card_date": card.date_signed,
                })
        return cards

    def action_withdraw(self):
        for card in self:
            card.write({
                "date_withdrawn": card.date_withdrawn
                or fields.Date.context_today(card),
            })
            card.membership_id.is_member = False
        return True
