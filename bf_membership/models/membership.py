from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

STATES = [
    ("draft", "Demande"),
    ("waiting", "À payer"),
    ("active", "En règle"),
    ("expired", "Échue"),
    ("withdrawn", "Retirée"),
    ("refused", "Refusée"),
]
PAYMENT_STATES = [
    ("to_pay", "À payer"),
    ("paid", "Payé"),
    ("exempt", "Exempté"),
]
PAYMENT_SOURCES = [
    ("invoice", "Facture"),
    ("online", "Paiement en ligne"),
    ("zeffy", "Zeffy"),
    ("stripe", "Stripe"),
    ("cheque", "Chèque"),
    ("cash", "Comptant"),
    ("transfer", "Virement"),
    ("platform", "Plateforme externe"),
    ("other", "Autre"),
]
# Les rappels, dans l'ordre où ils partent. La clé est stockée sur l'adhésion
# pour qu'un rappel ne parte jamais deux fois.
REMINDER_STAGES = [
    ("first", "Premier rappel"),
    ("second", "Second rappel"),
    ("due", "Échéance"),
    ("grace_end", "Fin du délai de grâce"),
    ("done", "Terminé"),
]
LIVE_STATES = ("draft", "waiting", "active")
IDENTITY_FIELDS = {"partner_id", "type_id", "company_id"}
# Champs « de bureau » que seul le code du module écrit (en superutilisateur),
# à la création comme après : décision, retrait, rappels, trace d'import, lien
# de renouvellement. Les écrire à la main falsifierait l'historique du registre.
OFFICE_LOCKED = {
    "decided_by_id", "decision_date", "withdrawal_date", "withdrawal_reason",
    "reminder_stage", "source", "external_ref", "renewal_of_id",
}
# Clés de contexte qui effaceraient la trace d'une modification : ignorées hors
# superutilisateur, le suivi au fil fait partie du registre.
NO_TRACE_KEYS = ("tracking_disable", "mail_notrack", "mail_create_nolog")


def keep_trace(records):
    """Les mêmes enregistrements, sans les clés qui feraient taire le suivi,
    sauf quand le superutilisateur agit lui-même (tâche planifiée, installation).

    🔴 La garde porte sur la personne qui agit, pas sur `env.su` : les actions
    écrivent en sudo APRÈS le contrôle des droits, et gardent le contexte de
    l'appel ; un `mail_notrack` passé à « accepter » ou « retirer » effacerait
    sinon le suivi de l'état. Les greffons l'importent pour leurs modèles suivis.
    """
    if records.env.user._is_superuser() or not any(k in records.env.context for k in NO_TRACE_KEYS):
        return records
    return records.with_context({k: v for k, v in records.env.context.items() if k not in NO_TRACE_KEYS})
# Champs qu'aucune personne ne fixe à la création, ni par les valeurs ni par un
# `default_*` du contexte : seul le code du module les pose, en superutilisateur.
PROTECTED_ON_CREATE = {
    "state", "decided_by_id", "decision_date", "withdrawal_date", "withdrawal_reason",
    "reminder_stage", "source", "external_ref", "renewal_of_id", "ever_settled",
}


class Membership(models.Model):
    """L'adhésion d'un membre pour une période.

    🔴 Deux états, pas un. `state` dit où en est l'adhésion (demandée, acceptée,
    en règle, échue, retirée, refusée) ; `payment_state` dit si la cotisation
    est réglée. Le module `membership` d'Odoo n'a que le second, calculé depuis
    une facture Odoo : une association qui encaisse par Zeffy, par Stripe ou par
    chèque n'aurait aucun membre. Ici le paiement se NOTE, quelle que soit sa
    source, et la facture est un greffon.

    🔴 Une adhésion ne se supprime pas une fois acceptée. L'article 104 de la Loi
    sur les compagnies veut au livre toutes les personnes qui sont OU ONT ÉTÉ
    membres : un départ se dit par l'état « retirée », jamais par une
    suppression, et le contact reste.
    """

    _name = "bf.membership"
    _description = "Adhésion"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"
    _rec_names_search = ["partner_id.name", "partner_id.member_number"]

    partner_id = fields.Many2one(
        "res.partner", string="Membre", required=True, index=True,
        ondelete="restrict", tracking=True,
    )
    member_number = fields.Char(related="partner_id.member_number", string="N° de membre")
    is_company = fields.Boolean(related="partner_id.is_company")
    type_id = fields.Many2one(
        "bf.membership.type", string="Catégorie", required=True, index=True,
        ondelete="restrict", tracking=True,
        domain="[('company_id', '=', company_id)]",
    )
    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True, tracking=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    voting = fields.Boolean(related="type_id.voting", string="Droit de vote")

    date_start = fields.Date(
        string="Début", required=True, tracking=True,
        default=fields.Date.context_today,
    )
    date_end = fields.Date(
        string="Fin", tracking=True, store=True, readonly=False,
        compute="_compute_date_end",
        help="Calculée depuis la catégorie, et modifiable : une adhésion de "
             "fondation ou une prolongation décidée par le conseil s'écrivent "
             "ici.",
    )
    period_label = fields.Char(string="Période", compute="_compute_period_label")

    state = fields.Selection(
        STATES, string="Adhésion", required=True, default="draft",
        index=True, tracking=True, copy=False,
    )
    payment_state = fields.Selection(
        PAYMENT_STATES, string="Paiement", required=True, default="to_pay",
        index=True, tracking=True, copy=False,
    )
    payment_source = fields.Selection(PAYMENT_SOURCES, string="Payé par", tracking=True, copy=False)
    payment_date = fields.Date(string="Date du paiement", tracking=True, copy=False)
    payment_reference = fields.Char(string="Référence du paiement", copy=False, tracking=True,
                                    groups="bf_membership.group_membership_user")
    amount = fields.Monetary(
        string="Cotisation", tracking=True, store=True, readonly=False,
        compute="_compute_amount",
    )

    # 🔴 Les champs « de bureau » sont réservés aux agents : le portail donne au
    # membre le droit de LIRE ses adhésions, et ce droit passe aussi par les
    # appels RPC, champ par champ.
    decided_by_id = fields.Many2one("res.users", string="Décision par", readonly=True, copy=False,
                                    groups="bf_membership.group_membership_user")
    decision_date = fields.Date(string="Date de la décision", readonly=True, copy=False)
    decision_note = fields.Text(string="Motif de la décision", copy=False, tracking=True,
                                groups="bf_membership.group_membership_user")

    withdrawal_date = fields.Date(string="Date du retrait", readonly=True, copy=False, tracking=True)
    withdrawal_reason = fields.Text(string="Motif du retrait", readonly=True, copy=False, groups="bf_membership.group_membership_user")

    renewal_of_id = fields.Many2one(
        "bf.membership", string="Renouvelle", index=True, copy=False,
        ondelete="set null",
    )
    renewal_ids = fields.One2many("bf.membership", "renewal_of_id", string="Renouvellements")
    reminder_stage = fields.Selection(
        REMINDER_STAGES, string="Dernier rappel", copy=False, readonly=True,
        groups="bf_membership.group_membership_user",
        help="Le dernier rappel parti pour cette adhésion. Un rappel ne part "
             "jamais deux fois, et une adhésion importée déjà échue naît à "
             "« Terminé » : l'import ne réveille personne.",
    )

    source = fields.Char(
        string="Source de l'import", copy=False, readonly=True,
        groups="bf_membership.group_membership_user",
        help="La liste d'où vient l'adhésion (Zeffy, plateforme nationale, "
             "Excel). Vide quand elle a été saisie ici.",
    )
    external_ref = fields.Char(
        string="Référence externe", copy=False, index=True,
        groups="bf_membership.group_membership_user",
        help="L'identifiant de la ligne dans la source. Réimporter la même "
             "liste met à jour l'adhésion au lieu d'en créer une seconde.",
    )
    note = fields.Html(string="Notes", groups="bf_membership.group_membership_user")

    ever_settled = fields.Boolean(
        string="A déjà fait quelqu'un membre", readonly=True, copy=False,
        groups="bf_membership.group_membership_user",
        help="Posé à la première mise en règle et jamais retiré. C'est lui, et non "
             "l'état du jour, qui fige le membre et la catégorie : remettre le "
             "paiement « à payer » ne rouvre pas l'adhésion à une autre personne.",
    )
    is_current = fields.Boolean(
        string="En règle aujourd'hui", compute="_compute_is_current",
        search="_search_is_current",
    )

    _sql_constraints = [
        ("external_ref_uniq", "unique(company_id, source, external_ref)",
         "Cette ligne de cette source a déjà été importée."),
    ]

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------

    @api.depends("type_id", "date_start")
    def _compute_date_end(self):
        for rec in self:
            if rec.type_id and rec.date_start:
                rec.date_end = rec.type_id._period_end(rec.date_start)
            else:
                rec.date_end = False

    @api.depends("type_id")
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.type_id.fee if rec.type_id else 0.0

    @api.depends("type_id", "date_start", "date_end")
    def _compute_period_label(self):
        for rec in self:
            rec.period_label = rec.type_id._period_label(rec.date_start, rec.date_end) if rec.type_id else ""

    @api.depends("partner_id", "period_label")
    def _compute_display_name(self):
        for rec in self:
            parts = [rec.partner_id.name or ""]
            if rec.period_label:
                parts.append(rec.period_label)
            rec.display_name = " · ".join(p for p in parts if p)

    @api.depends("state", "date_start", "date_end")
    def _compute_is_current(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_current = rec._covers(today)

    def _search_is_current(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Recherche non prise en charge."))
        today = fields.Date.context_today(self)
        domain = [
            ("state", "=", "active"),
            ("date_start", "<=", today),
            "|", ("date_end", "=", False), ("date_end", ">=", today),
        ]
        if (operator == "=") == value:
            return domain
        return [("id", "not in", self._search(domain))]

    def _covers(self, day):
        self.ensure_one()
        return (self.state == "active" and self.date_start and self.date_start <= day
                and (not self.date_end or self.date_end >= day))

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for rec in self:
            if rec.date_end and rec.date_start and rec.date_end < rec.date_start:
                raise ValidationError(_("L'adhésion ne peut pas finir avant de commencer."))

    @api.constrains("partner_id", "type_id")
    def _check_member_kind(self):
        for rec in self:
            kind = rec.type_id.member_kind
            if kind == "person" and rec.partner_id.is_company:
                raise ValidationError(_(
                    "La catégorie « %(type)s » est réservée aux personnes ; %(name)s est une organisation.",
                    type=rec.type_id.name, name=rec.partner_id.name))
            if kind == "organization" and not rec.partner_id.is_company:
                raise ValidationError(_(
                    "La catégorie « %(type)s » est réservée aux organisations ; %(name)s est une personne.",
                    type=rec.type_id.name, name=rec.partner_id.name))

    @api.constrains("partner_id", "company_id", "date_start", "date_end", "state")
    def _check_overlap(self):
        """Une personne n'a qu'une adhésion vivante à la fois, par société.

        Le renouvellement commence le lendemain de l'échéance : il ne chevauche
        jamais. Deux adhésions vivantes qui se chevauchent, c'est une double
        saisie ou un import rejoué.
        """
        for rec in self.filtered(lambda r: r.state in LIVE_STATES):
            domain = [
                ("id", "!=", rec.id),
                ("partner_id", "=", rec.partner_id.id),
                ("company_id", "=", rec.company_id.id),
                ("state", "in", LIVE_STATES),
                "|", ("date_end", "=", False), ("date_end", ">=", rec.date_start),
            ]
            if rec.date_end:
                domain.append(("date_start", "<=", rec.date_end))
            other = self.sudo().search(domain, limit=1)
            if other:
                raise ValidationError(_(
                    "%(name)s a déjà une adhésion pour cette période (%(other)s).",
                    name=rec.partner_id.name, other=other.display_name))

    @api.constrains("company_id", "type_id")
    def _check_company(self):
        for rec in self:
            if rec.type_id.company_id and rec.type_id.company_id != rec.company_id:
                raise ValidationError(_("La catégorie appartient à une autre société."))

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def _keep_trace(self):
        return keep_trace(self)

    @api.model_create_multi
    def create(self, vals_list):
        self = self._keep_trace()
        if not self.env.su:
            # 🔴 Les valeurs ne suffisent pas : l'ORM complète la création par les
            # `default_*` du contexte, qu'un appel direct choisit librement.
            ctx = {k: v for k, v in self.env.context.items()
                   if not (k.startswith("default_") and k[len("default_"):] in PROTECTED_ON_CREATE)}
            self = self.with_context(ctx)
            for vals in vals_list:
                forged = PROTECTED_ON_CREATE & {k for k, v in vals.items() if v and not (k == "state" and v == "draft")}
                if forged:
                    raise UserError(_(
                        "Une adhésion naît en demande : %s ne se fixe pas à la création.",
                        ", ".join(sorted(forged))))
        records = super().create(vals_list)
        if not self.env.su and any(rec.state != "draft" for rec in records):
            raise UserError(_("Une adhésion naît en demande."))
        for rec, vals in zip(records, vals_list):
            # Admission d'office : la demande est acceptée en naissant, sauf si
            # l'appelant fixe lui-même l'état (import d'une adhésion échue).
            if "state" not in vals and rec.type_id.admission == "automatic":
                rec._accept()
        records._ensure_numbers()
        return records

    def _identity_frozen(self):
        """Une adhésion payée, exemptée ou qui a fait quelqu'un membre ne change
        plus de membre, de catégorie ni de société : sinon un reçu, une carte ou
        une voix passeraient à une autre personne que celle qui a payé. Le
        greffon de facturation y ajoute : une facture la porte."""
        self.ensure_one()
        return (self.sudo().ever_settled or self.payment_state in ("paid", "exempt")
                or self.state in ("active", "expired", "withdrawn"))

    def write(self, vals):
        self = self._keep_trace()
        if not self.env.su:
            locked = [k for k in OFFICE_LOCKED & vals.keys()
                      if any((rec[k].id if hasattr(rec[k], "id") else rec[k]) != (vals[k] or False) for rec in self)]
            if locked:
                raise UserError(_(
                    "Ces champs se remplissent par les actions de l'adhésion, pas à la main : %s.",
                    ", ".join(sorted(locked))))
            # 🔴 L'état ne s'écrit que par les actions (en superutilisateur, après
            # le contrôle du droit d'écrire) : sinon « en règle » sans paiement.
            if "ever_settled" in vals:
                raise UserError(_("Ce drapeau se pose seul, à la première mise en règle."))
            if "state" in vals and any(rec.state != vals["state"] for rec in self):
                raise UserError(_(
                    "L'état d'une adhésion change par ses actions (accepter, noter le "
                    "paiement, retirer, refuser), pas par une écriture directe."))
            for field in IDENTITY_FIELDS & vals.keys():
                for rec in self:
                    if rec[field].id != (vals[field] or False) and rec._identity_frozen():
                        raise UserError(_(
                            "%(name)s est payée ou a fait quelqu'un membre : son membre, sa "
                            "catégorie et sa société ne changent plus.", name=rec.display_name))
        notes_changed = self.filtered(lambda r: r.note != vals["note"]) if "note" in vals else self.browse()
        res = super().write(vals)
        for rec in notes_changed:
            # Odoo ne suit pas un champ HTML : une ligne au fil dit qui a changé
            # les notes, sans en recopier le contenu.
            rec.sudo()._message_log(body=_("Notes de l'adhésion modifiées par %s.", self.env.user.name))
        if "payment_state" in vals:
            self._sync_state_from_payment()
        if "payment_state" in vals or "state" in vals:
            self._ensure_numbers()
        return res

    def _ensure_numbers(self):
        """Le numéro de membre vient avec la première mise en règle.

        🔴 Ni à la demande ni à l'acceptation : une demande jamais payée (un
        envoi du formulaire public, un pourriel) n'en consomme pas, et la
        fusion d'un contact « à rapprocher » dans le contact existant ne bute
        pas sur deux numéros.
        """
        for rec in self:
            settled = rec.payment_state in ("paid", "exempt")
            if rec.state == "active" or (settled and rec.state in ("expired", "withdrawn")):
                rec.partner_id._ensure_member_number()
                if not rec.sudo().ever_settled:
                    rec.sudo().ever_settled = True

    def _never_member(self):
        """Une adhésion qui n'a jamais rien fait de quelqu'un un membre : une
        demande, ou une adhésion refusée, jamais payée.

        🔴 L'état du jour ne suffit pas : une adhésion payée, remise « à payer »
        (un chèque sans provision), puis refusée, a fait un membre ; le registre
        la garde, et son contact avec elle."""
        self.ensure_one()
        # Une ligne importée échue et jamais payée n'a fait personne membre non
        # plus : elle part, comme une demande.
        return (not self.sudo().ever_settled and self.state in ("draft", "refused", "expired", "withdrawn")
                and self.payment_state == "to_pay")

    def unlink(self):
        if not all(rec._never_member() for rec in self):
            raise UserError(_(
                "Une adhésion acceptée ne se supprime pas : le registre garde "
                "les anciens membres. Retirez-la, ou refusez une demande jamais payée."))
        return super().unlink()

    def _sync_state_from_payment(self):
        """Le paiement fait passer d'« à payer » à « en règle », et inversement
        (une facture renversée, un chèque sans provision)."""
        for rec in self:
            settled = rec.payment_state in ("paid", "exempt")
            if rec.state == "waiting" and settled:
                rec.sudo().state = "active"
            elif rec.state == "active" and not settled:
                rec.sudo().state = "waiting"

    def _write_state(self, vals):
        """Les écritures de l'état : le droit d'écrire de la personne d'abord,
        puis en superutilisateur, seul admis par la garde de `write`."""
        self.check_access("write")
        return self.sudo().write(vals)

    def _accept(self):
        for rec in self:
            settled = rec.payment_state in ("paid", "exempt")
            rec._write_state({
                "state": "active" if settled else "waiting",
                "decided_by_id": self.env.user.id,
                "decision_date": fields.Date.context_today(rec),
            })

    def action_accept(self):
        if any(rec.state != "draft" for rec in self):
            raise UserError(_("Seule une demande peut être acceptée."))
        self._accept()

    def _refusable(self):
        """Une demande, ou une adhésion acceptée qui n'a jamais été payée (le
        pourriel d'un formulaire public, une personne qui ne donne pas suite).
        Le greffon de facturation y ajoute : sans facture validée."""
        self.ensure_one()
        if self.sudo().ever_settled:
            # Elle a fait un membre : un départ se dit par le retrait, daté et motivé.
            return False
        return self.state == "draft" or (self.state == "waiting" and self.payment_state == "to_pay")

    def action_refuse(self):
        if not all(rec._refusable() for rec in self):
            raise UserError(_(
                "Seule une demande, ou une adhésion qui n'a jamais fait de membre, peut "
                "être refusée. Une personne qui a été membre se retire."))
        self._write_state({
            "state": "refused",
            "decided_by_id": self.env.user.id,
            "decision_date": fields.Date.context_today(self),
        })

    def action_reset_draft(self):
        if any(rec.state != "refused" for rec in self):
            raise UserError(_("Seule une demande refusée revient à l'étude."))
        self._write_state({"state": "draft", "decided_by_id": False, "decision_date": False})

    def action_mark_paid(self):
        for rec in self:
            if rec.state in ("withdrawn", "refused"):
                raise UserError(_("On ne note pas un paiement sur une adhésion retirée ou refusée."))
            rec.write({
                "payment_state": "paid",
                "payment_date": rec.payment_date or fields.Date.context_today(rec),
                "payment_source": rec.payment_source or "other",
            })

    def action_exempt(self):
        self.write({"payment_state": "exempt"})

    def action_withdraw(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.membership.withdraw",
            "view_mode": "form",
            "target": "new",
            "context": {"default_membership_ids": self.ids},
        }

    def _withdraw(self, date, reason):
        for rec in self:
            if rec.state not in LIVE_STATES:
                raise UserError(_("%s n'est plus une adhésion vivante.", rec.display_name))
        self._write_state({
            "state": "withdrawn",
            "withdrawal_date": date,
            "withdrawal_reason": reason,
        })

    # ------------------------------------------------------------------
    # Renouvellement
    # ------------------------------------------------------------------

    def _renewal_vals(self):
        self.ensure_one()
        start = self.date_end + relativedelta(days=1)
        return {
            "partner_id": self.partner_id.id,
            "type_id": self.type_id.id,
            "company_id": self.company_id.id,
            "date_start": start,
            "renewal_of_id": self.id,
            "payment_state": "exempt" if not self.type_id.fee else "to_pay",
            # Un renouvellement ne repasse pas devant le conseil.
            "state": "waiting",
        }

    def action_renew(self):
        renewals = self.env["bf.membership"]
        for rec in self:
            if rec.state not in ("active", "expired") or not rec.date_end:
                raise UserError(_("%s ne se renouvelle pas (état ou période).", rec.display_name))
            if rec.renewal_ids.filtered(lambda r: r.state in LIVE_STATES):
                raise UserError(_("%s est déjà renouvelée.", rec.display_name))
            # Le renouvellement naît « à payer » : un état que seul le
            # superutilisateur fixe à la création, après le contrôle du droit.
            self.check_access("create")
            renewal = self.sudo().create(rec._renewal_vals()).sudo(False)
            renewal._sync_state_from_payment()
            renewals |= renewal
        if len(renewals) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "bf.membership",
                "res_id": renewals.id,
                "view_mode": "form",
            }
        return True

    # ------------------------------------------------------------------
    # La passe quotidienne
    # ------------------------------------------------------------------

    @api.model
    def _cron_daily(self):
        """Échoir, renouveler, rappeler, puis recalculer le statut des membres.

        Le renouvellement et les rappels ne tournent que pour les sociétés qui
        les ont allumés (`res.company`, éteints d'office).
        """
        # 🔴 En sudo : l'usager de la tâche planifiée n'a accès qu'à sa propre
        # société, et les règles par société cacheraient les autres.
        self = self.sudo()
        today = fields.Date.context_today(self)
        expiring = self.search([
            ("state", "in", ("active", "waiting")),
            ("date_end", "!=", False),
            ("date_end", "<", today),
        ])
        expiring.write({"state": "expired"})

        for company in self.env["res.company"].search([]):
            if company.membership_auto_renewal:
                self.with_company(company)._create_due_renewals(company, today)
            if company.membership_reminders:
                self.with_company(company)._send_due_reminders(company, today)

        partners = self.search([]).partner_id
        self.env.add_to_compute(self.env["res.partner"]._fields["member_status"], partners)
        self.env.add_to_compute(self.env["res.partner"]._fields["current_membership_id"], partners)

    def _create_due_renewals(self, company, today):
        candidates = self.search([
            ("company_id", "=", company.id),
            ("state", "=", "active"),
            ("date_end", "!=", False),
            ("date_end", ">=", today),
            ("type_id.active", "=", True),
        ])
        for rec in candidates:
            if rec.date_end - relativedelta(days=rec.type_id.renewal_days_before) > today:
                continue
            if rec.renewal_ids.filtered(lambda r: r.state != "refused"):
                continue
            renewal = self.create(rec._renewal_vals())
            renewal._sync_state_from_payment()

    def _reminder_due(self, today, company):
        """Le rappel que cette adhésion attend aujourd'hui, ou rien.

        🔴 Un rappel en retard de plus de `LATE_DAYS` ne part pas : il est
        sauté. Sans cette garde, allumer les rappels ou importer une liste
        réveillerait d'un coup tous les membres dont l'échéance est passée,
        avec des courriels datés d'hier.
        """
        self.ensure_one()
        late_days = 3
        order = [s for s, _label in REMINDER_STAGES]
        done = order.index(self.reminder_stage) if self.reminder_stage else -1
        triggers = [
            ("first", self.date_end - relativedelta(days=company.membership_reminder_first_days)),
            ("second", self.date_end - relativedelta(days=company.membership_reminder_second_days)),
            ("due", self.date_end),
            ("grace_end", self.date_end + relativedelta(days=self.type_id.grace_days)),
        ]
        due = None
        for stage, when in triggers:
            if order.index(stage) <= done or when > today:
                continue
            due = (stage, when)
        if not due:
            return None, None
        stage, when = due
        if (today - when).days > late_days:
            return None, stage
        return stage, stage

    def _send_due_reminders(self, company, today):
        template = self.env.ref("bf_membership.mail_template_renewal_reminder", raise_if_not_found=False)
        candidates = self.search([
            ("company_id", "=", company.id),
            ("state", "in", ("active", "expired")),
            ("payment_state", "in", ("paid", "exempt")),
            ("date_end", "!=", False),
            ("reminder_stage", "!=", "done"),
        ])
        for rec in candidates:
            # Déjà renouvelée et réglée : plus rien à rappeler.
            if rec.renewal_ids.filtered(lambda r: r.state == "active"):
                rec.reminder_stage = "done"
                continue
            # Un retrait dans CETTE société seulement : un départ d'une autre
            # association de la base ne coupe pas les rappels de celle-ci.
            if rec.partner_id.membership_ids.filtered(
                    lambda m: m.company_id == rec.company_id and m.state == "withdrawn"
                    and m.withdrawal_date and m.withdrawal_date >= rec.date_start):
                rec.reminder_stage = "done"
                continue
            send_stage, mark_stage = rec._reminder_due(today, company)
            if not mark_stage:
                continue
            if send_stage and template and rec.partner_id.email:
                template.with_context(reminder_stage=send_stage).send_mail(rec.id)
            rec.reminder_stage = "done" if mark_stage == "grace_end" else mark_stage
