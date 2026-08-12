import base64
import logging
from datetime import timedelta
from email import message_from_bytes
from email.utils import parseaddr

import requests

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Best-practice reporting taxonomy presented to employees. Shared with the
# report wizard so the dropdown and the stored value stay in sync.
REPORT_CATEGORIES = [
    ("phishing", "Hameçonnage (vol d'identifiants)"),
    ("wire_fraud", "Fraude au virement / au président"),
    ("malware", "Pièce jointe ou lien suspect"),
    ("impersonation", "Usurpation d'identité (collègue, fournisseur, dirigeant)"),
    ("spam", "Pourriel / indésirable"),
    ("other", "Autre / je ne suis pas certain"),
]


class BfReportedPhish(models.Model):
    """A user-submitted "report this email as malicious" record and its triage.

    Two outcomes, decided in :meth:`process`:
      * the reported mail is one of OUR simulations -> the reporter is credited
        (``register_report`` on their result) and NO incident is raised;
      * it is any other email -> it is triaged: an optional Helpdesk ticket, a
        follow-up activity, and an ntfy alert to the security team.

    Helpdesk and ntfy are BOTH soft/optional (guarded + config-driven) so the
    module never hard-depends on a specific helpdesk or notification stack.
    """

    _name = "bf.reported.phish"
    _description = "Courriel signalé comme malveillant"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(compute="_compute_name", store=True)
    reporter_id = fields.Many2one(
        "res.partner", string="Signalé par", required=True,
        ondelete="cascade", index=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Société",
        default=lambda self: self.env.company, index=True)
    report_date = fields.Datetime(
        string="Signalé le", default=fields.Datetime.now, tracking=True)

    category = fields.Selection(
        REPORT_CATEGORIES, string="Catégorie", required=True,
        default="phishing", tracking=True)
    email_from = fields.Char(string="Expéditeur signalé")
    subject = fields.Char(string="Sujet")
    details = fields.Text(string="Précisions")
    suspicious_link = fields.Char(string="Lien suspect")
    message_id = fields.Char(
        string="Message-ID",
        help="Identifiant interne unique du courriel (extrait du .eml joint). "
             "Permet un retrait exact dans toutes les boîtes.")

    clawback_operation_ids = fields.One2many(
        "bf.clawback.operation", "reported_phish_id", string="Retraits")
    clawback_count = fields.Integer(compute="_compute_clawback_count")

    state = fields.Selection(
        [
            ("new", "Nouveau"),
            ("triage", "En triage"),
            ("threat_confirmed", "Menace confirmée"),
            ("false_alarm", "Fausse alerte"),
            ("simulation", "Simulation (reconnue)"),
        ],
        string="État", default="new", required=True, tracking=True)

    is_simulation = fields.Boolean(
        string="Était une simulation", readonly=True, tracking=True)
    matched_result_id = fields.Many2one(
        "bf.phishing.result", string="Résultat de simulation lié",
        readonly=True, ondelete="set null")
    campaign_id = fields.Many2one(
        "bf.phishing.campaign", string="Campagne",
        related="matched_result_id.campaign_id", store=True)

    # Soft helpdesk link: a plain id (no m2o) so the module stays installable
    # without any helpdesk addon.
    helpdesk_ticket_id = fields.Integer(
        string="Ticket Helpdesk", readonly=True)
    has_ticket = fields.Boolean(compute="_compute_has_ticket")

    @api.depends("subject", "email_from", "reporter_id")
    def _compute_name(self):
        for rec in self:
            label = rec.subject or rec.email_from or _("Courriel suspect")
            rec.name = _("Signalement — %s") % label

    @api.depends("helpdesk_ticket_id")
    def _compute_has_ticket(self):
        for rec in self:
            rec.has_ticket = bool(rec.helpdesk_ticket_id) and (
                "helpdesk.ticket" in self.env)

    @api.depends("clawback_operation_ids")
    def _compute_clawback_count(self):
        for rec in self:
            rec.clawback_count = len(rec.clawback_operation_ids)

    # ------------------------------------------------------------------ #
    # Mail-gateway ingestion (Courriels app) — forward-as-attachment to an
    # alias, e.g. signaler-hameconnage@<domaine>, creates a report.
    # ------------------------------------------------------------------ #
    @api.model
    def _is_internal_sender(self, partner, email_from):
        """Belt-and-suspenders over the alias' alias_contact='employees':
        only accept a forward from an internal (non-portal) user."""
        if partner and partner.user_ids.filtered(lambda u: not u.share):
            return True
        return False

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Create a report from an email forwarded to the alias.

        The reporter is the (internal) sender; the *threat's* From / Subject /
        Message-ID are taken from the attached original .eml, not from this
        forwarding envelope. External senders are rejected (anti-DoS / anti-
        injection of clawback criteria)."""
        custom_values = dict(custom_values or {})
        author = self.env["res.partner"].browse(msg_dict.get("author_id")) \
            if msg_dict.get("author_id") else self.env["res.partner"]
        email_from = msg_dict.get("email_from") or ""
        if not self._is_internal_sender(author, email_from):
            raise UserError(_(
                "Signalement par courriel refusé : expéditeur externe (%s).")
                % (tools.email_normalize(email_from) or email_from or "?"))
        custom_values.setdefault("reporter_id", author.id or False)
        custom_values.setdefault("category", "phishing")
        # Leave email_from / subject blank: apply_eml_metadata fills them from
        # the forwarded .eml (the actual malicious message), not this envelope.
        report = super().message_new(msg_dict, custom_values)
        report.apply_eml_metadata()
        report.process()
        return report

    # ------------------------------------------------------------------ #
    # .eml ingestion — extract Message-ID / From / Subject for precise clawback
    # ------------------------------------------------------------------ #
    def apply_eml_metadata(self):
        """Parse any attached .eml and fill message_id (always) plus
        email_from / subject when they were left blank. Safe to re-run."""
        Attachment = self.env["ir.attachment"].sudo()
        for rec in self:
            atts = Attachment.search([
                ("res_model", "=", rec._name), ("res_id", "=", rec.id),
            ])
            for att in atts:
                is_eml = (att.name or "").lower().endswith(".eml") or (
                    att.mimetype == "message/rfc822")
                if not is_eml or not att.datas:
                    continue
                try:
                    raw = base64.b64decode(att.datas)
                    msg = message_from_bytes(raw)
                except Exception:  # noqa: BLE001
                    _logger.warning(
                        "bf_security_awareness: could not parse .eml %s", att.name)
                    continue
                mid = (msg.get("Message-ID") or "").strip()
                if mid:
                    rec.message_id = mid
                if not rec.email_from:
                    rec.email_from = parseaddr(msg.get("From") or "")[1] or False
                if not rec.subject:
                    rec.subject = (msg.get("Subject") or "").strip() or False
                break  # first .eml wins
        return True

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _icp(self, key, default=None):
        return self.env["ir.config_parameter"].sudo().get_param(
            "bf_security_awareness.%s" % key, default)

    # ------------------------------------------------------------------ #
    # Triage entry point
    # ------------------------------------------------------------------ #
    def process(self):
        """Classify the report and run the matching action sequence.

        Returns a small dict the wizard turns into a user-facing notification."""
        self.ensure_one()
        match = self._detect_simulation()
        if match:
            self.write({
                "is_simulation": True,
                "matched_result_id": match.id,
                "state": "simulation",
            })
            match.register_report()  # credit the reporter on their risk profile
            self.message_post(body=_(
                "Signalement reconnu : ce courriel faisait partie d'une "
                "simulation autorisée. Aucun incident n'a été créé."))
            return {
                "is_simulation": True,
                "message": _(
                    "Bien joué ! Ce courriel était une simulation de sécurité. "
                    "Votre bon réflexe a été enregistré — aucun incident créé."),
            }

        # Real suspicious email -> triage sequence.
        self.state = "triage"
        self._create_helpdesk_ticket()
        self._schedule_triage_activity()
        self._notify_security_team()
        self.message_post(body=_(
            "Courriel suspect transmis au triage de sécurité."))
        return {
            "is_simulation": False,
            "message": _(
                "Merci ! Votre signalement a été transmis à l'équipe de "
                "sécurité pour analyse."),
        }

    def _detect_simulation(self):
        Result = self.env["bf.phishing.result"].sudo()
        # 1) Precise: the reported link points at one of our /phish/<token> URLs.
        if self.suspicious_link and "/phish/" in self.suspicious_link:
            token = self.suspicious_link.split("/phish/", 1)[1]
            token = token.split("/")[0].split("?")[0].strip()
            if token:
                res = Result.search([("token", "=", token)], limit=1)
                if res:
                    return res
        # 2) Heuristic: a recent simulation to this reporter whose lure subject
        #    matches what they reported.
        if self.reporter_id and self.subject:
            window = int(float(self._icp("sim_match_window_days", "30") or 30))
            since = fields.Datetime.now() - timedelta(days=window)
            res = Result.search([
                ("partner_id", "=", self.reporter_id.id),
                ("create_date", ">=", since),
                ("campaign_id.template_id.subject", "ilike", self.subject),
            ], limit=1)
            if res:
                return res
        return Result.browse()

    def _create_helpdesk_ticket(self):
        if "helpdesk.ticket" not in self.env:
            return False
        Ticket = self.env["helpdesk.ticket"].sudo()
        cat = dict(REPORT_CATEGORIES).get(self.category, self.category)
        description = _(
            "<p>Courriel suspect signalé par <strong>%(rep)s</strong>.</p>"
            "<ul>"
            "<li><strong>Catégorie :</strong> %(cat)s</li>"
            "<li><strong>Expéditeur :</strong> %(from)s</li>"
            "<li><strong>Sujet :</strong> %(subj)s</li>"
            "<li><strong>Lien :</strong> %(link)s</li>"
            "</ul>"
            "<p><strong>Précisions :</strong><br/>%(details)s</p>"
        ) % {
            "rep": self.reporter_id.display_name or _("(inconnu)"),
            "cat": cat,
            "from": self.email_from or "—",
            "subj": self.subject or "—",
            "link": self.suspicious_link or "—",
            "details": (self.details or "—").replace("\n", "<br/>"),
        }
        vals = {
            "name": _("Courriel suspect : %s") % (
                self.subject or self.email_from or _("(sans objet)")),
            "description": description,
            "partner_id": self.reporter_id.id or False,
        }
        team_id = self._icp("report_helpdesk_team_id")
        if team_id:
            try:
                vals["team_id"] = int(team_id)
            except (TypeError, ValueError):
                pass
        ticket = Ticket.create(vals)
        self.helpdesk_ticket_id = ticket.id
        return ticket

    def _triage_user(self):
        """Resolve who chases a real report: explicit config, else a secaware
        manager, else the current user."""
        uid = self._icp("report_responsible_user_id")
        if uid:
            try:
                user = self.env["res.users"].sudo().browse(int(uid))
                if user.exists():
                    return user
            except (TypeError, ValueError):
                pass
        group = self.env.ref(
            "bf_security_awareness.group_bf_secaware_manager",
            raise_if_not_found=False)
        if group and group.users:
            return group.users[0]
        return self.env.user

    def _schedule_triage_activity(self):
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            summary=_("Trier le courriel suspect signalé"),
            note=_("Signalé par %s. Vérifier, confirmer la menace ou classer "
                   "en fausse alerte.") % self.reporter_id.display_name,
            user_id=self._triage_user().id,
        )

    def _notify_security_team(self):
        url = self._icp("report_ntfy_url")
        if not url:
            _logger.info(
                "bf_security_awareness: ntfy not configured, skipping alert "
                "for report %s", self.id)
            return
        token = self._icp("report_ntfy_token")
        priority = self._icp("report_ntfy_priority", "high") or "high"
        cat = dict(REPORT_CATEGORIES).get(self.category, self.category)
        body = _(
            "Signalé par : %(rep)s\nCatégorie : %(cat)s\nDe : %(from)s\n"
            "Sujet : %(subj)s"
        ) % {
            "rep": self.reporter_id.display_name or "?",
            "cat": cat,
            "from": self.email_from or "—",
            "subj": self.subject or "—",
        }
        headers = {
            "Title": "Courriel malveillant signale",  # ASCII: HTTP header safe
            "Priority": str(priority),
            "Tags": "rotating_light,fishing_pole_and_fish",
        }
        if token:
            headers["Authorization"] = "Bearer %s" % token
        try:
            requests.post(url, data=body.encode("utf-8"),
                          headers=headers, timeout=10)
        except Exception as exc:  # noqa: BLE001 - never block the report
            _logger.warning(
                "bf_security_awareness: ntfy alert failed for report %s: %s",
                self.id, exc)

    # ------------------------------------------------------------------ #
    # Manager state transitions
    # ------------------------------------------------------------------ #
    def action_mark_threat(self):
        for rec in self:
            rec.state = "threat_confirmed"
        return True

    def action_mark_false_alarm(self):
        for rec in self:
            rec.state = "false_alarm"
        return True

    def action_open_ticket(self):
        self.ensure_one()
        if not (self.helpdesk_ticket_id and "helpdesk.ticket" in self.env):
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "res_id": self.helpdesk_ticket_id,
            "views": [[False, "form"]],
        }

    # ------------------------------------------------------------------ #
    # Clawback (PhishRIP) — pull the email from every internal mailbox
    # ------------------------------------------------------------------ #
    def action_launch_clawback(self):
        """Create a clawback operation pre-filled from this report and open it.

        Manager-only (enforced by ir.model.access on the operation). Never auto-
        executes: the manager runs Aperçu then Exécuter from the operation form."""
        self.ensure_one()
        if self.is_simulation:
            raise UserError(_(
                "Ce signalement est une simulation : aucun retrait requis."))
        self.apply_eml_metadata()
        connector = self.env["bf.mail.clawback.connector"].search(
            [("active", "=", True)], limit=1)
        if not connector:
            raise UserError(_(
                "Aucun connecteur courriel actif. Configurez-en un sous "
                "Sensibilisation › Configuration › Connecteurs courriel."))
        window = int(float(self._icp("clawback_heuristic_window_days", "7") or 7))
        strategy = "message_id" if self.message_id else "heuristic"
        op = self.env["bf.clawback.operation"].create({
            "reported_phish_id": self.id,
            "connector_id": connector.id,
            "match_strategy": strategy,
            "message_id": self.message_id or False,
            "email_from": self.email_from or False,
            "subject": self.subject or False,
            "since_date": fields.Date.today() - timedelta(days=window),
        })
        if self.state not in ("threat_confirmed", "simulation"):
            self.state = "threat_confirmed"
        return {
            "type": "ir.actions.act_window",
            "name": _("Retrait du courriel"),
            "res_model": "bf.clawback.operation",
            "res_id": op.id,
            "views": [[False, "form"]],
            "target": "current",
        }

    def action_view_clawbacks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Retraits de courriel"),
            "res_model": "bf.clawback.operation",
            "domain": [("reported_phish_id", "=", self.id)],
            "views": [[False, "list"], [False, "form"]],
        }

    def _notify_security_team_clawback(self, operation):
        """ntfy alert when a clawback executes (reuses the report ntfy config)."""
        url = self._icp("report_ntfy_url")
        if not url:
            return
        token = self._icp("report_ntfy_token")
        priority = self._icp("report_ntfy_priority", "high") or "high"
        body = _(
            "Retrait exécuté : %(removed)s courriel(s) déplacé(s) dans "
            "%(boxes)s boîte(s).\nSujet : %(subj)s\nLancé par : %(user)s"
        ) % {
            "removed": operation.messages_removed,
            "boxes": operation.mailboxes_swept,
            "subj": self.subject or "—",
            "user": self.env.user.display_name,
        }
        headers = {
            "Title": "Clawback execute",  # ASCII: HTTP header safe
            "Priority": str(priority),
            "Tags": "wastebasket,fishing_pole_and_fish",
        }
        if token:
            headers["Authorization"] = "Bearer %s" % token
        try:
            requests.post(url, data=body.encode("utf-8"),
                          headers=headers, timeout=10)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "bf_security_awareness: clawback ntfy failed for report %s: %s",
                self.id, exc)
