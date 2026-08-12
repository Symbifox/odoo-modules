"""bf.email.rule — minimal rule engine for auto-categorization.

Rules fire on bf.email.create() after _compute_category, so manual writes
still win. Mass action ``action_replay_rules`` backfills existing rows.

Per-user: each rule belongs to a single owner (``user_id``) and only fires
against bf.email rows owned by that user. safe_eval still runs in a
restricted namespace; each user manages their own rules.
"""

import logging
import re

from odoo import api, fields, models
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

# Stock rules every user starts with. Mirrors data/bf_email_rule_default.xml
# (which only covers the user installing the module): seeded for any other
# user the first time they create a bf.email.account, if they have no rules.
DEFAULT_RULE_SPECS = [
    {
        "name": "Expéditeurs noreply → Notification + Traité",
        "sequence": 10,
        "condition_type": "regex_from",
        "condition_value": (
            r"^(noreply|no-reply|notification|mailer-daemon|postmaster"
            r"|bounce|do-not-reply)@"
        ),
        "set_category": "notification",
        "set_handled": True,
        "description": "Sortie immédiate de la boîte. Les notifications "
                       "automatiques ne demandent pas d'action.",
    },
    {
        "name": "List-Unsubscribe présent → Marketing + Traité",
        "sequence": 20,
        "condition_type": "header_present",
        "condition_value": "List-Unsubscribe",
        "set_category": "marketing",
        "set_handled": True,
        "description": "Grbovic et al. 2014 — l'en-tête List-Unsubscribe "
                       "est le signal le plus fort pour le bulk marketing.",
    },
    {
        "name": "Partenaire client_rank > 0 → Client (priorité Élevée)",
        "sequence": 40,
        "condition_type": "partner_tag",
        "condition_value": "(p.customer_rank or 0) > 0",
        "set_category": "client",
        "set_priority": "2",
        "description": "Tout courriel d'un client connu (customer_rank) "
                       "passe en priorité Élevée.",
    },
    {
        "name": "Partenaire supplier_rank > 0 → Fournisseur",
        "sequence": 50,
        "condition_type": "partner_tag",
        "condition_value": "(p.supplier_rank or 0) > 0",
        "set_category": "vendor",
        "description": "Fournisseurs connus.",
    },
]


class BfEmailRule(models.Model):
    _name = "bf.email.rule"
    _description = "Règle de catégorisation courriels"
    _order = "sequence, id"

    name = fields.Char(string="Nom", required=True)
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        required=True,
        index=True,
        default=lambda self: self.env.user,
        ondelete="cascade",
        help="La règle ne s'applique qu'aux courriels de ce·tte utilisateur·trice.",
    )

    condition_type = fields.Selection(
        selection=[
            ("domain", "Domaine Odoo"),
            ("regex_subject", "Regex sur l'objet"),
            ("regex_from", "Regex sur l'expéditeur"),
            ("regex_body", "Regex sur l'aperçu"),
            ("header_present", "En-tête présent"),
            ("partner_tag", "Champ partenaire (booléen ou comparaison)"),
        ],
        string="Type de condition",
        required=True,
        default="regex_from",
        help=(
            "domain : expression Odoo évaluée contre l'enregistrement (record). "
            "regex_* : motif Python regex (insensible à la casse). "
            "header_present : chaîne à chercher dans raw_headers (ex: List-Unsubscribe). "
            "partner_tag : expression booléenne sur partner — ex: customer_rank > 0."
        ),
    )
    condition_value = fields.Text(
        string="Valeur",
        required=True,
        help="Voir l'aide du type de condition.",
    )

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
    set_handled = fields.Boolean(
        string="Marquer comme traité",
        help="Sort le courriel de la boîte de réception (utile pour "
             "marketing/notifications qui ne demandent pas d'action).",
    )
    stop_processing = fields.Boolean(
        string="Arrêter ici",
        help="Si coché, les règles suivantes ne sont pas évaluées.",
    )

    description = fields.Text(string="Notes")

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def _match(self, record):
        """Return True if this rule's condition matches ``record`` (bf.email)."""
        self.ensure_one()
        if not self.active:
            return False
        try:
            if self.condition_type == "domain":
                domain = safe_eval(self.condition_value, {
                    "record": record,
                    "rec": record,
                    "uid": self.env.uid,
                    "user": self.env.user,
                })
                if isinstance(domain, list):
                    return bool(record.search_count(
                        domain + [("id", "=", record.id)]
                    ))
                return bool(domain)
            if self.condition_type == "regex_subject":
                return bool(re.search(
                    self.condition_value, record.subject or "",
                    re.IGNORECASE,
                ))
            if self.condition_type == "regex_from":
                return bool(re.search(
                    self.condition_value, record.email_from or "",
                    re.IGNORECASE,
                ))
            if self.condition_type == "regex_body":
                return bool(re.search(
                    self.condition_value, record.body_preview or "",
                    re.IGNORECASE,
                ))
            if self.condition_type == "header_present":
                return self.condition_value.lower() in (
                    record.raw_headers or ""
                ).lower()
            if self.condition_type == "partner_tag":
                partner = record.partner_id or record.author_id
                if not partner:
                    return False
                return bool(safe_eval(self.condition_value, {
                    "partner": partner,
                    "p": partner,
                }))
        except Exception:
            _logger.warning(
                "bf.email.rule %s: evaluation error on record %s",
                self.id, record.id, exc_info=True,
            )
            return False
        return False

    # ------------------------------------------------------------------
    # Per-user seeding
    # ------------------------------------------------------------------
    @api.model
    def _seed_defaults_for_user(self, user):
        """Give ``user`` the stock rules if they own none yet.

        Called when a user creates their first bf.email.account. sudo:
        an admin creating an account for someone else couldn't otherwise
        create rules owned by that user (owner ir.rule).
        """
        if not user or user.share:
            return
        Rule = self.sudo().with_context(active_test=False)
        if Rule.search_count([("user_id", "=", user.id)]):
            return
        Rule.create([
            dict(spec, user_id=user.id) for spec in DEFAULT_RULE_SPECS
        ])
        _logger.info(
            "bf.email.rule: seeded %d default rules for user %s",
            len(DEFAULT_RULE_SPECS), user.id,
        )

    # ------------------------------------------------------------------
    # Mass action: replay rules over existing rows
    # ------------------------------------------------------------------
    @api.model
    def action_replay_rules(self):
        """Re-run the current user's rules over their own bf.email rows.

        Bound as a server action; intended for backfill after editing rules.
        Only affects ``self.env.uid``'s rows — never another user's data.
        """
        BfEmail = self.env["bf.email"]
        rows = BfEmail.search([
            ("is_handled", "=", False),
            ("user_id", "=", self.env.uid),
        ])
        rows._apply_rules()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Règles rejouées",
                "message": f"{len(rows)} courriel(s) ré-évalué(s).",
                "type": "success",
                "sticky": False,
            },
        }
