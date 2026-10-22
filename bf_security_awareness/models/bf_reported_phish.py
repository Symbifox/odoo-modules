import base64
import logging
import unicodedata
from datetime import timedelta
from email import message_from_bytes
from email.utils import parseaddr

import requests

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError

from .reader_lang import company_env, reader_env

_logger = logging.getLogger(__name__)

# Best-practice reporting taxonomy presented to employees. Shared with the
# report wizard so the dropdown and the stored value stay in sync.
REPORT_CATEGORIES = [
    ("phishing", "Phishing (credential theft)"),
    ("wire_fraud", "Wire fraud / CEO fraud"),
    ("malware", "Suspicious attachment or link"),
    ("impersonation", "Impersonation (coworker, vendor, executive)"),
    ("spam", "Spam / junk"),
    ("other", "Other / I'm not sure"),
]


def _ascii_header(text):
    """An HTTP header value: accents folded, anything else non-ASCII dropped."""
    return unicodedata.normalize("NFKD", text).encode(
        "ascii", "ignore").decode("ascii")


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
    _description = "Email reported as malicious"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(compute="_compute_name", store=True)
    reporter_id = fields.Many2one(
        "res.partner", string="Reported by", required=True,
        ondelete="cascade", index=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Company",
        default=lambda self: self.env.company, index=True)
    report_date = fields.Datetime(
        string="Reported on", default=fields.Datetime.now, tracking=True)

    category = fields.Selection(
        REPORT_CATEGORIES, string="Category", required=True,
        default="phishing", tracking=True)
    email_from = fields.Char(string="Reported sender")
    subject = fields.Char(string="Subject")
    details = fields.Text(string="Details")
    suspicious_link = fields.Char(string="Suspicious link")
    message_id = fields.Char(
        string="Message-ID",
        help="Unique internal ID of the email (taken from the attached "
             ".eml). Allows an exact removal from every mailbox.")

    clawback_operation_ids = fields.One2many(
        "bf.clawback.operation", "reported_phish_id", string="Removals")
    clawback_count = fields.Integer(compute="_compute_clawback_count")

    state = fields.Selection(
        [
            ("new", "New"),
            ("triage", "In triage"),
            ("threat_confirmed", "Threat confirmed"),
            ("false_alarm", "False alarm"),
            ("simulation", "Simulation (recognized)"),
        ],
        string="Status", default="new", required=True, tracking=True)

    is_simulation = fields.Boolean(
        string="Was a simulation", readonly=True, tracking=True)
    matched_result_id = fields.Many2one(
        "bf.phishing.result", string="Linked simulation result",
        readonly=True, ondelete="set null")
    campaign_id = fields.Many2one(
        "bf.phishing.campaign", string="Campaign",
        related="matched_result_id.campaign_id", store=True)

    # Soft helpdesk link: a plain id (no m2o) so the module stays installable
    # without any helpdesk addon.
    helpdesk_ticket_id = fields.Integer(
        string="Helpdesk ticket", readonly=True)
    has_ticket = fields.Boolean(compute="_compute_has_ticket")

    @api.depends("subject", "email_from", "reporter_id")
    def _compute_name(self):
        # Stored, read by the security team: their company's language, not the
        # reporter's nor the mail gateway's (OdooBot).
        for rec in self:
            env = company_env(rec, rec.company_id).env
            label = rec.subject or rec.email_from or env._("Suspicious email")
            rec.name = env._("Report: %s") % label

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
                "Email report rejected: external sender (%s).")
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

    def _security_team_view(self):
        """This report in the security team's language (its company's).

        The reporter, the mail gateway or a scheduled job writes; the security
        team reads the notes, the ticket and the alerts."""
        self.ensure_one()
        return company_env(self, self.company_id)

    def _category_label(self, env):
        """The category as ``env``'s language shows it."""
        return dict(self._fields["category"]._description_selection(env)).get(
            self.category, self.category)

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
            team = self._security_team_view()
            team.message_post(body=team.env._(
                "Report recognized: this email was part of an authorized "
                "simulation. No incident was created."))
            return {
                "is_simulation": True,
                "message": _(
                    "Well done! This email was a security simulation. "
                    "Your good reflex has been recorded, and no incident "
                    "was created."),
            }

        # Real suspicious email -> triage sequence.
        self.state = "triage"
        self._create_helpdesk_ticket()
        self._schedule_triage_activity()
        self._notify_security_team()
        team = self._security_team_view()
        team.message_post(body=team.env._(
            "Suspicious email sent to security triage."))
        return {
            "is_simulation": False,
            "message": _(
                "Thank you! Your report was sent to the security team for "
                "analysis."),
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
        # Read by the helpdesk agents: the security team's language.
        env = self._security_team_view().env
        cat = self._category_label(env)
        description = env._(
            "<p>Suspicious email reported by "
            "<strong>%(rep)s</strong>.</p><ul><li><strong>Category:</strong> "
            "%(cat)s</li><li><strong>Sender:</strong> "
            "%(from)s</li><li><strong>Subject:</strong> "
            "%(subj)s</li><li><strong>Link:</strong> "
            "%(link)s</li></ul><p><strong>Details:</strong><br/>%(details)s</p>"
        ) % {
            "rep": self.reporter_id.display_name or env._("(unknown)"),
            "cat": cat,
            "from": self.email_from or "—",
            "subj": self.subject or "—",
            "link": self.suspicious_link or "—",
            "details": (self.details or "—").replace("\n", "<br/>"),
        }
        vals = {
            "name": env._("Suspicious email: %s") % (
                self.subject or self.email_from or env._("(no subject)")),
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
        # The assignee reads it: their language, else the company's. Created in
        # that language too, so the assignment notice's subject follows.
        user = self._triage_user()
        rec = reader_env(
            self, user.lang,
            (self.company_id or self.env.company).sudo().partner_id.lang)
        rec.activity_schedule(
            "mail.mail_activity_data_todo",
            summary=rec.env._("Triage the reported suspicious email"),
            note=rec.env._("Reported by %s. Review it, confirm the threat or "
                           "mark it as a false alarm.")
            % self.reporter_id.display_name,
            user_id=user.id,
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
        env = self._security_team_view().env
        cat = self._category_label(env)
        body = env._(
            "Reported by: %(rep)s\nCategory: %(cat)s\nFrom: "
            "%(from)s\nSubject: %(subj)s"
        ) % {
            "rep": self.reporter_id.display_name or "?",
            "cat": cat,
            "from": self.email_from or "—",
            "subj": self.subject or "—",
        }
        headers = {
            "Title": _ascii_header(env._("Malicious email reported")),
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
                "This report is a simulation: no removal needed."))
        self.apply_eml_metadata()
        connector = self.env["bf.mail.clawback.connector"].search(
            [("active", "=", True)], limit=1)
        if not connector:
            raise UserError(_(
                "No active email connector. Set one up under Security "
                "Awareness › Configuration › Email connectors."))
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
            "name": _("Remove the email"),
            "res_model": "bf.clawback.operation",
            "res_id": op.id,
            "views": [[False, "form"]],
            "target": "current",
        }

    def action_view_clawbacks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Email removals"),
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
        env = self._security_team_view().env
        body = env._(
            "Removal done: %(removed)s email(s) moved in %(boxes)s "
            "mailbox(es).\nSubject: %(subj)s\nLaunched by: %(user)s"
        ) % {
            "removed": operation.messages_removed,
            "boxes": operation.mailboxes_swept,
            "subj": self.subject or "—",
            "user": self.env.user.display_name,
        }
        headers = {
            "Title": _ascii_header(env._("Clawback executed")),
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
