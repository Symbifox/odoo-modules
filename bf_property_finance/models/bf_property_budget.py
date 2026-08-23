"""Budget annuel du syndicat et répartition des charges communes.

Deux règles commandent tout ce fichier, et l'une et l'autre se lisent de
travers dans la pratique courante.

**Qui arrête le budget.** Art. 1072 C.c.Q. : « Annuellement, le conseil
d'administration fixe, APRÈS CONSULTATION de l'assemblée des copropriétaires,
la contribution de ceux-ci aux charges communes ». L'assemblée n'adopte pas le
budget ; elle est consultée, et c'est le conseil qui fixe. La confusion est
répandue au point qu'on voit des procès-verbaux « adopter le budget » par un
vote. Le module suit le texte : la consultation est un préalable obligatoire,
la fixation appartient au conseil, et l'une ne peut pas se faire avant l'autre.

Même chose pour une contribution spéciale, art. 1072.1 : le conseil « doit
consulter l'assemblée des copropriétaires avant de décider » de celle-ci.

**Comment se répartissent les charges.** Art. 1064, tel que refait par la
Loi 16 (2019, c. 28, a. 32), pose TROIS régimes et non deux :

1. Règle générale, al. 1 : chacun contribue « en proportion de la valeur
   relative de sa fraction ».
2. Exception, al. 1 in fine : les copropriétaires « qui ont l'usage de parties
   communes à usage restreint contribuent seuls aux charges liées à
   L'ENTRETIEN ET AUX RÉPARATIONS COURANTES de ces parties ».
3. ⚠️ Al. 2 : « La déclaration de copropriété PEUT PRÉVOIR une répartition
   différente de la contribution des copropriétaires aux charges relatives aux
   RÉPARATIONS MAJEURES aux parties communes à usage restreint et au
   REMPLACEMENT de ces parties. »

Le troisième régime est celui qu'on encode de travers. « Les charges d'une
partie commune à usage restreint sont à ses bénéficiaires » est vrai de
l'entretien courant et FAUX des réparations majeures et du remplacement : pour
celles-là, la règle par défaut reste l'al. 1, donc TOUTES les fractions au
prorata, et il faut une stipulation de la déclaration pour en sortir. Refaire
la toiture d'une terrasse privative se répartit sur tout l'immeuble à moins que
la déclaration ne dise le contraire. Un module qui n'offrirait qu'un seul type
« usage restreint » facturerait la réfection à quelques copropriétaires qui ne
la doivent pas.

Le type de charge est donc à quatre valeurs, pas deux, et la dérogation de
l'al. 2 est une case distincte qui doit renvoyer à la déclaration.

⚠️ Un point que le texte ne règle pas et que le module tranche par défaut :
dans quelle proportion les bénéficiaires se partagent-ils entre eux une charge
d'usage restreint ? L'art. 1064 ne le dit pas. Le module applique la règle
générale à l'intérieur du groupe, soit au prorata de leurs quotes-parts
respectives. À confirmer en P2.3.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.tools.misc import formatLang

from .bf_property_allocation import allocate
from .bf_property_syndicat import CONTINGENCY_GENERAL_RATE


# Types de charge et régime de répartition. L'ordre suit celui de l'art. 1064.
CHARGE_TYPES = [
    ("common", "Charges communes générales"),
    ("restricted_maintenance", "Usage restreint : entretien et réparations courantes"),
    ("restricted_major", "Usage restreint : réparations majeures et remplacement"),
    ("contingency", "Fonds de prévoyance"),
    ("self_insurance", "Fonds d'auto-assurance"),
]
RESTRICTED_TYPES = ("restricted_maintenance", "restricted_major")


class BfPropertyBudget(models.Model):
    _name = "bf.property.budget"
    _description = "Budget annuel du syndicat"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(string="Exercice", required=True, tracking=True)
    syndicat_id = fields.Many2one(
        "bf.property.syndicat",
        string="Syndicat",
        required=True,
        ondelete="cascade",
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        related="syndicat_id.company_id", store=True, string="Société"
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", string="Devise"
    )
    date_start = fields.Date(string="Début de l'exercice", required=True, tracking=True)
    date_end = fields.Date(string="Fin de l'exercice", required=True, tracking=True)

    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("consulted", "Assemblée consultée"),
            ("fixed", "Fixé par le conseil"),
            ("notified", "Copropriétaires avisés"),
            ("closed", "Clôturé"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )
    consultation_assembly_id = fields.Many2one(
        "bf.property.assembly",
        string="Assemblée consultée",
        tracking=True,
        domain="[('syndicat_id', '=', syndicat_id)]",
        help="Art. 1072 C.c.Q. : le conseil d'administration fixe la "
             "contribution « après consultation de l'assemblée des "
             "copropriétaires ». L'assemblée n'adopte pas le budget, elle est "
             "consultée ; c'est le conseil qui le fixe.",
    )
    consultation_date = fields.Date(string="Consultée le", tracking=True)
    fixed_date = fields.Date(string="Fixé le", tracking=True)
    notice_date = fields.Date(
        string="Avis transmis le",
        tracking=True,
        help="Art. 1072 al. 3 C.c.Q. : le syndicat avise « sans délai » chaque "
             "copropriétaire du montant de ses contributions et de la date où "
             "elles sont exigibles. Le texte ne fixe aucun nombre de jours : le "
             "module signale un avis en souffrance, il n'invente pas "
             "d'échéance.",
    )
    notice_pending = fields.Boolean(
        string="Avis à transmettre", compute="_compute_notice_pending", store=True
    )

    line_ids = fields.One2many(
        "bf.property.budget.line", "budget_id", string="Postes budgétaires"
    )
    call_ids = fields.One2many(
        "bf.property.fund.call", "budget_id", string="Appels de fonds"
    )
    call_count = fields.Integer(compute="_compute_call_count")

    amount_common = fields.Monetary(
        string="Charges communes", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_restricted = fields.Monetary(
        string="Parties communes à usage restreint", compute="_compute_amounts",
        store=True, currency_field="currency_id",
    )
    amount_contingency = fields.Monetary(
        string="Fonds de prévoyance", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_self_insurance = fields.Monetary(
        string="Fonds d'auto-assurance", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_total = fields.Monetary(
        string="Contribution totale", compute="_compute_amounts", store=True,
        currency_field="currency_id",
        help="Art. 1072 C.c.Q. : la contribution aux charges communes comprend "
             "les sommes nécessaires à l'exploitation de l'immeuble ET les "
             "sommes à verser au fonds de prévoyance et au fonds "
             "d'auto-assurance. Les deux fonds ne sont pas des suppléments : "
             "ils font partie de la contribution.",
    )
    amount_called = fields.Monetary(
        string="Déjà appelé", compute="_compute_called", store=True,
        currency_field="currency_id",
        help="Somme des appels transmis ou clôturés. Les brouillons ne comptent "
             "pas : rien n'a encore été demandé aux copropriétaires.",
    )
    amount_collected = fields.Monetary(
        string="Encaissé", compute="_compute_called", store=True,
        currency_field="currency_id",
        help="Somme réellement reçue sur les appels de cet exercice, "
             "imputée en capital. Les intérêts n'y sont pas : ils ne "
             "financent aucun poste du budget.",
    )
    amount_outstanding = fields.Monetary(
        string="Appelé et non encaissé", compute="_compute_called", store=True,
        currency_field="currency_id",
    )
    amount_uncalled = fields.Monetary(
        string="Reste à appeler", compute="_compute_called", store=True,
        currency_field="currency_id",
        help="Contribution totale moins ce qui a été appelé. ⚠️ Ce reste n'est "
             "presque jamais nul par accident : quatre appels de 25 %% d'un "
             "budget qui ne se divise pas par quatre laissent des cents "
             "derrière eux, chaque appel étant arrondi pour lui-même. Le "
             "module montre l'écart plutôt que de le répartir en douce : c'est "
             "au dernier appel de l'exercice de le reprendre.",
    )
    contingency_reference = fields.Monetary(
        string="Repère du fonds de prévoyance",
        compute="_compute_amounts",
        store=True,
        currency_field="currency_id",
        help="Somme que le fonds de prévoyance devrait recevoir pour cet "
             "exercice. Elle vient de l'étude quand il y en a une, du plancher "
             "du promoteur (0,5 %% de la valeur de reconstruction, art. 1071 "
             "al. 4 C.c.Q.), ou du plancher transitoire du syndicat (5 %% des "
             "contributions aux charges communes, Loi 16, art. 153 al. 2). Ce "
             "dernier est une proportion de CET exercice : il ne peut se "
             "chiffrer que sur le budget.",
    )
    contingency_gap = fields.Monetary(
        string="Écart au repère du fonds de prévoyance",
        compute="_compute_amounts",
        store=True,
        currency_field="currency_id",
        help="Somme budgétée au fonds de prévoyance, moins le repère. Négatif "
             "quand le budget verse moins que ce que l'étude recommande, ou "
             "moins que le plancher applicable.",
    )
    contingency_warning = fields.Char(
        string="Fonds de prévoyance", compute="_compute_amounts", store=True
    )

    _sql_constraints = [
        (
            "dates_ordered",
            "CHECK(date_end > date_start)",
            "La fin de l'exercice doit suivre son début.",
        ),
    ]

    @api.depends("line_ids.amount", "line_ids.charge_type",
                 "syndicat_id.contingency_reference",
                 "syndicat_id.contingency_basis")
    def _compute_amounts(self):
        # ⚠️ Le repère du plancher transitoire du syndicat se calcule ICI et
        # non sur le syndicat, parce qu'il est une proportion des contributions
        # de l'exercice. Le porter sur le syndicat le ferait dépendre du total
        # budgété, que ce même calcul compare ensuite au repère : la dépendance
        # boucherait sur elle-même.
        for budget in self:
            totals = dict.fromkeys([code for code, _label in CHARGE_TYPES], 0.0)
            for line in budget.line_ids:
                totals[line.charge_type] += line.amount
            budget.amount_common = totals["common"]
            budget.amount_restricted = (
                totals["restricted_maintenance"] + totals["restricted_major"]
            )
            budget.amount_contingency = totals["contingency"]
            budget.amount_self_insurance = totals["self_insurance"]
            budget.amount_total = sum(totals.values())

            if budget.syndicat_id.contingency_basis == "general":
                # Loi 16, art. 153 al. 2 : « au moins 5 % des contributions des
                # copropriétaires aux charges communes ». L'assiette est la
                # contribution ENTIÈRE : l'art. 1072 y range expressément les
                # versements au fonds de prévoyance et au fonds
                # d'auto-assurance, qui ne sont pas des suppléments.
                # ⚠️ Lecture portée à P2.3 : la doctrine d'avant la Loi 16
                # calculait souvent sur le seul budget d'exploitation.
                reference = budget.amount_total * CONTINGENCY_GENERAL_RATE
            else:
                reference = budget.syndicat_id.contingency_reference
            budget.contingency_reference = reference
            budget.contingency_gap = budget.amount_contingency - reference
            if budget.syndicat_id.contingency_basis == "unknown":
                budget.contingency_warning = (
                    budget.syndicat_id.contingency_rule
                    or _(
                        "Aucune étude du fonds de prévoyance et aucune valeur "
                        "de reconstruction : rien ne permet de dire si la "
                        "somme budgétée répond à l'art. 1071."
                    )
                )
            elif budget.contingency_gap < 0:
                budget.contingency_warning = _(
                    "Le budget verse %(budgeted)s au fonds de prévoyance pour "
                    "un repère de %(reference)s. %(rule)s"
                ) % {
                    "budgeted": budget._report_amount(budget.amount_contingency),
                    "reference": budget._report_amount(reference),
                    "rule": budget.syndicat_id.contingency_rule or "",
                }
            else:
                budget.contingency_warning = False

    @api.depends("state", "notice_date")
    def _compute_notice_pending(self):
        for budget in self:
            budget.notice_pending = budget.state == "fixed" and not budget.notice_date

    @api.depends("call_ids")
    def _compute_call_count(self):
        for budget in self:
            budget.call_count = len(budget.call_ids)

    @api.depends(
        "call_ids.amount_total",
        "call_ids.amount_received",
        "call_ids.state",
        "amount_total",
    )
    def _compute_called(self):
        for budget in self:
            issued = budget.call_ids.filtered(
                lambda call: call.state in ("issued", "closed")
            )
            budget.amount_called = sum(issued.mapped("amount_total"))
            # ⚠️ L'encaissé se compte en CAPITAL. Les intérêts de retard entrent
            # bien dans les coffres du syndicat, mais ils ne financent aucun
            # poste du budget : les compter ici ferait croire un exercice mieux
            # financé qu'il ne l'est, et d'autant plus qu'il a mal été payé.
            budget.amount_collected = sum(issued.mapped("amount_received"))
            budget.amount_outstanding = (
                budget.amount_called - budget.amount_collected
            )
            budget.amount_uncalled = budget.amount_total - budget.amount_called

    def _report_amount(self, value):
        """Un montant lisible. Un zéro s'imprime zéro, jamais un tiret."""
        self.ensure_one()
        return formatLang(self.env, value or 0.0, currency_obj=self.currency_id)

    def _report_lines(self):
        """Le prévu, l'appelé et l'encaissé, poste par poste.

        ⚠️ « Budget contre réel » ne peut pas vouloir dire ici « prévu contre
        DÉPENSÉ ». Le module ne tient aucune dépense : ni facture, ni
        fournisseur, ni comptabilité, et il ne dépend pas de `account`. Ce qu'il
        sait est le cycle de la contribution, qui est précisément ce que
        l'art. 1072 confie au conseil : ce qui a été fixé, ce qui a été appelé,
        ce qui est rentré. Prétendre davantage serait mentir sur ce qu'un
        tableau montre.
        """
        self.ensure_one()
        issued = self.call_ids.filtered(
            lambda call: call.state in ("issued", "closed")
        )
        planned = dict.fromkeys([code for code, _label in CHARGE_TYPES], 0.0)
        for line in self.line_ids:
            planned[line.charge_type] += line.amount
        called = dict.fromkeys(planned, 0.0)
        for line in issued.mapped("line_ids"):
            for code, _label in CHARGE_TYPES:
                called[code] += line["amount_%s" % code]
        # L'encaissement s'impute sur la contribution d'une fraction, pas sur un
        # poste : le règlement des art. 1569 à 1572 ne connaît pas les postes.
        # On répartit donc l'encaissé au prorata de l'appelé, et on le dit.
        collected_total = sum(issued.mapped("amount_received"))
        called_total = sum(called.values())
        rows = []
        for code, label in CHARGE_TYPES:
            share = (
                collected_total * called[code] / called_total
                if called_total
                else 0.0
            )
            rows.append(
                {
                    "code": code,
                    "label": label,
                    "planned": planned[code],
                    "called": called[code],
                    "collected": round(share, 2),
                    "uncalled": planned[code] - called[code],
                }
            )
        return rows

    @api.constrains("date_start", "date_end", "syndicat_id")
    def _check_no_overlap(self):
        """Un exercice par période. Art. 1072 : la contribution se fixe « annuellement ».

        Deux budgets qui se chevauchent rendraient la répartition ambiguë pour
        les dates communes, et un appel de fonds ne saurait plus de quel budget
        il tire ses montants.
        """
        for budget in self:
            clash = self.search(
                [
                    ("id", "!=", budget.id),
                    ("syndicat_id", "=", budget.syndicat_id.id),
                    ("date_start", "<=", budget.date_end),
                    ("date_end", ">=", budget.date_start),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    _(
                        "L'exercice « %(clash)s » couvre déjà une partie de "
                        "cette période, du %(start)s au %(end)s."
                    )
                    % {
                        "clash": clash.name,
                        "start": clash.date_start,
                        "end": clash.date_end,
                    }
                )

    @api.constrains("consultation_assembly_id", "syndicat_id")
    def _check_consultation_assembly(self):
        for budget in self:
            assembly = budget.consultation_assembly_id
            if assembly and assembly.syndicat_id != budget.syndicat_id:
                raise ValidationError(
                    _("L'assemblée consultée appartient à un autre syndicat.")
                )

    # ── Répartition ──

    def _allocation_table(self, share=1.0):
        """Rend {fraction: {type de charge: montant}} pour la part demandée.

        Chaque poste est réparti séparément, puis les parts s'additionnent par
        fraction. Réparti poste par poste et non sur le total, parce que les
        assiettes diffèrent : le total n'a pas d'assiette commune.
        """
        self.ensure_one()
        table = {}
        for line in self.line_ids:
            units = line._allocation_units()
            if not units:
                continue
            weights = [(unit.id, unit.quote_part) for unit in units]
            for unit_id, amount in allocate(line.amount * share, weights).items():
                bucket = table.setdefault(unit_id, dict.fromkeys(
                    [code for code, _label in CHARGE_TYPES], 0.0
                ))
                bucket[line.charge_type] += amount
        return table

    # ── Cycle de vie (art. 1072) ──

    def action_consult(self):
        for budget in self:
            if not budget.consultation_assembly_id:
                raise UserError(
                    _(
                        "Indiquez l'assemblée consultée. Art. 1072 C.c.Q. : le "
                        "conseil fixe la contribution après consultation de "
                        "l'assemblée des copropriétaires."
                    )
                )
            budget.consultation_date = (
                budget.consultation_assembly_id.date.date()
                if budget.consultation_assembly_id.date
                else fields.Date.context_today(budget)
            )
            budget.state = "consulted"
        return True

    def action_fix(self):
        """Le conseil arrête la contribution. Jamais avant la consultation."""
        for budget in self:
            if budget.state == "draft":
                raise UserError(
                    _(
                        "L'assemblée n'a pas été consultée. Art. 1072 C.c.Q. : "
                        "le conseil d'administration fixe la contribution "
                        "« après consultation de l'assemblée des "
                        "copropriétaires ». L'ordre des deux gestes est dans le "
                        "texte."
                    )
                )
            if not budget.line_ids:
                raise UserError(_("Ce budget n'a aucun poste."))
            budget.fixed_date = fields.Date.context_today(budget)
            budget.state = "fixed"
        return True

    def action_notify(self):
        for budget in self:
            if budget.state != "fixed":
                raise UserError(
                    _("Le budget doit être fixé par le conseil avant d'être avisé.")
                )
            budget.notice_date = fields.Date.context_today(budget)
            budget.state = "notified"
            budget.message_post(
                body=_(
                    "Avis de contribution transmis aux copropriétaires "
                    "(art. 1072 al. 3 C.c.Q.)."
                )
            )
        return True

    def action_close(self):
        """Clore un exercice, mais pas un exercice qui n'a jamais été fixé.

        Art. 1072 C.c.Q. : le cycle commence quand le conseil FIXE la
        contribution, après consultation de l'assemblée. Clore un budget resté
        en brouillon ou tout juste consulté affirme qu'un exercice s'est
        déroulé alors que rien n'a été décidé. Reclore un exercice déjà clos ne
        change rien et ne lève rien.
        """
        for budget in self:
            if budget.state in ("draft", "consulted"):
                raise UserError(
                    _(
                        "L'exercice « %(name)s » n'a pas été fixé par le "
                        "conseil : il est %(state)s. Art. 1072 C.c.Q. : la "
                        "contribution se fixe après consultation de "
                        "l'assemblée. Un exercice qui n'a jamais été fixé ne "
                        "se clôt pas."
                    )
                    % {
                        "name": budget.name,
                        "state": dict(
                            self._fields["state"].selection
                        )[budget.state].lower(),
                    }
                )
        self.write({"state": "closed"})
        return True

    def action_reset_to_draft(self):
        """Rouvrir un exercice, sauf si des contributions sont déjà transmises.

        ⚠️ C'est la garde qui compte ici. Un appel de fonds à l'état
        « transmis » a été porté à la connaissance des copropriétaires
        (art. 1072 al. 3), et il fixe le montant que chacun doit. Ramener le
        budget au brouillon rouvrirait à la modification l'assiette de ce qui a
        déjà été réclamé, sans que l'appel bouge : le module afficherait alors
        un exercice et des appels qui ne se répondent plus. Même principe que
        l'état des charges de l'art. 1069, qui ne se recalcule pas une fois
        fourni.
        """
        for budget in self:
            sent = budget.call_ids.filtered(lambda c: c.state != "draft")
            if sent:
                raise UserError(
                    _(
                        "L'exercice « %(name)s » porte %(count)d appel(s) déjà "
                        "transmis : %(calls)s. Art. 1072 al. 3 C.c.Q. : les "
                        "copropriétaires ont été avisés du montant de leurs "
                        "contributions. Reprenez l'appel avant de rouvrir "
                        "l'exercice."
                    )
                    % {
                        "name": budget.name,
                        "count": len(sent),
                        "calls": ", ".join(sent.mapped("name")),
                    }
                )
        self.write({"state": "draft"})
        return True

    def action_view_calls(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Appels de fonds"),
            "res_model": "bf.property.fund.call",
            "view_mode": "list,form",
            "domain": [("budget_id", "=", self.id)],
            "context": {"default_budget_id": self.id},
        }


class BfPropertyBudgetLine(models.Model):
    _name = "bf.property.budget.line"
    _description = "Poste budgétaire"
    _order = "budget_id, sequence, id"

    budget_id = fields.Many2one(
        "bf.property.budget",
        string="Budget",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(string="Ordre", default=10)
    name = fields.Char(string="Poste", required=True)
    company_id = fields.Many2one(
        related="budget_id.company_id", store=True, string="Société"
    )
    currency_id = fields.Many2one(
        related="budget_id.currency_id", string="Devise"
    )
    syndicat_id = fields.Many2one(
        related="budget_id.syndicat_id", store=True, string="Syndicat"
    )
    charge_type = fields.Selection(
        CHARGE_TYPES,
        string="Nature",
        default="common",
        required=True,
        help="Art. 1064 C.c.Q. Les deux natures « usage restreint » ne se "
             "répartissent pas de la même façon : l'entretien courant est à la "
             "charge des seuls bénéficiaires, les réparations majeures et le "
             "remplacement suivent la règle générale à moins que la "
             "déclaration n'en dispose autrement.",
    )
    common_area_id = fields.Many2one(
        "bf.property.common.area",
        string="Partie commune à usage restreint",
        domain="[('syndicat_id', '=', syndicat_id), ('area_type', '=', 'restricted')]",
        ondelete="restrict",
    )
    declaration_derogation = fields.Boolean(
        string="La déclaration prévoit une répartition différente",
        help="Art. 1064 al. 2 C.c.Q. : sans stipulation de la déclaration de "
             "copropriété, les réparations majeures et le remplacement d'une "
             "partie commune à usage restreint se répartissent sur TOUTES les "
             "fractions, comme n'importe quelle charge commune. Cochez "
             "seulement si la déclaration porte une clause en ce sens, et "
             "citez-la à la note.",
    )
    derogation_reference = fields.Char(
        string="Clause de la déclaration",
        help="Article de la déclaration de copropriété qui prévoit la "
             "répartition différente.",
    )
    amount = fields.Monetary(
        string="Montant", currency_field="currency_id", required=True
    )
    allocation_rule = fields.Char(
        string="Répartition", compute="_compute_allocation", store=True
    )
    allocation_unit_count = fields.Integer(
        string="Fractions visées", compute="_compute_allocation", store=True
    )
    note = fields.Char(string="Note")

    _sql_constraints = [
        (
            "amount_positive",
            "CHECK(amount >= 0)",
            "Un poste budgétaire ne peut pas porter un montant négatif.",
        ),
    ]

    def _allocation_units(self):
        """Assiette du poste : les fractions sur lesquelles il se répartit."""
        self.ensure_one()
        everyone = self.budget_id.syndicat_id.unit_ids.filtered("active")
        if self.charge_type == "restricted_maintenance":
            return self.common_area_id.restricted_unit_ids.filtered("active")
        if self.charge_type == "restricted_major" and self.declaration_derogation:
            return self.common_area_id.restricted_unit_ids.filtered("active")
        return everyone

    @api.depends(
        "charge_type",
        "declaration_derogation",
        "common_area_id",
        "common_area_id.restricted_unit_ids",
        "budget_id.syndicat_id.unit_ids",
        "budget_id.syndicat_id.unit_ids.active",
    )
    def _compute_allocation(self):
        for line in self:
            line.allocation_unit_count = len(line._allocation_units())
            if line.charge_type == "restricted_maintenance":
                line.allocation_rule = _(
                    "Art. 1064 al. 1 in fine C.c.Q. : entretien et réparations "
                    "courantes d'une partie commune à usage restreint. Les "
                    "copropriétaires qui en ont l'usage contribuent seuls, au "
                    "prorata de leurs quotes-parts respectives."
                )
            elif line.charge_type == "restricted_major":
                if line.declaration_derogation:
                    line.allocation_rule = _(
                        "Art. 1064 al. 2 C.c.Q. : la déclaration de "
                        "copropriété prévoit une répartition différente, la "
                        "charge est portée aux seules fractions bénéficiaires."
                    )
                else:
                    line.allocation_rule = _(
                        "Art. 1064 al. 2 C.c.Q. : réparations majeures ou "
                        "remplacement d'une partie commune à usage restreint. "
                        "SANS stipulation de la déclaration, ces charges "
                        "suivent la règle générale et se répartissent sur "
                        "TOUTES les fractions, non sur les seuls bénéficiaires."
                    )
            elif line.charge_type == "contingency":
                line.allocation_rule = _(
                    "Art. 1071 et 1072 C.c.Q. : sommes à verser au fonds de "
                    "prévoyance, comprises dans la contribution aux charges "
                    "communes et réparties comme elle."
                )
            elif line.charge_type == "self_insurance":
                line.allocation_rule = _(
                    "Art. 1071.1 et 1072 C.c.Q. : sommes à verser au fonds "
                    "d'auto-assurance, comprises dans la contribution aux "
                    "charges communes et réparties comme elle."
                )
            else:
                line.allocation_rule = _(
                    "Art. 1064 al. 1 C.c.Q. : en proportion de la valeur "
                    "relative de chaque fraction."
                )

    @api.constrains("charge_type", "common_area_id")
    def _check_common_area(self):
        for line in self:
            if line.charge_type in RESTRICTED_TYPES and not line.common_area_id:
                raise ValidationError(
                    _(
                        "« %s » vise une partie commune à usage restreint : "
                        "indiquez laquelle. Sans elle, le module ne sait pas "
                        "qui en a l'usage."
                    )
                    % line.name
                )
            if line.charge_type not in RESTRICTED_TYPES and line.common_area_id:
                raise ValidationError(
                    _(
                        "« %s » n'est pas une charge d'usage restreint : "
                        "retirez la partie commune."
                    )
                    % line.name
                )
            if line.common_area_id and line.common_area_id.area_type != "restricted":
                raise ValidationError(
                    _(
                        "« %s » est une partie commune générale. Ses charges se "
                        "répartissent sur toutes les fractions."
                    )
                    % line.common_area_id.name
                )
            if (
                line.common_area_id
                and line.common_area_id.syndicat_id != line.budget_id.syndicat_id
            ):
                raise ValidationError(
                    _("Cette partie commune appartient à un autre syndicat.")
                )

    @api.constrains("declaration_derogation", "charge_type")
    def _check_derogation(self):
        """La dérogation de l'al. 2 ne vise que les réparations majeures.

        Cochée ailleurs, elle n'aurait aucun effet et laisserait croire à une
        répartition qui n'a pas lieu.
        """
        for line in self:
            if line.declaration_derogation and line.charge_type != "restricted_major":
                raise ValidationError(
                    _(
                        "La dérogation de l'art. 1064 al. 2 C.c.Q. ne vise que "
                        "les réparations majeures et le remplacement d'une "
                        "partie commune à usage restreint. Elle n'a pas d'objet "
                        "sur « %s »."
                    )
                    % line.name
                )

    @api.onchange("charge_type")
    def _onchange_charge_type(self):
        if self.charge_type not in RESTRICTED_TYPES:
            self.common_area_id = False
        if self.charge_type != "restricted_major":
            self.declaration_derogation = False
            self.derogation_reference = False
