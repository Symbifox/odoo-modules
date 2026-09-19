from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DuesRule(models.Model):
    """La règle de cotisation d'une convention, dans ses deux formes.

    🔴 Les deux formes sont cumulables, et ce n'est pas un cas tordu : « 1,75 %
    du salaire brut plus 0,50 $ par paie » est une formulation ordinaire. Un
    modèle qui n'offre qu'un choix exclusif oblige à mentir dès la première
    convention saisie.

    🔴 La cotisation suit l'UNITÉ, pas l'adhésion. L'article 47 du Code du
    travail impose la retenue à tout salarié de l'unité de négociation, membre
    du syndicat ou non. L'assiette de la règle est donc l'appartenance
    « couverte », jamais l'adhésion.

    Sans paie installée, ce modèle DÉCLARE la règle et n'exécute rien. Le
    montant à retenir se calcule ailleurs, et la remise se saisit à la main.
    """

    _name = "bf.labour.dues.rule"
    _description = "Règle de cotisation syndicale"
    _order = "agreement_id, date_start desc, id desc"

    name = fields.Char(string="Nom", required=True, default="Cotisation régulière")
    agreement_id = fields.Many2one(
        "bf.labour.agreement", string="Convention", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="agreement_id.company_id",
        store=True, readonly=True, index=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    date_start = fields.Date(
        string="En vigueur depuis", required=True, default=fields.Date.context_today,
        help="Un taux change en cours de convention : la règle est datée, elle "
             "n'est pas écrasée.",
    )
    date_end = fields.Date(string="Jusqu'au")

    rate_percent = fields.Float(
        string="Pourcentage", digits=(5, 4),
        help="Appliqué à l'assiette choisie. Zéro si la convention ne prévoit "
             "qu'un montant fixe.",
    )
    amount_fixed = fields.Monetary(
        string="Montant fixe", currency_field="currency_id",
        help="Par période. Se cumule au pourcentage : les deux formes "
             "coexistent dans beaucoup de conventions.",
    )
    basis = fields.Selection(
        [
            ("gross", "Salaire brut"),
            ("regular", "Salaire régulier, hors surtemps"),
            ("hours", "Heures travaillées"),
        ],
        string="Assiette", required=True, default="gross",
        help="Ce sur quoi le pourcentage s'applique. « Hors surtemps » n'est "
             "pas un détail : c'est souvent le point de litige.",
    )
    period = fields.Selection(
        [("pay", "Par paie"), ("month", "Par mois")],
        string="Période", required=True, default="pay",
    )
    floor_amount = fields.Monetary(
        string="Plancher", currency_field="currency_id",
        help="Montant minimal par période. Zéro veut dire aucun plancher.",
    )
    cap_amount = fields.Monetary(
        string="Plafond", currency_field="currency_id",
        help="Montant maximal par période. Zéro veut dire aucun plafond, ce qui "
             "n'est pas la même chose qu'un plafond à zéro.",
    )
    initiation_fee = fields.Monetary(
        string="Droit d'entrée", currency_field="currency_id",
        help="Perçu une fois à l'adhésion. Il ne suit pas la règle courante.",
    )
    special_assessment = fields.Monetary(
        string="Cotisation spéciale", currency_field="currency_id",
        help="Fonds de grève ou prélèvement voté en assemblée. Il s'ajoute à la "
             "cotisation régulière pour la durée décidée.",
    )
    note = fields.Text(string="Note")

    @api.constrains("rate_percent", "amount_fixed")
    def _check_something_is_charged(self):
        for rule in self:
            if not rule.rate_percent and not rule.amount_fixed:
                raise ValidationError(_(
                    "Une règle de cotisation sans pourcentage ni montant fixe ne "
                    "prélève rien. Écrivez au moins l'une des deux formes."
                ))

    @api.constrains("floor_amount", "cap_amount")
    def _check_floor_under_cap(self):
        for rule in self:
            if rule.cap_amount and rule.floor_amount > rule.cap_amount:
                raise ValidationError(_(
                    "Le plancher de la cotisation dépasse son plafond."
                ))

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for rule in self:
            if rule.date_end and rule.date_end < rule.date_start:
                raise ValidationError(_(
                    "La fin d'une règle de cotisation ne peut pas précéder son "
                    "début."
                ))

    def amount_for(self, base_amount):
        """Ce que la règle prélèverait sur une assiette donnée.

        Aucune paie ne l'appelle aujourd'hui, et c'est voulu : la fonction
        existe pour que la règle soit vérifiable et testable, et pour que le
        jour où une paie arrive, elle ait une seule définition à appeler.
        """
        self.ensure_one()
        amount = (base_amount or 0.0) * (self.rate_percent or 0.0) / 100.0
        amount += self.amount_fixed or 0.0
        if self.floor_amount and amount < self.floor_amount:
            amount = self.floor_amount
        if self.cap_amount and amount > self.cap_amount:
            amount = self.cap_amount
        return amount


class DuesRemittance(models.Model):
    """La remise déclarée pour une période.

    Elle se SAISIT. Rien ne la calcule, parce que rien dans le parc ne sait ce
    qui a été payé à qui. Ce modèle existe pour que la remise soit tracée,
    datée et rapprochable, pas pour faire semblant de la produire.
    """

    _name = "bf.labour.dues.remittance"
    _description = "Remise de cotisations"
    _inherit = ["mail.thread"]
    _order = "period_end desc, id desc"

    name = fields.Char(
        string="Référence", compute="_compute_name", store=True, readonly=False,
    )
    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité de négociation", required=True,
        ondelete="restrict", index=True, tracking=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="unit_id.company_id",
        store=True, readonly=True, index=True,
    )
    union_id = fields.Many2one(
        "bf.labour.union", string="Syndicat", related="unit_id.union_id",
        store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    period_start = fields.Date(string="Début de la période", required=True)
    period_end = fields.Date(string="Fin de la période", required=True)
    line_ids = fields.One2many(
        "bf.labour.dues.remittance.line", "remittance_id", string="Lignes",
    )
    amount_total = fields.Monetary(
        string="Total", currency_field="currency_id",
        compute="_compute_amount_total", store=True,
    )
    headcount = fields.Integer(
        string="Personnes", compute="_compute_amount_total", store=True,
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("declared", "Déclarée"),
            ("remitted", "Remise"),
        ],
        string="État", default="draft", required=True, tracking=True,
    )
    date_remitted = fields.Date(string="Remise le", readonly=True, copy=False)
    note = fields.Text(string="Note")

    @api.depends("unit_id.name", "period_start", "period_end")
    def _compute_name(self):
        for remittance in self:
            if remittance.name:
                continue
            bits = [remittance.unit_id.name or _("Remise")]
            if remittance.period_end:
                bits.append(remittance.period_end.strftime("%Y-%m"))
            remittance.name = " ".join(bits)

    @api.depends("line_ids.amount")
    def _compute_amount_total(self):
        for remittance in self:
            remittance.amount_total = sum(remittance.line_ids.mapped("amount"))
            remittance.headcount = len(remittance.line_ids)

    @api.constrains("period_start", "period_end")
    def _check_period(self):
        for remittance in self:
            if remittance.period_end < remittance.period_start:
                raise ValidationError(_(
                    "La fin de la période précède son début."
                ))

    def action_declare(self):
        self.write({"state": "declared"})
        return True

    def action_mark_remitted(self):
        for remittance in self:
            remittance.write({
                "state": "remitted",
                "date_remitted": fields.Date.context_today(remittance),
            })
        return True

    def action_fill_from_unit(self):
        """Poser une ligne par personne COUVERTE, à zéro.

        Les montants restent à saisir : le module ne sait pas ce qui a été payé.
        Ce que le bouton évite, c'est d'oublier quelqu'un, et il prend les
        couverts et non les membres, parce que c'est la couverture qui fait
        cotiser.
        """
        self.ensure_one()
        today = self.period_end or fields.Date.context_today(self)
        existing = self.line_ids.mapped("membership_id")
        memberships = self.unit_id.membership_ids.filtered(
            lambda m: m.covered
            and m.date_start <= today
            and (not m.date_end or m.date_end >= self.period_start)
            and m not in existing
        )
        self.env["bf.labour.dues.remittance.line"].create([
            {
                "remittance_id": self.id,
                "membership_id": membership.id,
                "amount": 0.0,
            }
            for membership in memberships
        ])
        return True


class DuesRemittanceLine(models.Model):
    _name = "bf.labour.dues.remittance.line"
    _description = "Ligne de remise de cotisations"
    _order = "remittance_id, id"

    remittance_id = fields.Many2one(
        "bf.labour.dues.remittance", string="Remise", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="remittance_id.company_id",
        store=True, readonly=True, index=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="remittance_id.currency_id", readonly=True,
    )
    membership_id = fields.Many2one(
        "bf.labour.membership", string="Appartenance", required=True,
        ondelete="restrict", index=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Personne", related="membership_id.employee_id",
        store=True, readonly=True,
    )
    amount = fields.Monetary(string="Montant", currency_field="currency_id")
