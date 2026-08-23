"""Encaissements et imputation des paiements.

Le rapprochement d'un encaissement avec une contribution n'est pas un choix de
gestion : le Code civil dit dans quel ordre un paiement s'impute, et il le dit
en quatre articles qui se lisent ensemble.

- **Art. 1569** : « Le débiteur de plusieurs dettes a le droit d'indiquer,
  lorsqu'il paie, quelle dette il entend acquitter. » C'est d'abord le
  copropriétaire qui décide, pas le syndicat. Il ne peut cependant pas, sans le
  consentement du créancier, payer une dette non échue de préférence à une dette
  échue.
- **Art. 1570** : on ne peut pas imputer sur le capital de préférence aux
  intérêts, et « le paiement fait sur capital et intérêts, mais qui n'est point
  intégral, s'impute d'abord sur les intérêts ».
- **Art. 1571** : une quittance acceptée fige l'imputation.
- **Art. 1572**, et seulement « à défaut d'imputation par les parties » :
  d'abord sur la dette échue ; entre plusieurs dettes échues, sur celle que le
  débiteur a le plus d'intérêt à acquitter ; à intérêt égal, sur la plus
  ancienne, « mais si toutes les dettes sont échues en même temps, elle se fait
  proportionnellement ».

⚠️ L'imputation automatique ne descend pas jusqu'à l'alinéa 2 de l'art. 1572.
« Celle que le débiteur a, pour lors, le plus d'intérêt à acquitter » suppose de
connaître sa situation, ce qu'aucun logiciel ne constate seul. Le module
applique l'alinéa 1 et l'alinéa 3, dit qu'il s'arrête là, et laisse imputer à la
main quand un motif existe.

⚠️ Et le partage proportionnel entre dettes échues le même jour se fait au sou
près, par la même méthode du plus fort reste que la répartition d'un appel :
sans cela, une imputation laisserait des cents orphelins sur des contributions
que plus personne ne saurait solder.

La charge, elle, reste rattachée à la fraction (art. 1069). Un encaissement
porte donc le payeur pour information, et ce sont ses imputations qui disent
quelles fractions sont soldées.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .bf_property_allocation import allocate

PAYMENT_METHODS = [
    ("transfer", "Virement"),
    ("preauthorized", "Prélèvement préautorisé"),
    ("cheque", "Chèque"),
    ("cash", "Comptant"),
    ("notary", "Notaire (acte de vente)"),
    ("other", "Autre"),
]


class BfPropertyPayment(models.Model):
    _name = "bf.property.payment"
    _description = "Encaissement"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(
        string="Encaissement", required=True, default=lambda s: _("Nouveau"),
        tracking=True,
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
    payer_partner_id = fields.Many2one(
        "res.partner",
        string="Payeur",
        required=True,
        tracking=True,
        help="Qui a remis la somme. Ce n'est pas forcément un copropriétaire "
             "courant : au closing d'une vente, c'est souvent le notaire, et "
             "l'art. 1069 C.c.Q. rend l'acquéreur tenu des charges dues avant "
             "son acquisition.",
    )
    unit_id = fields.Many2one(
        "bf.property.unit",
        string="Fraction visée",
        domain="[('syndicat_id', '=', syndicat_id)]",
        tracking=True,
        help="Facultatif. Renseignée, elle borne l'imputation à cette "
             "fraction, ce qui est le cas d'un paiement fait au titre d'une "
             "vente. Laissée vide, l'imputation cherche parmi les fractions que "
             "le payeur détient aujourd'hui.",
    )
    date = fields.Date(
        string="Reçu le", required=True, default=fields.Date.context_today,
        tracking=True,
    )
    amount = fields.Monetary(
        string="Montant", required=True, currency_field="currency_id",
        tracking=True,
    )
    method = fields.Selection(
        PAYMENT_METHODS, string="Mode", default="transfer", required=True
    )
    reference = fields.Char(
        string="Référence",
        help="Numéro de chèque, référence du virement, minute du notaire. "
             "Sert aussi à relier les encaissements d'un même versement qui "
             "solde plusieurs fractions.",
    )
    imputation_mode = fields.Selection(
        [
            ("legal", "À défaut d'indication (art. 1572)"),
            ("debtor", "Indiquée par le copropriétaire (art. 1569)"),
        ],
        string="Imputation",
        default="legal",
        required=True,
        tracking=True,
        help="Art. 1569 C.c.Q. : le débiteur a le droit d'indiquer quelle "
             "dette il entend acquitter. L'ordre de l'art. 1572 ne s'applique "
             "qu'à défaut d'indication.",
    )
    creditor_consent = fields.Boolean(
        string="Le syndicat consent à l'imputation indiquée",
        tracking=True,
        help="Art. 1569 al. 2 C.c.Q. : le débiteur ne peut pas, sans le "
             "consentement du créancier, imputer son paiement sur une dette "
             "non encore échue de préférence à une dette échue.",
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("applied", "Imputé"),
            ("cancelled", "Annulé"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )
    allocation_ids = fields.One2many(
        "bf.property.payment.allocation", "payment_id", string="Imputations"
    )
    amount_allocated = fields.Monetary(
        string="Imputé", compute="_compute_allocated", store=True,
        currency_field="currency_id",
    )
    amount_unallocated = fields.Monetary(
        string="Reste à imputer", compute="_compute_allocated", store=True,
        currency_field="currency_id",
    )
    imputation_rule = fields.Char(
        string="Règle appliquée", readonly=True, copy=False
    )
    note = fields.Char(string="Note")

    _sql_constraints = [
        (
            "amount_positive",
            "CHECK(amount > 0)",
            "Un encaissement porte un montant supérieur à zéro.",
        ),
    ]

    @api.depends("allocation_ids.amount_total", "amount")
    def _compute_allocated(self):
        for payment in self:
            payment.amount_allocated = sum(
                payment.allocation_ids.mapped("amount_total")
            )
            payment.amount_unallocated = payment.amount - payment.amount_allocated

    @api.constrains(
        "allocation_ids", "amount", "unit_id", "creditor_consent", "date"
    )
    def _check_allocations(self):
        """Les trois contrôles se déclenchent des deux côtés.

        Une imputation se crée aussi bien depuis l'encaissement, par la
        one2many, que directement sur le modèle d'imputation, comme le fait
        l'imputation automatique. Les `constrains` d'un parent ne voient pas la
        seconde voie : le modèle d'imputation rappelle donc les mêmes contrôles
        sur son propre déclencheur.
        """
        self._check_not_over_allocated()
        self._check_allocations_scope()
        self._check_due_before_undue()

    def _check_not_over_allocated(self):
        for payment in self:
            allocated = sum(payment.allocation_ids.mapped("amount_total"))
            if allocated > payment.amount + 0.005:
                raise ValidationError(
                    _(
                        "Les imputations totalisent %(a).2f, soit plus que "
                        "l'encaissement de %(p).2f."
                    )
                    % {"a": allocated, "p": payment.amount}
                )

    def _check_allocations_scope(self):
        for payment in self:
            for allocation in payment.allocation_ids:
                line = allocation.line_id
                if line.syndicat_id != payment.syndicat_id:
                    raise ValidationError(
                        _("La contribution imputée appartient à un autre syndicat.")
                    )
                if payment.unit_id and line.unit_id != payment.unit_id:
                    raise ValidationError(
                        _(
                            "L'encaissement vise la fraction %(unit)s : il ne "
                            "s'impute pas sur la contribution d'une autre."
                        )
                        % {"unit": payment.unit_id.display_name}
                    )

    def _check_due_before_undue(self):
        """Art. 1569 al. 2 C.c.Q.

        Payer d'avance pendant qu'une dette échue traîne demande l'accord du
        créancier. Le module ne l'interdit pas, il exige que l'accord soit
        consigné : c'est le syndicat qui le donne, pas le logiciel.
        """
        for payment in self:
            if payment.creditor_consent or not payment.allocation_ids:
                continue
            touched = payment.allocation_ids.mapped("line_id")
            undue = touched.filtered(lambda l, p=payment: not l._is_due_on(p.date))
            if not undue:
                continue
            outstanding = payment._candidate_lines().filtered(
                lambda l, p=payment: l._is_due_on(p.date) and l.total_due > 0
            )
            if outstanding - touched:
                raise ValidationError(
                    _(
                        "Art. 1569 al. 2 C.c.Q. : cette imputation solde une "
                        "contribution non encore échue alors qu'une "
                        "contribution échue reste impayée. Il y faut le "
                        "consentement du syndicat, à cocher sur l'encaissement."
                    )
                )

    # ── Imputation ──

    def _candidate_lines(self):
        """Contributions sur lesquelles cet encaissement peut s'imputer.

        La fraction visée l'emporte quand elle est renseignée : un paiement fait
        au titre d'une vente solde les charges de CETTE fraction (art. 1069),
        que le payeur en soit propriétaire ou non. À défaut, on prend les
        fractions que le payeur détient aujourd'hui.
        """
        self.ensure_one()
        Line = self.env["bf.property.fund.call.line"]
        domain = [
            ("syndicat_id", "=", self.syndicat_id.id),
            ("call_id.state", "in", ("issued", "closed")),
        ]
        if self.unit_id:
            domain.append(("unit_id", "=", self.unit_id.id))
        else:
            # ⚠️ On passe par le registre de propriété et non par `owner_ids`.
            # Ce dernier est un many2many CALCULÉ STOCKÉ : une acquisition
            # saisie juste avant est correcte à la lecture mais pas encore
            # écrite dans la table de relation, et un `search` dessus rend
            # l'ancien propriétaire. Un encaissement irait alors sur les
            # fractions de la veille. Le registre, lui, porte des colonnes
            # ordinaires.
            ownerships = self.env["bf.property.ownership"].search(
                [
                    ("syndicat_id", "=", self.syndicat_id.id),
                    ("partner_id", "=", self.payer_partner_id.id),
                ]
            )
            units = ownerships.filtered("is_current").mapped("unit_id")
            domain.append(("unit_id", "in", units.ids))
        return Line.search(domain)

    def action_allocate(self):
        """Impute le reste à imputer selon l'ordre de l'art. 1572 C.c.Q."""
        for payment in self:
            if payment.state == "cancelled":
                raise UserError(_("Un encaissement annulé ne s'impute pas."))
            if payment.imputation_mode == "debtor":
                raise UserError(
                    _(
                        "Art. 1569 C.c.Q. : le copropriétaire a indiqué quelle "
                        "dette il acquitte. Saisissez ses imputations plutôt "
                        "que d'appliquer l'ordre supplétif de l'art. 1572."
                    )
                )
            payment._allocate_by_law()
        return True

    def _allocate_by_law(self):
        self.ensure_one()
        remaining = round(self.amount_unallocated, 2)
        if remaining <= 0:
            raise UserError(_("Cet encaissement est déjà entièrement imputé."))
        already = set(self.allocation_ids.mapped("line_id").ids)
        lines = self._candidate_lines().filtered(
            lambda l: l.id not in already and l.total_due > 0
        )
        if not lines:
            raise UserError(
                _(
                    "Aucune contribution transmise et impayée ne correspond à "
                    "cet encaissement. Vérifiez la fraction visée, ou le "
                    "payeur si elle est laissée vide."
                )
            )

        # Art. 1572 al. 1 : d'abord la dette échue. Ce qui n'est pas échu ne
        # vient qu'ensuite, et seulement si le syndicat y consent (al. 2 de
        # l'art. 1569, vérifié par une contrainte distincte).
        due = lines.filtered(lambda l, d=self.date: l._is_due_on(d))
        undue = lines - due
        # ⚠️ L'alinéa 2 de l'art. 1572 s'interpose « entre plusieurs dettes
        # échues » : il ne se pose que quand l'encaissement ne les couvre pas
        # toutes, et c'est justement là que le module s'arrête.
        stopped_short = len(due) > 1 and remaining < round(
            sum(due.mapped("total_due")), 2
        )
        rows = []

        # Les deux groupes se parcourent l'un après l'autre pour que
        # l'alinéa 1 se lise dans le code. ⚠️ Aucun test ne peut les
        # distinguer d'un parcours unique : une dette non échue a par
        # construction une échéance postérieure à toute dette échue, donc le
        # tri par date qui suit produit déjà le même ordre. La séparation est
        # gardée pour la lisibilité de l'article, pas pour son effet.
        for group in (due, undue):
            if remaining <= 0 or not group:
                continue
            # Art. 1572 al. 3 : à la plus anciennement échue, et
            # proportionnellement entre celles échues le même jour.
            by_date = {}
            for line in group:
                by_date.setdefault(line.call_id.due_date, self.env[line._name])
                by_date[line.call_id.due_date] |= line
            for due_date in sorted(by_date, key=lambda d: (d is None, d)):
                if remaining <= 0:
                    break
                batch = by_date[due_date]
                owed = {line.id: round(line.total_due, 2) for line in batch}
                total_owed = round(sum(owed.values()), 2)
                if len(batch) > 1 and remaining < total_owed:
                    # « elle se fait proportionnellement » : au sou près, par la
                    # méthode du plus fort reste, sinon des cents se perdent.
                    parts = allocate(
                        remaining, [(line_id, owed[line_id]) for line_id in owed]
                    )
                else:
                    # Une seule dette, ou de quoi les solder toutes : on sert
                    # dans l'ordre sans jamais distribuer plus que le reste.
                    running = remaining
                    parts = {}
                    for line in batch:
                        share = min(round(owed[line.id], 2), round(running, 2))
                        parts[line.id] = share
                        running -= share
                for line in batch:
                    share = round(parts.get(line.id, 0.0), 2)
                    if share <= 0:
                        continue
                    rows.append(dict(line._split_payment(share), line_id=line.id))
                    remaining = round(remaining - share, 2)

        if rows:
            self.env["bf.property.payment.allocation"].create(
                [dict(row, payment_id=self.id) for row in rows]
            )
        rule = _(
            "Art. 1572 C.c.Q. : imputé d'abord sur les dettes échues, puis de "
            "la plus ancienne à la plus récente, et proportionnellement entre "
            "celles échues le même jour. Art. 1570 : à l'intérieur d'une "
            "contribution, les intérêts avant le capital."
        )
        if stopped_short:
            rule += _(
                " ⚠️ L'alinéa 2 de l'art. 1572, « celle que le débiteur a le "
                "plus d'intérêt à acquitter », n'est pas appliqué : il suppose "
                "de connaître sa situation. Imputez à la main s'il existe un "
                "motif de préférer une contribution à une autre."
            )
        self.imputation_rule = rule
        self.message_post(body=rule)
        return True

    def action_apply(self):
        for payment in self:
            if not payment.allocation_ids:
                raise UserError(
                    _("Imputez l'encaissement avant de le porter au registre.")
                )
            payment.state = "applied"
        return True

    def action_reset(self):
        for payment in self:
            if payment.state == "applied":
                raise UserError(
                    _(
                        "Art. 1571 C.c.Q. : une imputation acceptée par le "
                        "copropriétaire ne se refait pas. Annulez "
                        "l'encaissement et saisissez-en un autre, pour que le "
                        "registre garde trace des deux."
                    )
                )
            payment.allocation_ids.unlink()
            payment.imputation_rule = False
        return True

    def action_cancel(self):
        for payment in self:
            payment.allocation_ids.unlink()
            payment.state = "cancelled"
            payment.message_post(
                body=_("Encaissement annulé : ses imputations sont retirées.")
            )
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name") in (None, "", _("Nouveau")):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "bf.property.payment"
                ) or _("Encaissement")
        return super().create(vals_list)


class BfPropertyPaymentAllocation(models.Model):
    _name = "bf.property.payment.allocation"
    _description = "Imputation d'un encaissement"
    _order = "payment_id, id"

    payment_id = fields.Many2one(
        "bf.property.payment",
        string="Encaissement",
        required=True,
        ondelete="cascade",
        index=True,
    )
    line_id = fields.Many2one(
        "bf.property.fund.call.line",
        string="Contribution",
        required=True,
        ondelete="restrict",
        index=True,
    )
    unit_id = fields.Many2one(
        related="line_id.unit_id", store=True, string="Fraction"
    )
    syndicat_id = fields.Many2one(
        related="payment_id.syndicat_id", store=True, string="Syndicat"
    )
    company_id = fields.Many2one(
        related="payment_id.company_id", store=True, string="Société"
    )
    currency_id = fields.Many2one(
        related="payment_id.currency_id", string="Devise"
    )
    date = fields.Date(related="payment_id.date", store=True, string="Reçu le")
    amount_interest = fields.Monetary(
        string="Sur les intérêts", currency_field="currency_id",
        help="Art. 1570 al. 2 C.c.Q. : un paiement partiel sur capital et "
             "intérêts s'impute d'abord sur les intérêts.",
    )
    amount_capital = fields.Monetary(
        string="Sur le capital", currency_field="currency_id"
    )
    amount_total = fields.Monetary(
        string="Imputé", compute="_compute_total", store=True,
        currency_field="currency_id",
    )

    _sql_constraints = [
        (
            "unique_line_per_payment",
            "UNIQUE(payment_id, line_id)",
            "Cette contribution figure déjà aux imputations de cet encaissement.",
        ),
        (
            "amounts_positive",
            "CHECK(amount_interest >= 0 AND amount_capital >= 0)",
            "Une imputation ne peut pas être négative.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        allocations = super().create(vals_list)
        allocations._invalidate_deprivation_state()
        return allocations

    def write(self, vals):
        result = super().write(vals)
        self._invalidate_deprivation_state()
        return result

    def unlink(self):
        self._invalidate_deprivation_state()
        return super().unlink()

    def _invalidate_deprivation_state(self):
        """L'art. 1094 se lit sur un registre qu'aucune dépendance ne relie ici.

        `deprivation_suggested` est calculé non stocké faute de chaîne de
        dépendances vers les encaissements, mais il reste EN CACHE dans la
        transaction. Sans cette invalidation, imputer un paiement puis relire
        une feuille de présence dans le même appel rendrait l'état d'avant, et
        l'assemblée priverait de son vote quelqu'un qui vient de payer.
        """
        self.env["bf.property.assembly.attendance"].invalidate_model(
            [
                "charges_overdue_amount",
                "charges_overdue_since",
                "charges_total_due",
                "deprivation_suggested",
            ]
        )
        self.env["bf.property.assembly"].invalidate_model(
            ["deprivation_candidate_count"]
        )

    @api.depends("amount_interest", "amount_capital")
    def _compute_total(self):
        for allocation in self:
            allocation.amount_total = (
                allocation.amount_interest + allocation.amount_capital
            )

    @api.constrains("amount_interest", "amount_capital", "line_id", "payment_id")
    def _check_against_the_payment(self):
        """Rappelle les contrôles de l'encaissement. Voir `_check_allocations`."""
        self.mapped("payment_id")._check_allocations()

    @api.constrains("amount_interest", "amount_capital", "line_id")
    def _check_interest_before_capital(self):
        """Art. 1570 al. 1 C.c.Q.

        « Le débiteur d'une dette qui porte intérêt ou produit des arrérages ne
        peut, sans le consentement du créancier, imputer le paiement qu'il fait
        sur le capital de préférence aux intérêts ou arrérages. » Le module
        laisse passer l'imputation sur le capital seulement quand les intérêts
        dus sont déjà couverts, ou quand le syndicat a consenti.
        """
        for allocation in self:
            if allocation.payment_id.creditor_consent:
                continue
            line = allocation.line_id
            others = line.allocation_ids - allocation
            owed_interest = round(
                line.interest_accrued - sum(others.mapped("amount_interest")), 2
            )
            if owed_interest <= 0.005:
                continue
            if allocation.amount_capital > 0 and (
                allocation.amount_interest + 0.005 < owed_interest
            ):
                raise ValidationError(
                    _(
                        "Art. 1570 C.c.Q. : %(i).2f d'intérêts restent dus sur "
                        "la contribution de %(unit)s. Un paiement s'impute "
                        "d'abord sur les intérêts, sauf consentement du "
                        "syndicat."
                    )
                    % {"i": owed_interest, "unit": line.unit_id.display_name}
                )

    @api.depends("payment_id", "line_id")
    def _compute_display_name(self):
        for allocation in self:
            allocation.display_name = _("%(payment)s → %(line)s") % {
                "payment": allocation.payment_id.name or "",
                "line": allocation.line_id.display_name or "",
            }
