"""bf.email.rule — Outlook-style routing rules over the unified inbox.

A rule is *conditions* (all of them, or any of them), *minus exceptions*, then
*actions*. Rules fire from ``bf.email.create()`` right after ``_compute_category``,
so a manual write still wins, and ``action_replay_rules`` re-runs them over rows
already in the box.

Two scopes:

- **user** — the rule belongs to one person and only sees their own rows. This
  is what everybody gets; the stock rules are seeded per user.
- **company** — the rule has no owner and applies to every user of its company.
  Only a settings administrator can create or edit one, because it reaches into
  other people's mailboxes.

Actions can classify (category, priority, contact), file (folder, out of the
box, snooze), hand over (route to a colleague's box) and forward. Everything up
to *forward* stays inside Odoo; forwarding is the one action that leaves the
building, and it carries its own guards — see ``_forward_blocked_reason``.

Ordering: rules run in ``sequence, id`` order and the **first** rule to set a
given field wins, so an early specific rule is not undone by a later generic
one. ``stop_processing`` cuts the walk short.
"""

import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import formataddr

_logger = logging.getLogger(__name__)

# Hops a message may already carry before we refuse to forward it again.
DEFAULT_MAX_HOPS = 1
# Forwards one rule may send per calendar day.
DEFAULT_DAILY_CAP = 200
# Messages « essayer » and « appliquer » look at. Bounded on purpose: a
# rule is written against what the box looks like now, not against five
# years of archive, and an unbounded filtered() over 40 000 rows with a
# regex clause is a worker that does not come back.
PREVIEW_LIMIT = 500


# ---------------------------------------------------------------------------
# Recipe catalogue
#
# Each entry is a ready-made rule. ``seed`` marks the ones every new user
# starts with (they used to live in DEFAULT_RULE_SPECS); the rest are offered
# by the « Ajouter des règles courantes » wizard. One catalogue, so a recipe
# improved here improves both paths.
#
# On the ``^(noreply|…)@`` patterns: ``email_from`` holds the raw From header,
# so it is usually ``"Acme" <noreply@acme.com>`` and an anchor at the start of
# the string never fires. Every address pattern below therefore anchors on an
# address boundary instead.
# ---------------------------------------------------------------------------
ADDRESS_START = r"(?:^|[<\s:,;])"

RULE_RECIPES = [
    {
        "key": "cc_only",
        "seed": False,
        "name": "Je suis seulement en c.c. → hors de la boîte",
        "sequence": 5,
        "description": "Le cas d'école : ce qui m'est envoyé en copie "
                       "conforme est de l'information, pas une demande. "
                       "Classé, remis à « non lu » et sorti de la boîte de "
                       "réception — il reste consultable dans son dossier.",
        "conditions": [
            {"field_name": "is_cc_to_me", "operator": "is_true"},
            {"field_name": "direction", "operator": "equals",
             "value_direction": "in"},
        ],
        "exceptions": [
            {"field_name": "priority", "operator": "equals",
             "value_priority": "3"},
        ],
        "actions": {
            "set_category": "notification",
            "set_status": "new",
            "set_folder": "Copie conforme",
            "set_handled": True,
        },
    },
    {
        "key": "noreply",
        "seed": True,
        "name": "Expéditeurs automatiques (noreply) → Notification + traité",
        "sequence": 10,
        "description": "Sortie immédiate de la boîte. Les notifications "
                       "automatiques ne demandent pas d'action.",
        "conditions": [
            {"field_name": "email_from", "operator": "regex",
             "value": ADDRESS_START + r"(noreply|no-reply|notification"
                      r"|mailer-daemon|postmaster|bounce|do-not-reply)@"},
        ],
        "actions": {"set_category": "notification", "set_handled": True},
    },
    {
        "key": "list_unsubscribe",
        "seed": True,
        "name": "List-Unsubscribe présent → Marketing + traité",
        "sequence": 20,
        "description": "Grbovic et al. 2014 — l'en-tête List-Unsubscribe est "
                       "le signal le plus fort pour le bulk marketing.",
        "conditions": [
            {"field_name": "header", "operator": "is_set",
             "header_name": "List-Unsubscribe"},
        ],
        "actions": {"set_category": "marketing", "set_handled": True},
    },
    {
        "key": "auto_reply",
        "seed": False,
        "name": "Réponses automatiques d'absence → traité",
        "sequence": 25,
        "description": "RFC 3834 : un message portant Auto-Submitted autre "
                       "que « no » est une réponse de machine. Rien à y "
                       "répondre.",
        "conditions": [
            {"field_name": "header", "operator": "is_set",
             "header_name": "Auto-Submitted"},
        ],
        "exceptions": [
            {"field_name": "header", "operator": "contains",
             "header_name": "Auto-Submitted", "value": "no"},
        ],
        "actions": {"set_category": "notification", "set_handled": True},
    },
    {
        "key": "client_partner",
        "seed": True,
        "name": "Contact client → Client (priorité Élevée)",
        "sequence": 40,
        "description": "Tout courriel d'un client connu (customer_rank) "
                       "passe en priorité Élevée.",
        "conditions": [
            {"field_name": "partner_field", "operator": "expr",
             "value": "(p.customer_rank or 0) > 0"},
        ],
        "actions": {"set_category": "client", "set_priority": "2"},
    },
    {
        "key": "vendor_partner",
        "seed": True,
        "name": "Contact fournisseur → Fournisseur",
        "sequence": 50,
        "description": "Fournisseurs connus.",
        "conditions": [
            {"field_name": "partner_field", "operator": "expr",
             "value": "(p.supplier_rank or 0) > 0"},
        ],
        "actions": {"set_category": "vendor"},
    },
    {
        "key": "internal_sender",
        "seed": False,
        "name": "Expéditeur de mon organisation → Interne",
        "sequence": 45,
        "description": "Le domaine de l'expéditeur est un de ceux de "
                       "l'organisation. Utile quand le contact n'existe pas "
                       "encore comme partenaire.",
        "conditions": [
            {"field_name": "is_internal_sender", "operator": "is_true"},
        ],
        "actions": {"set_category": "internal"},
    },
    {
        "key": "invoices",
        "seed": False,
        "name": "Factures et reçus → dossier Comptabilité",
        "sequence": 60,
        "description": "Regroupe ce qui ressemble à une pièce comptable "
                       "dans un dossier dédié, sans le sortir de la boîte : "
                       "une facture demande encore une décision.",
        "conditions": [
            {"field_name": "subject", "operator": "contains_any",
             "value": "facture, invoice, reçu, receipt, relevé, statement, "
                      "paiement, payment"},
            {"field_name": "has_attachments", "operator": "is_true"},
        ],
        "actions": {"set_category": "vendor", "set_folder": "Comptabilité"},
    },
    {
        "key": "social",
        "seed": False,
        "name": "Réseaux sociaux → Marketing + traité",
        "sequence": 70,
        "description": "LinkedIn, Facebook, X, Instagram : de la "
                       "notification, jamais une demande.",
        "conditions": [
            {"field_name": "email_from", "operator": "regex",
             "value": r"@(linkedin|facebook|facebookmail|twitter|x|instagram"
                      r"|tiktok|pinterest)\.[a-z.]+"},
        ],
        "actions": {"set_category": "marketing", "set_handled": True},
    },
    {
        "key": "monitoring",
        "seed": False,
        "name": "Alertes de supervision → dossier Alertes",
        "sequence": 30,
        "description": "Les sondes et les surveillances parlent beaucoup. "
                       "Elles vont dans leur dossier, en priorité Élevée "
                       "pour rester visibles.",
        "conditions": [
            {"field_name": "subject", "operator": "contains_any",
             "value": "alert, alarm, alerte, monitoring, uptime, down, "
                      "critical, warning, watchdog, backup failed"},
        ],
        "match_type": "any",
        "actions": {
            "set_category": "notification",
            "set_priority": "2",
            "set_folder": "Alertes",
        },
    },
    {
        "key": "calendar",
        "seed": False,
        "name": "Invitations et réponses d'agenda → traité",
        "sequence": 35,
        "description": "Les invitations iMIP sont déjà ingérées dans "
                       "l'agenda par le module. La copie courriel n'a plus "
                       "à occuper la boîte.",
        "conditions": [
            {"field_name": "header", "operator": "contains",
             "header_name": "Content-Type", "value": "text/calendar"},
        ],
        "actions": {"set_category": "notification", "set_handled": True},
    },
    {
        "key": "newsletters_digest",
        "seed": False,
        "name": "Infolettres → dossier Lecture",
        "sequence": 75,
        "description": "Variante plus douce que « Marketing + traité » : "
                       "l'infolettre est mise de côté pour plus tard au "
                       "lieu d'être classée sans suite.",
        "conditions": [
            {"field_name": "header", "operator": "is_set",
             "header_name": "List-Id"},
        ],
        "actions": {"set_folder": "Lecture", "set_handled": True},
    },
]

RECIPES_BY_KEY = {r["key"]: r for r in RULE_RECIPES}
SEED_RECIPE_KEYS = [r["key"] for r in RULE_RECIPES if r.get("seed")]


class BfEmailRule(models.Model):
    _name = "bf.email.rule"
    _description = "Règle de routage des courriels"
    _order = "sequence, id"

    name = fields.Char(string="Nom", required=True)
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)

    scope = fields.Selection(
        selection=[
            ("user", "Mes courriels"),
            ("company", "Toute l'organisation"),
        ],
        string="Portée",
        required=True,
        default="user",
        help="« Toute l'organisation » applique la règle aux courriels de "
             "chaque utilisateur·trice de la société. Réservé aux "
             "administrateurs.",
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        index=True,
        default=lambda self: self.env.user,
        ondelete="cascade",
        help="La règle ne s'applique qu'aux courriels de cette personne. "
             "Vide pour une règle d'organisation.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        index=True,
        default=lambda self: self.env.company,
        ondelete="cascade",
        help="Filtre les courriels par société — mais uniquement pour une "
             "règle d'organisation. Une règle personnelle est déjà bornée "
             "par son propriétaire ; y ajouter un filtre de société ne "
             "ferait que la désactiver en silence le jour où la fiche "
             "courriel porte une autre société.",
    )

    match_type = fields.Selection(
        selection=[
            ("all", "Toutes les conditions (ET)"),
            ("any", "Au moins une condition (OU)"),
        ],
        string="Déclenchement",
        required=True,
        default="all",
    )
    condition_ids = fields.One2many(
        comodel_name="bf.email.rule.condition",
        inverse_name="rule_id",
        string="Conditions",
        domain=[("kind", "=", "condition")],
        context={"default_kind": "condition"},
        copy=True,
    )
    exception_ids = fields.One2many(
        comodel_name="bf.email.rule.condition",
        inverse_name="rule_id",
        string="Exceptions",
        domain=[("kind", "=", "exception")],
        context={"default_kind": "exception"},
        copy=True,
        help="Si une exception s'applique, la règle ne fait rien.",
    )
    condition_summary = fields.Char(
        string="Résumé",
        compute="_compute_condition_summary",
    )

    # -- actions -------------------------------------------------------
    set_category = fields.Selection(
        selection=[
            ("client", "Client"),
            ("internal", "Interne"),
            ("vendor", "Fournisseur"),
            ("notification", "Notification"),
            ("marketing", "Marketing"),
        ],
        string="Définir la catégorie",
    )
    set_priority = fields.Selection(
        selection=[
            ("0", "Normal"),
            ("1", "Faible"),
            ("2", "Élevée"),
            ("3", "Urgente"),
        ],
        string="Définir la priorité",
    )
    set_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Lier au contact",
    )
    set_status = fields.Selection(
        selection=[
            ("new", "Non lu"),
            ("read", "Lu"),
        ],
        string="Marquer comme",
        help="« Non lu » est l'équivalent Outlook de la règle qui range un "
             "message sans le faire disparaître des compteurs.",
    )
    set_folder = fields.Char(
        string="Déplacer vers le dossier",
        help="Dossier IMAP de destination. {YYYY} et {MM} sont remplacés par "
             "l'année et le mois du courriel. Demande « Réécriture des "
             "archives » sur le compte IMAP : sans elle rien n'est déplacé, "
             "ni côté serveur ni côté Odoo — la fiche ne peut pas annoncer "
             "un dossier où le message n'est pas.",
    )
    set_handled = fields.Boolean(
        string="Sortir de la boîte de réception",
        help="Marque le courriel comme traité. Sans dossier de destination, "
             "la copie IMAP part vers le dossier d'archives du compte.",
    )
    snooze_hours = fields.Integer(
        string="Reporter de (heures)",
        help="Sort le courriel de la boîte et l'y ramène après ce délai.",
    )
    route_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Confier à",
        help="Réattribue la fiche à une autre personne de l'organisation : "
             "le courriel apparaît dans SA boîte. Aucun courriel n'est "
             "envoyé — c'est un transfert interne.",
    )

    forward_to = fields.Char(
        string="Réacheminer à",
        help="Adresses séparées par des virgules. Le courriel est renvoyé "
             "depuis votre propre adresse (jamais celle de l'expéditeur "
             "d'origine, qui échouerait aux contrôles SPF/DKIM), avec "
             "l'expéditeur d'origine en « répondre à ».",
    )
    forward_allow_external = fields.Boolean(
        string="Autoriser hors de l'organisation",
        help="Sans cette case, seuls les destinataires d'un domaine de "
             "l'organisation reçoivent le réacheminement. Réservé aux "
             "administrateurs.",
    )
    forward_outgoing = fields.Boolean(
        string="Réacheminer aussi mes envois",
        help="Par défaut seuls les courriels entrants sont réacheminés.",
    )
    forward_log_ids = fields.One2many(
        comodel_name="bf.email.auto.log",
        inverse_name="rule_id",
        string="Journal des réacheminements",
        domain=[("kind", "=", "forward")],
        readonly=True,
    )
    forward_log_count = fields.Integer(
        string="Réacheminements",
        compute="_compute_forward_log_count",
    )

    stop_processing = fields.Boolean(
        string="Arrêter ici",
        help="Si coché, les règles suivantes ne sont pas évaluées.",
    )

    description = fields.Text(string="Notes")
    recipe_key = fields.Char(
        string="Recette d'origine",
        readonly=True,
        copy=False,
        help="Clé de la recette du catalogue qui a produit cette "
             "règle. Sert à l'assistant « règles courantes » pour ne "
             "pas reproposer ce qui est déjà en place ; l'effacer ne "
             "change rien au comportement de la règle.",
    )

    action_summary = fields.Char(
        string="Ce qu'elle fait",
        compute="_compute_action_summary",
        help="Les actions de la règle en une phrase. La liste porte huit "
             "colonnes d'actions dont sept sont masquées par défaut ; "
             "celle-ci les dit toutes d'un coup.",
    )
    is_noop = fields.Boolean(
        string="Sans effet",
        compute="_compute_is_noop",
        help="La règle se déclenche et ne fait rien : aucune action n'est "
             "renseignée. Une règle qui ne fait rien en silence est pire "
             "qu'une règle qui échoue.",
    )
    folder_gap_accounts = fields.Char(
        string="Comptes sans réécriture",
        compute="_compute_folder_gap_accounts",
        help="Comptes du propriétaire qui ne réécrivent pas sur le serveur "
             "IMAP. Un « déplacer vers le dossier » ne peut rien y faire.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("condition_ids.description", "exception_ids.description",
                 "match_type")
    def _compute_condition_summary(self):
        for rule in self:
            joiner = " ET " if rule.match_type == "all" else " OU "
            summary = joiner.join(
                c.description for c in rule.condition_ids if c.description
            )
            if rule.exception_ids:
                summary += " — sauf si " + " ou ".join(
                    c.description for c in rule.exception_ids if c.description
                )
            rule.condition_summary = summary or "aucune condition"

    @api.depends("set_category", "set_priority", "set_partner_id",
                 "set_status", "set_folder", "set_handled", "snooze_hours",
                 "route_user_id", "forward_to")
    def _compute_action_summary(self):
        """One readable sentence for what the rule does, for the list view."""
        categories = dict(
            self._fields["set_category"]._description_selection(self.env))
        priorities = dict(
            self._fields["set_priority"]._description_selection(self.env))
        statuses = dict(
            self._fields["set_status"]._description_selection(self.env))
        for rule in self:
            parts = []
            if rule.set_category:
                parts.append(_("catégorie %s",
                               categories.get(rule.set_category)))
            if rule.set_priority:
                parts.append(_("priorité %s",
                               priorities.get(rule.set_priority)))
            if rule.set_partner_id:
                parts.append(_("contact %s", rule.set_partner_id.display_name))
            if rule.set_status:
                parts.append(_("marque « %s »",
                               statuses.get(rule.set_status)))
            if rule.set_folder:
                parts.append(_("range dans %s", rule.set_folder))
            if rule.snooze_hours:
                parts.append(_("reporte de %s h", rule.snooze_hours))
            elif rule.set_handled:
                parts.append(_("sort de la boîte"))
            if rule.route_user_id:
                parts.append(_("confie à %s", rule.route_user_id.name))
            if rule.forward_to:
                parts.append(_("réachemine à %s", rule.forward_to))
            rule.action_summary = " · ".join(parts) or _("aucune action")

    @api.depends("condition_ids", "set_category", "set_priority",
                 "set_partner_id", "set_status", "set_folder", "set_handled",
                 "snooze_hours", "route_user_id", "forward_to")
    def _compute_is_noop(self):
        """A rule with conditions and no action fires and changes nothing.

        ``_match`` already refuses a rule with no *condition*, on the grounds
        that « no condition » must not read as « everything ». The mirror case
        is just as silent and had nothing watching it.
        """
        for rule in self:
            rule.is_noop = bool(rule.condition_ids) and not any([
                rule.set_category, rule.set_priority, rule.set_partner_id,
                rule.set_status, rule.set_folder, rule.set_handled,
                rule.snooze_hours, rule.route_user_id, rule.forward_to,
            ])

    @api.depends("set_folder", "user_id")
    def _compute_folder_gap_accounts(self):
        """Name the owner's accounts a « move to folder » cannot reach.

        ``set_folder`` needs ``writeback_archive`` on the IMAP account: without
        it ``_apply_rules`` skips the move and says so in the log, which nobody
        reads. Naming the accounts on the form is the same information, where
        the rule is being written.
        """
        Account = self.env["bf.email.account"].sudo()
        for rule in self:
            if not rule.set_folder or not rule.user_id:
                rule.folder_gap_accounts = False
                continue
            deaf = Account.search([
                ("user_id", "=", rule.user_id.id),
                ("writeback_archive", "=", False),
            ])
            rule.folder_gap_accounts = ", ".join(deaf.mapped("name")) or False

    def _compute_forward_log_count(self):
        counts = {}
        if self.ids:
            grouped = self.env["bf.email.auto.log"].sudo()._read_group(
                [("rule_id", "in", self.ids)], ["rule_id"], ["__count"],
            )
            counts = {rule.id: count for rule, count in grouped}
        for rule in self:
            rule.forward_log_count = counts.get(rule.id, 0)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @api.onchange("scope")
    def _onchange_scope(self):
        for rule in self:
            if rule.scope == "company":
                rule.user_id = False
            elif not rule.user_id:
                rule.user_id = self.env.user

    @api.constrains("scope", "user_id")
    def _check_scope(self):
        for rule in self:
            if rule.scope == "user" and not rule.user_id:
                raise ValidationError(_(
                    "Une règle personnelle a besoin d'un propriétaire."))
            if rule.scope == "company" and rule.user_id:
                raise ValidationError(_(
                    "Une règle d'organisation ne peut pas avoir de "
                    "propriétaire : elle s'applique à tout le monde."))

    @api.constrains("scope")
    def _check_company_scope_is_admin(self):
        """Only a settings administrator reaches into other people's boxes.

        The ir.rule already stops a plain user from writing an ownerless row,
        but a rule flipped from ``user`` to ``company`` by its own owner would
        pass that check on the way in — the record still has their user_id at
        the moment the domain is evaluated. So the gate lives here too.
        """
        if self.env.su:
            return
        for rule in self:
            if rule.scope == "company" and not self.env.user.has_group(
                    "base.group_system"):
                raise ValidationError(_(
                    "Seul un administrateur peut créer une règle qui "
                    "s'applique à toute l'organisation."))

    @api.constrains("forward_to", "forward_allow_external", "user_id")
    def _check_forward(self):
        for rule in self:
            if not rule.forward_to:
                continue
            recipients = rule._forward_recipients()
            if not recipients:
                raise ValidationError(_(
                    "« Réacheminer à » ne contient aucune adresse valide."))
            external = rule._external_recipients(recipients)
            if external and not rule.forward_allow_external:
                raise ValidationError(_(
                    "%(addrs)s sont hors de l'organisation. Cochez "
                    "« Autoriser hors de l'organisation » si c'est voulu.",
                    addrs=", ".join(sorted(external)),
                ))
            if (rule.forward_allow_external and not self.env.su
                    and not self.env.user.has_group("base.group_system")):
                raise ValidationError(_(
                    "Le réacheminement hors de l'organisation est réservé "
                    "aux administrateurs."))

    @api.constrains("route_user_id")
    def _check_route_user(self):
        for rule in self:
            if rule.route_user_id and rule.route_user_id.share:
                raise ValidationError(_(
                    "« Confier à » vise une personne interne ; %s est un "
                    "utilisateur de portail.", rule.route_user_id.name))

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def _match(self, record, ctx=None):
        """True when this rule fires on ``record``.

        A rule with no condition never fires. That is deliberate: the other
        reading — "no condition means everything" — turns a half-written rule
        into a mailbox-wide action.
        """
        self.ensure_one()
        if not self.active:
            return False
        # Company filter for company-wide rules only — see the field help.
        if (self.scope == "company" and self.company_id and record.company_id
                and self.company_id != record.company_id):
            return False
        conditions = self.condition_ids
        if not conditions:
            return False
        results = (c._match(record, ctx) for c in conditions)
        fired = all(results) if self.match_type == "all" else any(results)
        if not fired:
            return False
        return not any(c._match(record, ctx) for c in self.exception_ids)

    # ------------------------------------------------------------------
    # Actions applied to a record
    # ------------------------------------------------------------------
    def _plan_actions(self, record, taken):
        """Contribute this rule's actions to a record's plan.

        ``taken`` is the set of targets an earlier rule already claimed, so the
        first rule to speak about a target wins. Returns ``(vals, extras)``.
        """
        self.ensure_one()
        vals = {}
        extras = {}

        def claim(target):
            if target in taken:
                return False
            taken.add(target)
            return True

        if self.set_category and claim("category"):
            vals["category"] = self.set_category
        if self.set_priority and claim("priority"):
            vals["priority"] = self.set_priority
        if self.set_partner_id and claim("partner_id"):
            vals["partner_id"] = self.set_partner_id.id
        if self.set_status and claim("status"):
            vals["status"] = self.set_status
        if self.route_user_id and claim("user_id"):
            vals["user_id"] = self.route_user_id.id
        if self.snooze_hours and claim("snooze"):
            vals["snoozed_until"] = fields.Datetime.add(
                fields.Datetime.now(), hours=self.snooze_hours)
            # Snoozing takes the row out of the box too, so it claims
            # "handled" like any other rule would. Without the claim a later
            # rule could still speak about the same target and rewrite
            # handled_at behind this one's back.
            if claim("handled"):
                vals["is_handled"] = True
                vals.setdefault("handled_at", fields.Datetime.now())
        # ``not record.is_handled`` comes BEFORE claim(): a row that is already
        # out of the box has nothing to take, and burning the claim on it would
        # silence a later rule for no reason.
        if self.set_handled and not record.is_handled and claim("handled"):
            vals["is_handled"] = True
            vals["handled_at"] = fields.Datetime.now()
        if self.set_folder and claim("folder"):
            extras["folder"] = self._resolve_folder(record)
        if self.forward_to:
            extras.setdefault("forward_rules", []).append(self.id)
        return vals, extras

    def _resolve_folder(self, record):
        """Expand {YYYY} / {MM} in the destination folder."""
        self.ensure_one()
        when = record.date or fields.Datetime.now()
        return (self.set_folder or "").replace(
            "{YYYY}", when.strftime("%Y")).replace("{MM}", when.strftime("%m"))

    # ------------------------------------------------------------------
    # Forwarding
    # ------------------------------------------------------------------
    def _forward_recipients(self):
        """Parsed, de-duplicated, lower-cased addresses from ``forward_to``."""
        self.ensure_one()
        out = []
        for piece in re.split(r"[,;\s]+", self.forward_to or ""):
            piece = piece.strip().strip("<>")
            if piece and "@" in piece and piece.lower() not in out:
                out.append(piece.lower())
        return out

    def _internal_domains(self):
        """Domains that count as « inside the organisation ».

        Three sources, union: the ICP ``bf_email.internal_domains`` (the
        explicit answer, a comma-separated list), the company's own email
        addresses, and the owner's own sending addresses.
        """
        self.ensure_one()
        domains = set()
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_email.internal_domains", "")
        for piece in re.split(r"[,;\s]+", raw or ""):
            piece = piece.strip().lower().lstrip("@")
            if piece:
                domains.add(piece)
        companies = self.company_id or self.env["res.company"].sudo().search([])
        for company in companies:
            for addr in (company.email, company.catchall_email,
                         company.partner_id.email):
                if addr and "@" in addr:
                    domains.add(addr.rsplit("@", 1)[1].strip().lower())
        if self.user_id:
            for addr in self.env["bf.email"]._get_self_addresses(
                    user=self.user_id):
                if "@" in addr:
                    domains.add(addr.rsplit("@", 1)[1])
        return {d for d in domains if d}

    def _external_recipients(self, recipients=None):
        """The subset of ``recipients`` outside the organisation."""
        self.ensure_one()
        recipients = recipients if recipients is not None \
            else self._forward_recipients()
        internal = self._internal_domains()
        return {r for r in recipients
                if r.rsplit("@", 1)[-1] not in internal}

    def _forward_max_hops(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_email.forward_max_hops")
        try:
            return max(0, int(raw)) if raw else DEFAULT_MAX_HOPS
        except ValueError:
            return DEFAULT_MAX_HOPS

    def _forward_daily_cap(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_email.forward_daily_cap")
        try:
            return max(0, int(raw)) if raw else DEFAULT_DAILY_CAP
        except ValueError:
            return DEFAULT_DAILY_CAP

    def _instance_uuid(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "database.uuid", "")

    def _read_hop_count(self, record):
        """Hops the incoming message already carries, from our own header."""
        headers = record.raw_headers or ""
        match = re.search(
            r"^X-BF-Forward-Hops:\s*(\d+)", headers,
            re.IGNORECASE | re.MULTILINE,
        )
        return int(match.group(1)) if match else 0

    def _forward_blocked_reason(self, record):
        """Why this message must not be forwarded — or False to proceed.

        Every guard is answered **here**, before a mail.mail exists. A guard
        placed after the send is not a guard: a message already handed to the
        queue cannot be recalled by a rollback.
        """
        self.ensure_one()
        Log = self.env["bf.email.auto.log"]
        if not self.forward_to:
            return "aucun destinataire"
        if record.direction != "in" and not self.forward_outgoing:
            return "courriel sortant"
        headers = record.raw_headers or ""
        auto = re.search(
            r"^Auto-Submitted:\s*([^\r\n]+)", headers,
            re.IGNORECASE | re.MULTILINE,
        )
        if auto and auto.group(1).strip().lower() != "no":
            return "message automatique (Auto-Submitted: %s)" % (
                auto.group(1).strip())
        uuid = self._instance_uuid()
        if uuid and re.search(
            r"^X-BF-Forwarded-By:.*%s" % re.escape(uuid), headers,
            re.IGNORECASE | re.MULTILINE,
        ):
            return "déjà réacheminé par cette instance (boucle)"
        hops = self._read_hop_count(record)
        max_hops = self._forward_max_hops()
        if hops >= max_hops:
            return "plafond de sauts atteint (%s/%s)" % (hops, max_hops)
        cap = self._forward_daily_cap()
        if cap and Log._sent_today(self) >= cap:
            return "plafond quotidien atteint (%s)" % cap
        return False

    def _forward(self, record):
        """Send ``record`` onward. Returns the mail.mail, or an empty set."""
        self.ensure_one()
        Log = self.env["bf.email.auto.log"]
        recipients = self._forward_recipients()
        if not recipients:
            return self.env["mail.mail"]

        # Test mode is refused HERE and not in _forward_blocked_reason: that
        # method answers « why is this message unforwardable », and running
        # under a test suite is not a property of the message. Putting it
        # there made every guard test read « mode test » instead of the
        # reason it was checking.
        if Log._test_mode():
            for addr in recipients:
                Log._log(self, record, addr, "skipped", "mode test : aucun envoi")
            return self.env["mail.mail"]

        blocked = self._forward_blocked_reason(record)
        if blocked:
            for addr in recipients:
                Log._log(self, record, addr, "skipped", blocked)
            return self.env["mail.mail"]

        owner = record.user_id or self.user_id or self.env.user
        own_addresses = self.env["bf.email"]._get_self_addresses(user=owner)
        internal = self._internal_domains()

        keep, from_addr = [], None
        for addr in recipients:
            if addr in own_addresses:
                Log._log(self, record, addr, "skipped",
                         "adresse du propriétaire (boucle)")
                continue
            is_external = addr.rsplit("@", 1)[-1] not in internal
            if is_external and not self.forward_allow_external:
                Log._log(self, record, addr, "skipped",
                         "hors organisation, non autorisé", is_external=True)
                continue
            keep.append((addr, is_external))
        if not keep:
            return self.env["mail.mail"]

        from_addr = (record.account_id.login or owner.email
                     or owner.partner_id.email)
        if not from_addr:
            for addr, is_external in keep:
                Log._log(self, record, addr, "error",
                         "aucune adresse d'expédition pour %s" % owner.name,
                         is_external=is_external)
            return self.env["mail.mail"]

        headers = {
            "Auto-Submitted": "auto-forwarded",
            "X-BF-Forwarded-By": self._instance_uuid() or "bf_email",
            "X-BF-Forward-Hops": str(self._read_hop_count(record) + 1),
            "X-BF-Forward-Rule": str(self.id),
        }
        if record.message_id_header:
            headers["References"] = record.message_id_header

        subject = record.subject or _("(sans objet)")
        if not subject.lower().startswith(("tr :", "tr:", "fwd:", "fw:")):
            subject = "Tr : %s" % subject

        try:
            mail = self.env["mail.mail"].sudo().create({
                "subject": subject[:250],
                "body_html": self._forward_body(record),
                "email_from": formataddr((owner.name, from_addr)),
                "email_to": ", ".join(addr for addr, _ext in keep),
                "reply_to": record.email_from or from_addr,
                "headers": repr(headers),
                "auto_delete": True,
                "attachment_ids": [(6, 0, self._forward_attachments(record))],
            })
        except Exception as exc:
            _logger.warning(
                "bf.email.rule %s: réacheminement impossible pour bf.email %s",
                self.id, record.id, exc_info=True,
            )
            for addr, is_external in keep:
                Log._log(self, record, addr, "error", str(exc),
                         is_external=is_external)
            return self.env["mail.mail"]

        for addr, is_external in keep:
            Log._log(self, record, addr, "sent", mail=mail,
                     is_external=is_external)
        _logger.info(
            "bf.email.rule %s: bf.email %s réacheminé vers %s",
            self.id, record.id, ", ".join(a for a, _e in keep),
        )
        return mail

    def _forward_attachments(self, record):
        """Attachment ids to carry over, original files first."""
        if record.attachment_ids:
            return record.attachment_ids.ids
        if record.raw_rfc822:
            try:
                return record._extract_orphan_attachments()
            except Exception:
                _logger.warning(
                    "bf.email %s: extraction des pièces jointes impossible "
                    "pour le réacheminement", record.id, exc_info=True,
                )
        return []

    def _forward_body(self, record):
        """Forward wrapper without the composer's signature block.

        ``bf.email._build_forward_body`` opens with the current user's
        signature because a human is about to type above it. Nobody is typing
        here, and the signature of whoever happened to trigger the cron would
        be the wrong one anyway.
        """
        self.ensure_one()
        note = _(
            "Réacheminé automatiquement par la règle « %s ».", self.name)
        body = record._build_forward_body()
        return (
            '<div style="font-size:12px;color:#888;margin-bottom:8px;">'
            f'{note}</div>{body}'
        )

    # ------------------------------------------------------------------
    # Seeding and recipes
    # ------------------------------------------------------------------
    @api.model
    def _recipe_to_vals(self, recipe, user=None, company=None):
        """Turn a catalogue entry into a create() dict."""
        vals = {
            "name": recipe["name"],
            "recipe_key": recipe["key"],
            "sequence": recipe.get("sequence", 10),
            "description": recipe.get("description"),
            "match_type": recipe.get("match_type", "all"),
            "condition_ids": [
                (0, 0, dict(spec, kind="condition"))
                for spec in recipe.get("conditions", [])
            ],
            "exception_ids": [
                (0, 0, dict(spec, kind="exception"))
                for spec in recipe.get("exceptions", [])
            ],
        }
        vals.update(recipe.get("actions", {}))
        if user is not None:
            vals["scope"] = "user"
            vals["user_id"] = user.id
            vals["company_id"] = (company or user.company_id).id
        else:
            vals["scope"] = "company"
            vals["user_id"] = False
            vals["company_id"] = (company or self.env.company).id
        return vals

    @api.model
    def _seed_defaults_for_user(self, user):
        """Give ``user`` the stock rules if they own none yet.

        Called when a user creates their first bf.email.account. sudo: an admin
        creating an account for someone else could not otherwise create rules
        owned by that user (owner ir.rule).
        """
        if not user or user.share:
            return
        Rule = self.sudo().with_context(active_test=False)
        if Rule.search_count([("user_id", "=", user.id)]):
            return
        Rule.create([
            self._recipe_to_vals(RECIPES_BY_KEY[key], user=user)
            for key in SEED_RECIPE_KEYS
        ])
        _logger.info(
            "bf.email.rule: %d règles par défaut semées pour %s",
            len(SEED_RECIPE_KEYS), user.id,
        )

    @api.model
    def action_open_quick_create(self):
        """Open the « common rules » picker."""
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.email.rule.quick",
            "view_mode": "form",
            "target": "new",
            "name": _("Ajouter des règles courantes"),
        }

    # ------------------------------------------------------------------
    # Mass action: replay rules over existing rows
    # ------------------------------------------------------------------
    @api.model
    def action_replay_rules(self):
        """Re-run the current user's rules over their own bf.email rows.

        Bound as a server action; intended for backfill after editing rules.
        Only affects ``self.env.uid``'s rows — never another user's data, and
        never forwards: replaying a rule over three months of archive would
        put three months of mail back on the wire.
        """
        BfEmail = self.env["bf.email"]
        rows = BfEmail.search([
            ("is_handled", "=", False),
            ("user_id", "=", self.env.uid),
        ])
        rows._apply_rules(allow_outbound=False)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Règles rejouées"),
                "message": _(
                    "%s courriel(s) ré-évalué(s). Le réacheminement et la "
                    "réponse d'absence restent volontairement inactifs "
                    "pendant un rejeu.", len(rows)),
                "type": "success",
                "sticky": False,
            },
        }

    def action_view_forward_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Réacheminements — %s", self.name),
            "res_model": "bf.email.auto.log",
            "view_mode": "list,form",
            "domain": [("rule_id", "=", self.id)],
            "context": {"create": False},
        }

    def _preview_owner(self):
        """Whose mailbox « essayer » and « appliquer » look into.

        A company-wide rule has no owner, so both answer for the person
        holding the form. That is deliberate: an administrator writing a rule
        for everybody still wants to see it against a mailbox they may read,
        and applying it to every user's archive from a button is not a thing
        a button should be able to do.
        """
        self.ensure_one()
        return self.user_id or self.env.user

    def _matching_rows(self, limit=PREVIEW_LIMIT):
        """The owner's most recent rows this rule fires on, newest first."""
        self.ensure_one()
        rows = self.env["bf.email"].search(
            [("user_id", "=", self._preview_owner().id)],
            order="date desc", limit=limit,
        )
        if not rows:
            return rows
        ctx = rows._rule_owner_context(self._preview_owner())
        return rows.filtered(lambda r: self._match(r, ctx))

    def action_test_rule(self):
        """List the messages this rule would fire on, changing nothing.

        Read-only on purpose: it answers « est-ce que ma condition attrape ce
        que je crois » without touching a single message. It looks at the
        owner's most recent messages (``PREVIEW_LIMIT``) whether or not they
        are still in the box — « Appliquer cette règle maintenant » acts on
        exactly this list.
        """
        self.ensure_one()
        hits = self._matching_rows()
        return {
            "type": "ir.actions.act_window",
            "name": _("Essai — %s", self.name),
            "res_model": "bf.email",
            "view_mode": "list,form",
            "domain": [("id", "in", hits.ids)],
            "context": {"create": False},
            "help": "<p>%s</p>" % _(
                "Aucun des %s derniers courriels ne déclenche cette règle.",
                PREVIEW_LIMIT),
        }

    def action_apply_this_rule(self):
        """Apply THIS rule, now, to the messages it already matches.

        The gap this closes: rules fire from ``bf.email.create()``, so a rule
        written today does nothing about the mail that arrived yesterday. The
        only lever used to be « rejouer toutes les règles », which re-runs the
        whole rule set and — by design — only looks at what is still in the
        box. A rule that merely files or reclassifies (dossier, catégorie,
        priorité, sans « sortir de la boîte ») could therefore never be
        applied to anything after the fact.

        Acts on exactly what « essayer » just listed, so the dry run is an
        honest preview of this. Never forwards and never answers an absence,
        for the reason a replay never does: re-running a rule over an archive
        must not put old mail back on the wire.

        One thing this deliberately does NOT reproduce: the walk. Running one
        rule on its own ignores an earlier rule that would have claimed the
        same target first, or cut the walk short with ``stop_processing``. The
        outcome can therefore differ from what the engine would have decided
        on arrival — which is the point of asking for *this* rule. « Rejouer
        toutes les règles » is the faithful-to-the-engine version.
        """
        self.ensure_one()
        owner = self._preview_owner()
        if owner != self.env.user and not self.env.user.has_group(
                "base.group_system"):
            raise UserError(_(
                "Cette règle appartient à %s : seule cette personne (ou un "
                "administrateur) peut l'appliquer à sa boîte.", owner.name))
        rows = self._matching_rows()
        if rows:
            # As the owner, not as whoever pressed the button. The « admin
            # sees all » ir.rule grants read and NOT write, so an
            # administrator applying somebody's rule would otherwise get an
            # AccessError halfway through — some rows filed, some not.
            rows.with_user(owner)._apply_rules(allow_outbound=False, rules=self)
        message = _(
            "%(count)s courriel(s) traité(s) par « %(rule)s ». Le "
            "réacheminement et la réponse d'absence restent volontairement "
            "inactifs.", count=len(rows), rule=self.name)
        # Say what was not looked at. A bounded scan reported as a plain count
        # reads as « tout est passé », which is exactly the impression that
        # made this button necessary in the first place.
        total = self.env["bf.email"].search_count(
            [("user_id", "=", owner.id)])
        if total > PREVIEW_LIMIT:
            message += " " + _(
                "Seuls les %(limit)s courriels les plus récents ont été "
                "examinés, sur %(total)s.",
                limit=PREVIEW_LIMIT, total=total)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Règle appliquée"),
                "message": message,
                "type": "success" if rows else "warning",
                "sticky": False,
            },
        }
