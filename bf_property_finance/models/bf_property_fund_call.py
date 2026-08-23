"""Appels de fonds : la contribution, période par période.

La charge suit la fraction, pas la personne. C'est l'art. 1069 C.c.Q. qui le
dit le plus nettement : celui qui acquiert une fraction « est tenu au paiement,
avec les intérêts, de TOUTES les charges communes dues relativement à cette
fraction au moment de l'acquisition ». Une charge impayée ne reste pas au
vendeur, elle voyage avec la fraction. La ligne d'appel est donc rattachée à la
fraction, et les copropriétaires du moment n'y figurent que pour information.

Conséquence pratique, et elle est voulue : un appel déjà transmis ne se
réécrit pas quand la fraction change de mains. Il porte la contribution de la
fraction pour sa période, et c'est au nouvel acquéreur de la régler s'il reste
un solde.

Art. 1072 al. 3 : le syndicat avise « sans délai » chaque copropriétaire du
montant de ses contributions et de la date où elles sont exigibles. Le texte ne
donne aucun nombre de jours ; le module suit l'échéance saisie et ne fabrique
pas de délai que la loi ne pose pas.

Art. 1072.1 : une contribution spéciale exige que le conseil ait consulté
l'assemblée AVANT de la décider. La consultation d'un appel spécial est donc
distincte de celle du budget annuel, et le module la réclame séparément.

Ce qui est reçu ne se saisit plus à la main sur la contribution : il vient des
encaissements et de leurs imputations (`bf_property_payment.py`), parce que
l'ordre d'imputation est réglé par les art. 1569 à 1572 C.c.Q. et qu'un nombre
tapé à côté ne dirait pas sur quelle dette il s'est posé.

⚠️ Les intérêts ne courent PAS de l'échéance. Art. 1617 : les dommages-intérêts
de retard consistent « dans l'intérêt au taux convenu ou, à défaut de toute
convention, au taux légal », et le créancier y a droit « à compter de la
demeure ». Art. 1594 : la demeure vient des termes mêmes du contrat lorsqu'il
est stipulé que le seul écoulement du temps l'entraîne, sinon d'une demande
extrajudiciaire écrite (art. 1595). Le module n'invente ni taux ni demeure : à
défaut de l'un ou de l'autre, il porte zéro et dit pourquoi.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .bf_property_budget import CHARGE_TYPES



class BfPropertyFundCall(models.Model):
    _name = "bf.property.fund.call"
    _description = "Appel de fonds"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "due_date desc, id desc"

    name = fields.Char(string="Appel", required=True, tracking=True)
    budget_id = fields.Many2one(
        "bf.property.budget",
        string="Budget",
        required=True,
        ondelete="cascade",
        tracking=True,
        index=True,
    )
    syndicat_id = fields.Many2one(
        related="budget_id.syndicat_id", store=True, string="Syndicat", index=True
    )
    company_id = fields.Many2one(
        related="budget_id.company_id", store=True, string="Société"
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise"
    )
    call_type = fields.Selection(
        [
            ("periodic", "Appel périodique"),
            ("special", "Contribution spéciale"),
        ],
        string="Nature",
        default="periodic",
        required=True,
        tracking=True,
        help="Art. 1072.1 C.c.Q. : une contribution spéciale exige que le "
             "conseil ait consulté l'assemblée avant de la décider.",
    )
    consultation_assembly_id = fields.Many2one(
        "bf.property.assembly",
        string="Assemblée consultée",
        domain="[('syndicat_id', '=', syndicat_id)]",
        tracking=True,
        help="Art. 1072.1 C.c.Q., pour une contribution spéciale. La "
             "consultation du budget annuel ne vaut pas pour elle.",
    )
    period_start = fields.Date(string="Période du", required=True)
    period_end = fields.Date(string="au", required=True)
    due_date = fields.Date(string="Exigible le", required=True, tracking=True)
    budget_share = fields.Float(
        string="Part du budget (%)",
        default=100.0,
        digits=(16, 4),
        help="Part de l'exercice appelée ici. Quatre appels trimestriels "
             "portent 25 % chacun. Proposée d'après la durée de la période, et "
             "modifiable : un syndicat peut appeler autrement qu'au prorata "
             "des jours.",
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("issued", "Transmis"),
            ("closed", "Clôturé"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "bf.property.fund.call.line", "call_id", string="Répartition"
    )

    amount_total = fields.Monetary(
        string="Total appelé", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_received = fields.Monetary(
        string="Reçu", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_balance = fields.Monetary(
        string="Solde en capital", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_interest = fields.Monetary(
        string="Intérêts dus", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_due = fields.Monetary(
        string="Total dû", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    overdue_line_count = fields.Integer(
        string="Fractions en défaut", compute="_compute_amounts", store=True
    )

    _sql_constraints = [
        (
            "period_ordered",
            "CHECK(period_end >= period_start)",
            "La fin de la période doit suivre son début.",
        ),
        (
            "share_positive",
            "CHECK(budget_share > 0)",
            "La part du budget appelée doit être supérieure à zéro.",
        ),
    ]

    @api.depends(
        "line_ids.amount",
        "line_ids.amount_received",
        "line_ids.interest_balance",
        "line_ids.is_overdue",
    )
    def _compute_amounts(self):
        for call in self:
            call.amount_total = sum(call.line_ids.mapped("amount"))
            call.amount_received = sum(call.line_ids.mapped("amount_received"))
            call.amount_balance = call.amount_total - call.amount_received
            call.amount_interest = sum(call.line_ids.mapped("interest_balance"))
            call.amount_due = call.amount_balance + call.amount_interest
            call.overdue_line_count = len(call.line_ids.filtered("is_overdue"))

    @api.onchange("period_start", "period_end", "budget_id")
    def _onchange_period(self):
        """Propose la part au prorata des jours, sans l'imposer."""
        if not (self.period_start and self.period_end and self.budget_id):
            return
        budget_days = (
            self.budget_id.date_end - self.budget_id.date_start
        ).days + 1
        period_days = (self.period_end - self.period_start).days + 1
        if budget_days > 0:
            self.budget_share = round(100.0 * period_days / budget_days, 4)

    @api.constrains("call_type", "consultation_assembly_id", "syndicat_id")
    def _check_consultation(self):
        for call in self:
            assembly = call.consultation_assembly_id
            if assembly and assembly.syndicat_id != call.syndicat_id:
                raise ValidationError(
                    _("L'assemblée consultée appartient à un autre syndicat.")
                )

    @api.constrains("period_start", "period_end", "budget_id")
    def _check_period_within_budget(self):
        for call in self:
            budget = call.budget_id
            if call.period_start < budget.date_start or call.period_end > (
                budget.date_end
            ):
                raise ValidationError(
                    _(
                        "La période appelée déborde l'exercice « %(budget)s », "
                        "qui court du %(start)s au %(end)s."
                    )
                    % {
                        "budget": budget.name,
                        "start": budget.date_start,
                        "end": budget.date_end,
                    }
                )

    # ── Calcul ──

    def action_compute_lines(self):
        """Répartit la part appelée du budget entre les fractions."""
        Line = self.env["bf.property.fund.call.line"]
        for call in self:
            if call.state != "draft":
                raise UserError(
                    _(
                        "Un appel transmis ne se recalcule pas. Les "
                        "copropriétaires ont reçu ces montants : corrigez par "
                        "un appel distinct."
                    )
                )
            if not call.budget_id.line_ids:
                raise UserError(_("Le budget n'a aucun poste à répartir."))
            call.line_ids.unlink()
            table = call.budget_id._allocation_table(share=call.budget_share / 100.0)
            rows = []
            for unit_id, buckets in table.items():
                rows.append(
                    dict(
                        {
                            "call_id": call.id,
                            "unit_id": unit_id,
                        },
                        **{
                            "amount_%s" % code: buckets[code]
                            for code, _label in CHARGE_TYPES
                        },
                    )
                )
            if rows:
                Line.create(rows)
            call.message_post(
                body=_(
                    "Répartition calculée : %(count)d fraction(s) pour "
                    "%(share).4f %% de l'exercice."
                )
                % {"count": len(rows), "share": call.budget_share}
            )
        return True

    def action_issue(self):
        for call in self:
            if not call.line_ids:
                raise UserError(_("Calculez la répartition avant de transmettre."))
            if call.call_type == "special" and not call.consultation_assembly_id:
                raise UserError(
                    _(
                        "Art. 1072.1 C.c.Q. : le conseil d'administration doit "
                        "consulter l'assemblée des copropriétaires avant de "
                        "décider d'une contribution spéciale. Indiquez "
                        "l'assemblée consultée."
                    )
                )
            call.state = "issued"
            call.message_post(
                body=_(
                    "Appel transmis, exigible le %(due)s. Art. 1072 al. 3 "
                    "C.c.Q. : le syndicat avise sans délai chaque "
                    "copropriétaire du montant de sa contribution et de la date "
                    "où elle est exigible."
                )
                % {"due": call.due_date}
            )
        return True

    def action_close(self):
        """Clore un appel, mais pas un appel jamais transmis.

        Un appel en brouillon n'a été porté à la connaissance de personne. Le
        clore laisserait au dossier une contribution que nul n'a été appelé à
        payer, et qui compterait pourtant dans l'appelé de l'exercice. Un appel
        en brouillon se supprime ; il ne se clôt pas.
        """
        for call in self:
            if call.state == "draft":
                raise UserError(
                    _(
                        "L'appel « %(name)s » n'a jamais été transmis. "
                        "Transmettez-le, ou supprimez-le : un appel en "
                        "brouillon ne se clôt pas."
                    )
                    % {"name": call.name}
                )
        self.write({"state": "closed"})
        return True


class BfPropertyFundCallLine(models.Model):
    _name = "bf.property.fund.call.line"
    _description = "Contribution d'une fraction"
    _order = "call_id, unit_id"

    call_id = fields.Many2one(
        "bf.property.fund.call",
        string="Appel",
        required=True,
        ondelete="cascade",
        index=True,
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction",
        required=True,
        ondelete="cascade",
        index=True,
    )
    syndicat_id = fields.Many2one(
        related="call_id.syndicat_id", store=True, string="Syndicat"
    )
    company_id = fields.Many2one(
        related="call_id.company_id", store=True, string="Société"
    )
    currency_id = fields.Many2one(
        related="call_id.currency_id", string="Devise"
    )
    quote_part = fields.Float(
        related="unit_id.quote_part", string="Quote-part", digits=(16, 4)
    )
    owner_ids = fields.Many2many(
        "res.partner",
        string="Copropriétaires du moment",
        compute="_compute_owners",
        help="Pour information seulement. La charge est rattachée à la "
             "fraction : art. 1069 C.c.Q., l'acquéreur est tenu des charges "
             "dues relativement à la fraction au moment de l'acquisition.",
    )

    amount_common = fields.Monetary(
        string="Charges communes", currency_field="currency_id"
    )
    amount_restricted_maintenance = fields.Monetary(
        string="Usage restreint : entretien", currency_field="currency_id"
    )
    amount_restricted_major = fields.Monetary(
        string="Usage restreint : réparations majeures", currency_field="currency_id"
    )
    amount_contingency = fields.Monetary(
        string="Fonds de prévoyance", currency_field="currency_id"
    )
    amount_self_insurance = fields.Monetary(
        string="Fonds d'auto-assurance", currency_field="currency_id"
    )
    amount = fields.Monetary(
        string="Contribution", compute="_compute_amount", store=True,
        currency_field="currency_id",
    )
    allocation_ids = fields.One2many(
        "bf.property.payment.allocation", "line_id", string="Imputations"
    )
    amount_received = fields.Monetary(
        string="Reçu en capital",
        compute="_compute_received",
        store=True,
        currency_field="currency_id",
        help="Somme des imputations en capital des encaissements. Ne se "
             "saisit pas : l'ordre d'imputation vient des art. 1569 à 1572 "
             "C.c.Q., et un nombre tapé à côté ne dirait pas sur quelle dette "
             "il s'est posé.",
    )
    amount_interest_paid = fields.Monetary(
        string="Reçu en intérêts",
        compute="_compute_received",
        store=True,
        currency_field="currency_id",
    )
    balance = fields.Monetary(
        string="Solde en capital", compute="_compute_amount", store=True,
        currency_field="currency_id",
    )
    demeure_date = fields.Date(
        string="Mise en demeure le",
        help="Art. 1595 C.c.Q. : date de la demande extrajudiciaire écrite. "
             "Sert quand la déclaration de copropriété ne stipule pas que le "
             "seul écoulement du temps constitue le copropriétaire en demeure.",
    )
    interest_start_date = fields.Date(
        string="Intérêts courent depuis",
        compute="_compute_interest",
        store=True,
    )
    interest_accrued = fields.Monetary(
        string="Intérêts courus",
        compute="_compute_interest",
        store=True,
        currency_field="currency_id",
        help="Art. 1617 C.c.Q. : intérêt au taux convenu, dû à compter de la "
             "demeure. Calculé sur le capital réellement dû période par "
             "période, les encaissements le faisant décroître au fil du temps.",
    )
    interest_rule = fields.Char(
        string="Règle des intérêts", compute="_compute_interest", store=True
    )
    interest_balance = fields.Monetary(
        string="Intérêts dus", compute="_compute_due", store=True,
        currency_field="currency_id",
    )
    total_due = fields.Monetary(
        string="Total dû", compute="_compute_due", store=True,
        currency_field="currency_id",
    )
    is_overdue = fields.Boolean(
        string="En défaut", compute="_compute_amount", store=True
    )
    days_overdue = fields.Integer(
        string="Jours de retard", compute="_compute_amount", store=True
    )

    _sql_constraints = [
        (
            "unique_unit_per_call",
            "UNIQUE(call_id, unit_id)",
            "Cette fraction figure déjà à cet appel.",
        ),
    ]

    @api.depends(
        "allocation_ids.amount_capital",
        "allocation_ids.amount_interest",
    )
    def _compute_received(self):
        for line in self:
            line.amount_received = sum(line.allocation_ids.mapped("amount_capital"))
            line.amount_interest_paid = sum(
                line.allocation_ids.mapped("amount_interest")
            )

    @api.depends(
        "amount",
        "call_id.due_date",
        "call_id.state",
        "demeure_date",
        "allocation_ids.amount_capital",
        "allocation_ids.date",
        "syndicat_id.late_interest_basis",
        "syndicat_id.late_interest_rate",
    )
    def _compute_interest(self):
        """Art. 1617, 1594 et 1595 C.c.Q.

        Les intérêts courent à compter de la DEMEURE, pas de l'échéance. Deux
        sources possibles, et pas d'autre : la déclaration de copropriété qui
        stipule que le seul écoulement du temps y constitue (art. 1594 al. 1),
        ou une demande extrajudiciaire écrite (art. 1595). À défaut, zéro.

        Le calcul suit le capital réellement dû période par période : un
        versement encaissé au sixième mois cesse de porter intérêt à cette
        date-là. Prendre le solde d'aujourd'hui sur toute la durée le
        sous-estimerait, et c'est un montant qu'un syndicat réclame à quelqu'un.
        """
        today = fields.Date.context_today(self)
        for line in self:
            syndicat = line.syndicat_id
            basis = syndicat.late_interest_basis or "none"
            rate = (syndicat.late_interest_rate or 0.0) / 100.0
            start = False
            if basis == "declaration_term":
                start = line.call_id.due_date
                rule = _(
                    "Art. 1594 al. 1 et 1617 C.c.Q. : la déclaration de "
                    "copropriété constitue en demeure par le seul écoulement "
                    "du temps. Les intérêts courent de l'échéance, à %(r).4f %% "
                    "l'an."
                ) % {"r": syndicat.late_interest_rate}
            elif basis == "demeure":
                start = line.demeure_date
                rule = (
                    _(
                        "Art. 1595 et 1617 C.c.Q. : intérêts à %(r).4f %% l'an "
                        "depuis la mise en demeure écrite."
                    )
                    % {"r": syndicat.late_interest_rate}
                    if start
                    else _(
                        "Art. 1595 C.c.Q. : aucune mise en demeure écrite n'est "
                        "consignée. Sans demeure, l'art. 1617 ne fait courir "
                        "aucun intérêt."
                    )
                )
            else:
                rule = _(
                    "Le syndicat ne porte pas d'intérêt sur les arrérages. "
                    "L'art. 1617 C.c.Q. suppose un taux convenu ou le taux "
                    "légal, et une demeure : le module n'invente ni l'un ni "
                    "l'autre."
                )
            line.interest_start_date = start or False
            line.interest_rule = rule
            if not start or rate <= 0 or start >= today:
                line.interest_accrued = 0.0
                continue
            line.interest_accrued = line._accrue_interest(start, today, rate)

    def _accrue_interest(self, start, end, rate):
        """Intérêt simple, en jours réels sur 365, sur le capital du moment."""
        self.ensure_one()
        events = sorted(
            (
                (allocation.date, allocation.amount_capital)
                for allocation in self.allocation_ids
                if allocation.amount_capital
            ),
            key=lambda pair: pair[0],
        )
        capital = self.amount
        cursor = start
        accrued = 0.0
        for paid_on, paid in events:
            moment = max(paid_on, start)
            if moment > cursor and capital > 0:
                accrued += capital * rate * (moment - cursor).days / 365.0
            if moment > cursor:
                cursor = moment
            capital -= paid
        if end > cursor and capital > 0:
            accrued += capital * rate * (end - cursor).days / 365.0
        return round(accrued, 2)

    def _is_due_on(self, date):
        """La contribution est-elle échue à cette date ? Art. 1572 al. 1."""
        self.ensure_one()
        due = self.call_id.due_date
        return bool(due) and due <= date

    def _split_payment(self, amount):
        """Répartit une somme entre intérêts puis capital. Art. 1570 C.c.Q."""
        self.ensure_one()
        owed_interest = max(round(self.interest_balance, 2), 0.0)
        on_interest = min(round(amount, 2), owed_interest)
        return {
            "amount_interest": on_interest,
            "amount_capital": round(amount - on_interest, 2),
        }

    @api.depends("balance", "interest_accrued", "amount_interest_paid")
    def _compute_due(self):
        """Le total dû vit à part du montant appelé, et ce n'est pas cosmétique.

        Les intérêts se calculent sur le capital appelé ; si le montant appelé
        dépendait à son tour des intérêts, la dépendance boucherait sur
        elle-même. Le total dû est donc en aval des deux.
        """
        for line in self:
            line.interest_balance = max(
                line.interest_accrued - line.amount_interest_paid, 0.0
            )
            line.total_due = line.balance + line.interest_balance

    @api.depends(
        "amount_common",
        "amount_restricted_maintenance",
        "amount_restricted_major",
        "amount_contingency",
        "amount_self_insurance",
        "amount_received",
        "call_id.due_date",
        "call_id.state",
    )
    def _compute_amount(self):
        today = fields.Date.context_today(self)
        for line in self:
            line.amount = (
                line.amount_common
                + line.amount_restricted_maintenance
                + line.amount_restricted_major
                + line.amount_contingency
                + line.amount_self_insurance
            )
            line.balance = line.amount - line.amount_received
            due = line.call_id.due_date
            overdue = (
                line.call_id.state == "issued"
                and bool(due)
                and due < today
                and line.balance > 0
            )
            line.is_overdue = overdue
            line.days_overdue = (today - due).days if overdue else 0

    @api.depends("unit_id.owner_ids")
    def _compute_owners(self):
        for line in self:
            line.owner_ids = line.unit_id.owner_ids

    @api.model
    def _cron_refresh_overdue(self):
        """Le défaut naît du passage d'une date, pas d'une écriture.

        Même raison d'être que les crons du socle et de la gouvernance : sans
        ce rafraîchissement quotidien, une contribution dont l'échéance vient
        d'expirer resterait « à jour » jusqu'à ce que quelqu'un rouvre l'appel,
        et un tableau des impayés afficherait zéro là où il y a un défaut.
        """
        today = fields.Date.context_today(self)
        stale = self.search(
            [
                ("is_overdue", "=", False),
                ("balance", ">", 0),
                ("call_id.state", "=", "issued"),
                ("call_id.due_date", "<", today),
            ]
        )
        # Les intérêts, eux, grossissent chaque jour sans qu'une ligne bascule :
        # toute contribution dont les intérêts ont commencé à courir et qui
        # n'est pas soldée est à recalculer, qu'elle soit déjà en défaut ou non.
        accruing = self.search(
            [
                ("interest_start_date", "!=", False),
                ("interest_start_date", "<", today),
                ("balance", ">", 0),
            ]
        )
        if stale:
            stale.modified(["amount_received"])
        if accruing:
            accruing.modified(["demeure_date"])
        return len(stale | accruing)
