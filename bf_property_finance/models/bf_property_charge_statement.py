"""État des charges communes dues, au sens de l'art. 1069 al. 2 C.c.Q.

  « Celui qui se propose d'acquérir une fraction de copropriété peut néanmoins
  demander au syndicat des copropriétaires un état des charges communes dues
  relativement à cette fraction et le syndicat est, de ce fait, autorisé à le
  lui fournir, sauf à en aviser au préalable le propriétaire de la fraction ou
  ses ayants cause ; le proposant acquéreur n'est alors tenu au paiement de ces
  charges communes que si l'état lui est fourni par le syndicat dans les
  15 jours de la demande.
  L'état fourni est ajusté selon le dernier budget annuel des copropriétaires. »

  (1991, c. 64, a. 1069 ; 2002, c. 19, a. 6 ; 2019, c. 28, a. 36.)

🔴 **Le délai joue CONTRE le syndicat, et c'est unique dans ce module.** Partout
ailleurs une échéance ratée expose le syndicat à une sanction ; ici elle lui
fait perdre une créance qu'il avait. L'alinéa 1 rend l'acquéreur tenu de toutes
les charges dues au moment de l'acquisition ; l'alinéa 2 retire cette dette au
proposant acquéreur si l'état ne lui est pas fourni dans les quinze jours. Un
syndicat qui laisse filer se retrouve à réclamer au vendeur, souvent parti.

⚠️ **Trois régimes que la doctrine confond**, et ce modèle n'en couvre qu'un :

- **art. 1069 al. 2**, ici : demandé par le PROPOSANT ACQUÉREUR, préavis
  obligatoire au propriétaire, 15 jours, et la sanction ci-dessus.
- **art. 1068.1** : l'attestation sur l'état de la copropriété, demandée par le
  COPROPRIÉTAIRE VENDEUR, contenu fixé par règlement, 15 jours aussi. Elle vit
  dans `bf_property_loi16`.
- **art. 1068.2** : les documents permettant un consentement éclairé, demandés
  par le PROMETTANT ACHETEUR, à ses frais, « avec diligence », sans délai
  chiffré.

⚠️ **Le préavis au propriétaire n'est pas une politesse.** L'alinéa dit que le
syndicat est autorisé à fournir l'état « sauf à en aviser au préalable le
propriétaire ». C'est la condition de l'autorisation, et l'état porte des
renseignements sur les dettes d'une personne nommée : le fournir sans préavis
poserait aussi une question de renseignements personnels.

⚠️ **Un état fourni ne se recalcule pas.** Il énonce des montants à une date, il
a été remis, et l'acquéreur s'y fie. Ses lignes sont figées à la remise, comme
un appel de fonds transmis ne se réécrit pas.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Art. 1069 al. 2 : « dans les 15 jours de la demande ».
STATEMENT_DAYS = 15


class BfPropertyChargeStatement(models.Model):
    _name = "bf.property.charge.statement"
    _description = "État des charges communes dues"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_date desc, id desc"

    name = fields.Char(
        string="État", required=True, default=lambda s: _("Nouveau"), tracking=True
    )
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        related="syndicat_id.company_id", store=True, string="Société"
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise"
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction",
        required=True,
        ondelete="cascade",
        domain="[('syndicat_id', '=', syndicat_id)]",
        tracking=True,
        help="Art. 1069 : l'état porte sur les charges dues « relativement à "
             "cette fraction ». La charge suit la fraction, pas la personne.",
    )
    requester_partner_id = fields.Many2one(
        "res.partner",
        string="Proposant acquéreur",
        required=True,
        tracking=True,
        help="Art. 1069 al. 2 : « celui qui se propose d'acquérir ». Ce n'est "
             "ni le copropriétaire vendeur, qui demande l'attestation de "
             "l'art. 1068.1, ni le promettant acheteur de l'art. 1068.2.",
    )
    owner_partner_ids = fields.Many2many(
        "res.partner",
        string="Propriétaires à aviser",
        compute="_compute_owners",
        help="Art. 1069 al. 2 : le syndicat n'est autorisé à fournir l'état "
             "que « sauf à en aviser au préalable le propriétaire de la "
             "fraction ou ses ayants cause ».",
    )

    request_date = fields.Date(
        string="Demandée le",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    deadline_date = fields.Date(
        string="À fournir au plus tard le",
        compute="_compute_deadline",
        store=True,
    )
    owner_notice_date = fields.Date(
        string="Propriétaire avisé le",
        tracking=True,
        help="Le préavis conditionne l'autorisation de fournir l'état. Sans "
             "lui, le module refuse la remise.",
    )
    issued_date = fields.Date(string="Fourni le", tracking=True)
    cancelled = fields.Boolean(string="Annulé", tracking=True)

    state = fields.Selection(
        [
            ("requested", "Demandé"),
            ("late", "Délai dépassé"),
            ("issued", "Fourni dans le délai"),
            ("issued_late", "Fourni hors délai"),
            ("cancelled", "Annulé"),
        ],
        string="État",
        compute="_compute_state",
        store=True,
        tracking=True,
    )
    acquirer_bound = fields.Boolean(
        string="L'acquéreur est tenu des charges",
        compute="_compute_state",
        store=True,
        help="Art. 1069 al. 2 : « le proposant acquéreur n'est alors tenu au "
             "paiement de ces charges communes QUE SI l'état lui est fourni "
             "par le syndicat dans les 15 jours de la demande ». Hors délai, "
             "le syndicat perd sa créance contre lui et devra la réclamer au "
             "vendeur.",
    )
    binding_rule = fields.Char(
        string="Effet du délai", compute="_compute_state", store=True
    )

    budget_id = fields.Many2one(
        "bf.property.budget",
        string="Dernier budget annuel",
        domain="[('syndicat_id', '=', syndicat_id)]",
        tracking=True,
        help="Art. 1069 al. 3 : « L'état fourni est ajusté selon le dernier "
             "budget annuel des copropriétaires. » Le module rattache le "
             "budget et conserve l'ajustement ; il ne le calcule pas, "
             "l'ajustement supposant un jugement sur l'exercice en cours.",
    )
    adjustment_amount = fields.Monetary(
        string="Ajustement selon le budget",
        currency_field="currency_id",
        tracking=True,
        help="Art. 1069 al. 3. Positif ou négatif, saisi à la main.",
    )
    adjustment_note = fields.Text(string="Explication de l'ajustement")

    line_ids = fields.One2many(
        "bf.property.charge.statement.line", "statement_id", string="Détail"
    )
    amount_capital = fields.Monetary(
        string="Capital dû", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_interest = fields.Monetary(
        string="Intérêts dus", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_total = fields.Monetary(
        string="Total dû", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    note = fields.Char(string="Note")

    @api.depends("unit_id.owner_ids")
    def _compute_owners(self):
        for statement in self:
            statement.owner_partner_ids = statement.unit_id.owner_ids

    @api.depends("request_date")
    def _compute_deadline(self):
        for statement in self:
            statement.deadline_date = (
                statement.request_date + relativedelta(days=STATEMENT_DAYS)
                if statement.request_date
                else False
            )

    @api.depends("issued_date", "deadline_date", "cancelled")
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for statement in self:
            deadline = statement.deadline_date
            if statement.cancelled:
                statement.state = "cancelled"
                statement.acquirer_bound = False
                statement.binding_rule = _("Demande annulée.")
                continue
            if statement.issued_date:
                in_time = bool(deadline) and statement.issued_date <= deadline
                statement.state = "issued" if in_time else "issued_late"
                statement.acquirer_bound = in_time
                statement.binding_rule = (
                    _(
                        "État fourni le %(issued)s, dans les 15 jours de la "
                        "demande. Art. 1069 al. 2 : le proposant acquéreur est "
                        "tenu au paiement des charges qui y figurent."
                    )
                    % {"issued": fields.Date.to_string(statement.issued_date)}
                    if in_time
                    else _(
                        "⚠️ État fourni le %(issued)s, après l'échéance du "
                        "%(deadline)s. Art. 1069 al. 2 : le proposant "
                        "acquéreur n'est PAS tenu au paiement de ces charges. "
                        "Le syndicat a perdu sa créance contre lui et devra la "
                        "réclamer au vendeur."
                    )
                    % {
                        "issued": fields.Date.to_string(statement.issued_date),
                        "deadline": fields.Date.to_string(deadline),
                    }
                )
                continue
            late = bool(deadline) and deadline < today
            statement.state = "late" if late else "requested"
            statement.acquirer_bound = False
            statement.binding_rule = (
                _(
                    "🔴 Les 15 jours de l'art. 1069 al. 2 sont écoulés depuis "
                    "le %(deadline)s et l'état n'a pas été fourni. Fourni "
                    "maintenant, il n'obligera plus le proposant acquéreur."
                )
                % {"deadline": fields.Date.to_string(deadline)}
                if late
                else _(
                    "À fournir au plus tard le %(deadline)s. Passé ce jour, le "
                    "proposant acquéreur ne sera plus tenu des charges."
                )
                % {"deadline": fields.Date.to_string(deadline or "")}
            )

    @api.depends(
        "line_ids.amount_capital",
        "line_ids.amount_interest",
        "adjustment_amount",
    )
    def _compute_amounts(self):
        for statement in self:
            statement.amount_capital = sum(
                statement.line_ids.mapped("amount_capital")
            )
            statement.amount_interest = sum(
                statement.line_ids.mapped("amount_interest")
            )
            statement.amount_total = (
                statement.amount_capital
                + statement.amount_interest
                + (statement.adjustment_amount or 0.0)
            )

    @api.constrains("unit_id", "syndicat_id")
    def _check_unit_syndicat(self):
        for statement in self:
            if statement.unit_id.syndicat_id != statement.syndicat_id:
                raise ValidationError(
                    _("La fraction appartient à un autre syndicat.")
                )

    @api.constrains("budget_id", "syndicat_id")
    def _check_budget_syndicat(self):
        for statement in self:
            if (
                statement.budget_id
                and statement.budget_id.syndicat_id != statement.syndicat_id
            ):
                raise ValidationError(
                    _("Le budget annuel appartient à un autre syndicat.")
                )

    # ── Actions ──

    def action_compute_lines(self):
        """Relève les charges dues de la fraction, à cette date."""
        Line = self.env["bf.property.charge.statement.line"]
        for statement in self:
            if statement.issued_date:
                raise UserError(
                    _(
                        "Un état fourni ne se recalcule pas. Il énonce des "
                        "montants à une date, il a été remis, et l'acquéreur "
                        "s'y fie. Ouvrez une nouvelle demande si les chiffres "
                        "ont changé."
                    )
                )
            statement.line_ids.unlink()
            rows = statement.unit_id._charges_due()
            if rows:
                Line.create(
                    [
                        {
                            "statement_id": statement.id,
                            "name": row["call"],
                            "due_date": row["due_date"],
                            "amount_capital": row["capital"],
                            "amount_interest": row["interest"],
                        }
                        for row in rows
                    ]
                )
            statement.message_post(
                body=_(
                    "Relevé des charges dues : %(count)d contribution(s) "
                    "impayée(s) pour la fraction %(unit)s."
                )
                % {"count": len(rows), "unit": statement.unit_id.display_name}
            )
        return True

    def action_notify_owner(self):
        """Le préavis de l'art. 1069 al. 2, qui conditionne l'autorisation."""
        today = fields.Date.context_today(self)
        for statement in self:
            if statement.issued_date:
                raise UserError(
                    _("L'état est déjà fourni : le préavis devait le précéder.")
                )
            statement.owner_notice_date = today
            statement.message_post(
                body=_(
                    "Propriétaire avisé le %(date)s : %(owners)s. Art. 1069 "
                    "al. 2 C.c.Q., le syndicat n'est autorisé à fournir l'état "
                    "que « sauf à en aviser au préalable le propriétaire de la "
                    "fraction ou ses ayants cause »."
                )
                % {
                    "date": today,
                    "owners": ", ".join(
                        statement.owner_partner_ids.mapped("name")
                    )
                    or _("aucun propriétaire au registre"),
                }
            )
        return True

    def action_issue(self):
        for statement in self:
            if statement.cancelled:
                raise UserError(_("Une demande annulée ne se fournit pas."))
            if not statement.owner_notice_date:
                raise UserError(
                    _(
                        "Art. 1069 al. 2 C.c.Q. : le syndicat n'est autorisé à "
                        "fournir l'état que « sauf à en aviser au préalable le "
                        "propriétaire de la fraction ou ses ayants cause ». "
                        "Consignez le préavis avant de fournir l'état."
                    )
                )
            if not statement.budget_id:
                raise UserError(
                    _(
                        "Art. 1069 al. 3 C.c.Q. : « L'état fourni est ajusté "
                        "selon le dernier budget annuel des copropriétaires. » "
                        "Rattachez le budget sur lequel l'ajustement est fait."
                    )
                )
            statement.issued_date = fields.Date.context_today(statement)
            statement.message_post(body=statement.binding_rule)
        return True

    def action_cancel(self):
        self.write({"cancelled": True})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name") in (None, "", _("Nouveau")):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bf.property.charge.statement"
                ) or _("État des charges")
        return super().create(vals_list)

    @api.model
    def _cron_refresh_state(self):
        """Le dépassement naît du passage de l'échéance, pas d'une écriture."""
        today = fields.Date.context_today(self)
        stale = self.search(
            [("state", "=", "requested"), ("deadline_date", "<", today)]
        )
        if not stale:
            return 0
        stale.modified(["issued_date"])
        return len(stale)


class BfPropertyChargeStatementLine(models.Model):
    _name = "bf.property.charge.statement.line"
    _description = "Ligne d'un état des charges"
    _order = "statement_id, due_date, id"

    statement_id = fields.Many2one(
        "bf.property.charge.statement",
        string="État",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="statement_id.company_id", store=True, string="Société"
    )
    currency_id = fields.Many2one(
        related="statement_id.currency_id", string="Devise"
    )
    name = fields.Char(string="Contribution", required=True)
    due_date = fields.Date(string="Exigible le")
    amount_capital = fields.Monetary(
        string="Capital", currency_field="currency_id"
    )
    amount_interest = fields.Monetary(
        string="Intérêts", currency_field="currency_id"
    )
    amount_total = fields.Monetary(
        string="Total", compute="_compute_total", store=True,
        currency_field="currency_id",
    )

    @api.depends("amount_capital", "amount_interest")
    def _compute_total(self):
        for line in self:
            line.amount_total = line.amount_capital + line.amount_interest
