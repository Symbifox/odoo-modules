"""Customer testimonials with consent tracking.

The core tracks consent obtained verbally or in writing (with a proof note).
When privacy_consent is installed, the bf_cx_privacy bridge adds the
formal Loi 25 consent flow (privacy.consent link + request button); the core
has no reference to privacy models so it loads on any tenant.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfCxTestimonial(models.Model):
    _name = "bf.cx.testimonial"
    _description = "Témoignage client"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(string="Titre", required=True, tracking=True)
    partner_id = fields.Many2one(
        "res.partner", string="Client", required=True, index=True, tracking=True
    )
    body = fields.Text(string="Témoignage", required=True)
    context_note = fields.Char(
        string="Contexte",
        help="Mandat, projet ou situation d'où vient le témoignage.",
    )
    project_id = fields.Many2one("project.project", string="Projet / mandat")
    date = fields.Date(
        string="Date", default=fields.Date.context_today, required=True
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("consent_pending", "Consentement demandé"),
            ("consented", "Consentement obtenu"),
            ("published", "Publié"),
            ("declined", "Refusé"),
            ("retired", "Retiré"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
    )
    # The 'privacy' mode (formal privacy.consent flow) is ADDED by the
    # bf_cx_privacy bridge via selection_add - the core must not
    # offer a mode whose proof mechanism is not installed.
    consent_mode = fields.Selection(
        [
            ("verbal", "Verbal"),
            ("written", "Écrit (courriel, lettre)"),
        ],
        string="Mode de consentement",
        tracking=True,
    )
    consent_note = fields.Char(
        string="Preuve du consentement",
        help="Référence de la preuve : courriel du client, date de l'appel, "
             "document signé, etc.",
    )
    consent_date = fields.Date(string="Consenti le")
    published_at = fields.Date(string="Publié le", readonly=True, copy=False)
    where_used = fields.Char(
        string="Utilisé sur",
        help="Site web, soumission, page LinkedIn… là où le témoignage est "
             "cité.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        required=True,
        default=lambda self: self.env.company,
    )
    feedback_id = fields.Many2one(
        "bf.cx.feedback",
        string="Feedback d'origine",
        ondelete="set null",
    )

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_set_consented(self):
        for rec in self:
            if not rec.consent_mode:
                raise UserError(
                    _("Indiquer le mode de consentement avant de le déclarer "
                      "obtenu.")
                )
            if not rec.consent_note:
                raise UserError(
                    _("Consigner la preuve du consentement (champ « Preuve du "
                      "consentement »).")
                )
            rec.write(
                {
                    "state": "consented",
                    "consent_date": rec.consent_date
                    or fields.Date.context_today(rec),
                }
            )
        return True

    def action_publish(self):
        for rec in self:
            if rec.state != "consented":
                raise UserError(
                    _("Un témoignage ne peut être publié qu'avec un "
                      "consentement obtenu.")
                )
            rec.write(
                {
                    "state": "published",
                    "published_at": fields.Date.context_today(rec),
                }
            )
        return True

    def action_decline(self):
        self.write({"state": "declined"})
        return True

    def action_retire(self):
        """Consent withdrawn: stop using the testimonial everywhere."""
        for rec in self:
            rec.write({"state": "retired"})
            rec.message_post(
                body=_(
                    "Témoignage retiré - retirer toute utilisation en cours "
                    "(%s)."
                )
                % (rec.where_used or _("aucun emplacement consigné"))
            )
        return True

    def action_reset_draft(self):
        self.write({"state": "draft"})
        return True
