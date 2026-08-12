import logging
import random
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Appended to every lure body so we capture opens in real time on our own
# endpoint, independently of the native mass-mailing tracking pixel. The marker
# attribute lets us detect "already injected" precisely, rather than scanning
# the body for a substring that could legitimately appear in template content.
OPEN_PIXEL_MARKER = "data-bf-sa-pixel"
OPEN_PIXEL_SNIPPET = (
    '<img t-att-src="object.open_pixel_url" %s="1" width="1" height="1" '
    'style="display:none;border:0;" alt=""/>' % OPEN_PIXEL_MARKER
)

# Optional QR block (quishing): encodes the per-recipient landing URL via Odoo's
# public barcode controller, so scanning the code on a phone hits /phish/<token>.
QR_MARKER = "data-bf-sa-qr"
QR_SNIPPET = (
    '<div %s="1" style="text-align:center;margin:18px 0;">'
    '<img t-att-src="object.qr_src" alt="QR" width="180" height="180"/>'
    '</div>' % QR_MARKER
)


class BfPhishingCampaign(models.Model):
    _name = "bf.phishing.campaign"
    _description = "Campagne de simulation d'hameçonnage"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(string="Nom", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("scheduled", "Planifiée"),
            ("sending", "Envoi en cours"),
            ("running", "En cours"),
            ("done", "Terminée"),
            ("cancelled", "Annulée"),
        ],
        string="État", default="draft", required=True, tracking=True,
    )
    template_id = fields.Many2one(
        "bf.phishing.template", string="Modèle de leurre",
        required=True, tracking=True,
    )

    # --- Audience selection --------------------------------------------
    recipient_partner_ids = fields.Many2many(
        "res.partner", string="Personnes ciblées",
        domain="[('email', '!=', False)]",
    )
    mailing_list_ids = fields.Many2many(
        "mailing.list", string="Listes de diffusion",
        help="Les contacts de ces listes sont ajoutés aux personnes ciblées.",
    )
    scheduled_date = fields.Datetime(
        string="Date d'envoi planifiée",
        help="Si renseignée, la campagne est lancée automatiquement à cette "
             "date par la tâche planifiée.",
    )
    send_window_days = fields.Float(
        string="Étalement des envois (jours)", default=0.0,
        help="Répartit aléatoirement les envois sur cette fenêtre (en jours) "
             "pour éviter qu'un destinataire alerte les autres. 0 = envoi "
             "immédiat à tous.",
    )
    mail_server_id = fields.Many2one(
        "ir.mail_server", string="Serveur d'envoi",
        help="Serveur SMTP utilisé pour l'envoi des leurres. Vide = serveur par "
             "défaut du système.",
    )
    auto_assign_training = fields.Boolean(
        string="Assigner la formation à l'échec", default=True,
    )

    # --- Engine links ---------------------------------------------------
    utm_campaign_id = fields.Many2one(
        "utm.campaign", string="Campagne UTM", readonly=True, copy=False)
    mailing_id = fields.Many2one(
        "mailing.mailing", string="Envoi de masse", readonly=True, copy=False)
    result_ids = fields.One2many(
        "bf.phishing.result", "campaign_id", string="Résultats")

    # --- Counters -------------------------------------------------------
    recipient_count = fields.Integer(
        compute="_compute_counters", string="Destinataires")
    sent_count = fields.Integer(compute="_compute_counters", string="Envoyés")
    opened_count = fields.Integer(compute="_compute_counters", string="Ouverts")
    clicked_count = fields.Integer(compute="_compute_counters", string="Clics")
    submitted_count = fields.Integer(
        compute="_compute_counters", string="Identifiants soumis")
    reported_count = fields.Integer(
        compute="_compute_counters", string="Signalements")
    fail_rate = fields.Float(
        compute="_compute_counters", string="Taux d'échec (%)",
        help="Part des destinataires ayant cliqué ou soumis leurs identifiants.")

    @api.depends("result_ids.state", "result_ids.reported")
    def _compute_counters(self):
        for camp in self:
            results = camp.result_ids
            camp.recipient_count = len(results)
            camp.sent_count = len(results.filtered(
                lambda r: r.state != "pending"))
            camp.opened_count = len(results.filtered(
                lambda r: r.state in ("opened", "clicked", "submitted")))
            clicked = len(results.filtered(
                lambda r: r.state in ("clicked", "submitted")))
            camp.clicked_count = clicked
            camp.submitted_count = len(results.filtered(
                lambda r: r.state == "submitted"))
            camp.reported_count = len(results.filtered("reported"))
            camp.fail_rate = (
                100.0 * clicked / camp.recipient_count
                if camp.recipient_count else 0.0
            )

    # ------------------------------------------------------------------ #
    # Audience resolution
    # ------------------------------------------------------------------ #
    def _resolve_target_partners(self):
        self.ensure_one()
        partners = self.recipient_partner_ids
        # mailing.contact has NO partner_id in stock Odoo, so resolve each list
        # contact to a res.partner by email (find-or-create) — a result row
        # requires a partner. Matching by email reuses an existing contact and
        # avoids duplicates across campaigns.
        Partner = self.env["res.partner"]
        for mlist in self.mailing_list_ids:
            for contact in mlist.contact_ids.filtered(lambda c: c.email):
                partner = Partner.search(
                    [("email", "=", contact.email)], limit=1
                ) or Partner.create({
                    "name": contact.name or contact.email,
                    "email": contact.email,
                })
                partners |= partner
        return partners.filtered(lambda p: p.email)

    # ------------------------------------------------------------------ #
    # State transitions
    # ------------------------------------------------------------------ #
    def action_prepare(self):
        """Materialise one result per recipient and build the engine records."""
        for camp in self:
            if camp.state not in ("draft",):
                raise UserError(_("La campagne doit être en brouillon."))
            partners = camp._resolve_target_partners()
            if not partners:
                raise UserError(_(
                    "Aucun destinataire avec une adresse courriel valide."))

            # Create results for partners not yet targeted.
            existing = camp.result_ids.mapped("partner_id")
            Result = self.env["bf.phishing.result"]
            to_create = []
            for partner in partners - existing:
                to_create.append({
                    "campaign_id": camp.id,
                    "partner_id": partner.id,
                    "email": partner.email,
                })
            if to_create:
                Result.create(to_create)

            camp._ensure_utm_campaign()
            camp._ensure_mailing()
            camp.state = "scheduled"
        return True

    def action_launch(self):
        for camp in self:
            if camp.state not in ("scheduled", "sending"):
                raise UserError(_("Préparez d'abord la campagne."))
            if not camp.mailing_id:
                camp._ensure_mailing()
            camp.state = "sending"
            # Assign each pending result a send time: now (blast) or jittered
            # across the window (drip). The cron then releases due batches.
            now = fields.Datetime.now()
            pending = camp.result_ids.filtered(lambda r: r.state == "pending")
            for res in pending:
                if camp.send_window_days > 0:
                    res.scheduled_send = now + timedelta(
                        days=random.uniform(0.0, camp.send_window_days))
                else:
                    res.scheduled_send = now
            camp.state = "running"
            camp._release_due_results()
        return True

    def _release_due_results(self):
        """Send the lure to every pending result whose scheduled_send has
        arrived. Idempotent: released rows move to 'sent' and won't re-enter."""
        self.ensure_one()
        if self.state != "running" or not self.mailing_id:
            return
        now = fields.Datetime.now()
        due = self.result_ids.filtered(
            lambda r: r.state == "pending"
            and r.scheduled_send and r.scheduled_send <= now)
        if not due:
            return
        # Our own source of truth for "sent": independent of SMTP success so the
        # simulation flow works even on a mail-less staging tenant.
        due.register_sent()
        try:
            self.mailing_id.action_send_mail(res_ids=due.ids)
        except Exception as exc:  # noqa: BLE001 - never block the campaign
            _logger.warning(
                "bf_security_awareness: mass-mail send failed for campaign "
                "%s: %s", self.id, exc)
            self.message_post(body=_(
                "L'envoi de masse a échoué (%s). Les résultats restent suivis "
                "via le lien piégé.") % exc)

    def action_cancel(self):
        for camp in self:
            camp.state = "cancelled"
        return True

    def action_done(self):
        for camp in self:
            camp.state = "done"
        return True

    def action_reset_to_draft(self):
        for camp in self:
            camp.state = "draft"
        return True

    # ------------------------------------------------------------------ #
    # Engine record builders
    # ------------------------------------------------------------------ #
    def _ensure_utm_campaign(self):
        self.ensure_one()
        if self.utm_campaign_id:
            return
        self.utm_campaign_id = self.env["utm.campaign"].create({
            "name": _("Hameçonnage : %s") % self.name,
        })

    def _build_body_html(self):
        self.ensure_one()
        body = self.template_id.email_body or ""
        # Optional QR (quishing) block, injected once.
        if self.template_id.qr_code and QR_MARKER not in body:
            body = "%s\n%s" % (body, QR_SNIPPET)
        # Append our real-time open pixel exactly once (marker-based check).
        if OPEN_PIXEL_MARKER not in body:
            body = "%s\n%s" % (body, OPEN_PIXEL_SNIPPET)
        return body

    def _default_email_from(self):
        # mailing.mailing.email_from is NOT NULL: always resolve to something.
        return (
            self.template_id.email_from
            or self.env.user.email_formatted
            or self.env.company.email
            or "no-reply@localhost"
        )

    def _ensure_mailing(self):
        self.ensure_one()
        model_id = self.env["ir.model"]._get_id("bf.phishing.result")
        body = self._build_body_html()
        email_from = self._default_email_from()
        vals = {
            "subject": self.template_id.subject or self.name,
            "body_arch": body,
            "body_html": body,
            "mailing_model_id": model_id,
            "mailing_domain": repr([
                ("campaign_id", "=", self.id),
            ]),
            "campaign_id": self.utm_campaign_id.id,
            "reply_to_mode": "new",
            "reply_to": email_from,
            "email_from": email_from,
            "mail_server_id": self.mail_server_id.id or False,
            "attachment_ids": [(6, 0, self.template_id.attachment_ids.ids)],
            "keep_archives": True,
        }
        if self.mailing_id:
            self.mailing_id.write(vals)
        else:
            self.mailing_id = self.env["mailing.mailing"].create(vals)

    def action_view_results(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Résultats"),
            "res_model": "bf.phishing.result",
            "view_mode": "list,form,pivot,graph",
            "domain": [("campaign_id", "=", self.id)],
            "context": {"default_campaign_id": self.id},
        }

    def action_view_mailing(self):
        self.ensure_one()
        if not self.mailing_id:
            raise UserError(_("Aucun envoi de masse n'a encore été créé."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "mailing.mailing",
            "res_id": self.mailing_id.id,
            "view_mode": "form",
        }

    # ------------------------------------------------------------------ #
    # Cron
    # ------------------------------------------------------------------ #
    @api.model
    def _cron_scheduler(self):
        """Launch scheduled campaigns whose time has arrived, and reconcile
        bounces for running ones."""
        now = fields.Datetime.now()
        due = self.search([
            ("state", "=", "scheduled"),
            ("scheduled_date", "!=", False),
            ("scheduled_date", "<=", now),
        ])
        for camp in due:
            try:
                camp.action_launch()
            except Exception as exc:  # noqa: BLE001
                _logger.exception(
                    "bf_security_awareness: scheduled launch failed for %s: %s",
                    camp.id, exc)
        running = self.search([("state", "=", "running")])
        for camp in running:
            camp._release_due_results()  # drip: send batches whose time arrived
        running.result_ids._sync_from_traces()
