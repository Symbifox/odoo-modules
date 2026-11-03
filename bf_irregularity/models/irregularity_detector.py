import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

FAMILIES = [
    ("off_process", "Off-process"),
    ("late", "Late"),
    ("hazard", "Hazardous"),
    ("silent", "Silent"),
    ("orphan", "Orphaned"),
    ("abnormal", "Abnormal"),
]
SEVERITIES = [
    ("info", "Info"),
    ("warning", "Warning"),
    ("critical", "Critical"),
]


class BfIrregularityDetector(models.Model):
    """Un détecteur = une règle nommée, réglable, qui renvoie des constats.

    La logique vit dans une méthode ``_detect_<code>`` ajoutée par le module
    qui connaît le modèle visé (au besoin un pont). Elle renvoie une liste de
    dicts ``{"record", "key", "message", "owner"}``, ou un tuple
    ``(constats, clés_vérifiées)`` quand elle ne balaie qu'une fenêtre : seules
    les irrégularités dont la clé a été vérifiée peuvent alors se résoudre.
    """

    _name = "bf.irregularity.detector"
    _description = "Irregularity detector"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="Links the record to the method _detect_<code>.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(translate=True)
    family = fields.Selection(FAMILIES, required=True)
    severity = fields.Selection(SEVERITIES, required=True, default="warning")
    threshold = fields.Integer(help="Detector-specific threshold, usually a number of days.")
    threshold_label = fields.Char(translate=True)
    default_owner_id = fields.Many2one(
        "res.users", string="Fallback owner",
        default=lambda self: self.env.ref("base.user_admin", raise_if_not_found=False),
        help="Receives the irregularities for which no responsible person can be found.",
    )
    notify_activity = fields.Boolean(
        string="Schedule an activity",
        help="When a new irregularity appears, schedule a to-do for its owner on the affected record.",
    )
    last_run = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    irregularity_ids = fields.One2many("bf.irregularity", "detector_id")
    open_count = fields.Integer(compute="_compute_open_count")

    _sql_constraints = [
        ("code_unique", "unique(code)", "A detector code must be unique."),
    ]

    @api.depends("irregularity_ids.state")
    def _compute_open_count(self):
        groups = self.env["bf.irregularity"]._read_group(
            [("detector_id", "in", self.ids), ("state", "=", "open")],
            ["detector_id"], ["__count"],
        )
        counts = {detector.id: count for detector, count in groups}
        for detector in self:
            detector.open_count = counts.get(detector.id, 0)

    # ------------------------------------------------------------------ run

    @api.model
    def _cron_run_all(self):
        for detector in self.search([]):
            detector._run()

    def action_run(self):
        """Lance les détecteurs sélectionnés.

        ⚠️ Méthode publique, donc atteignable par ``/web/dataset/call_kw`` : la
        garde du menu ne protège rien. Les détecteurs écrivent en ``sudo()`` et
        peuvent planifier des activités pour d'autres personnes, donc on exige
        ici le droit d'écriture sur le détecteur, que l'ACL ne donne qu'à
        ``base.group_system``.
        """
        self.check_access("write")
        for detector in self:
            detector._run()
        return True

    def action_open_irregularities(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_irregularity.action_bf_irregularity")
        action["domain"] = [("detector_id", "=", self.id)]
        action["context"] = {"search_default_filter_active": 1}
        return action

    def _run(self):
        self.ensure_one()
        method = getattr(self, "_detect_%s" % self.code, None)
        if method is None:
            self.sudo().write({"last_error": _("No method _detect_%s is installed.", self.code)})
            return
        try:
            with self.env.cr.savepoint():
                result = method()
                if isinstance(result, tuple):
                    detections, checked_keys = result
                else:
                    detections, checked_keys = result, None
                self._sync(detections, checked_keys)
        except Exception as exc:  # un détecteur cassé ne doit pas bloquer les autres
            _logger.exception("Irregularity detector %s failed", self.code)
            self.sudo().write({"last_run": fields.Datetime.now(), "last_error": str(exc)})
            return
        self.sudo().write({"last_run": fields.Datetime.now(), "last_error": False})

    def _sync(self, detections, checked_keys=None):
        self.ensure_one()
        Irregularity = self.env["bf.irregularity"].sudo()
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        existing = {
            irr.key: irr
            for irr in Irregularity.with_context(active_test=False).search([("detector_id", "=", self.id)])
        }
        seen = set()
        for det in detections:
            record = det["record"]
            key = det.get("key") or "%s,%s" % (record._name, record.id)
            if key in seen:
                continue
            seen.add(key)
            owner = det.get("owner") or self.default_owner_id
            vals = {
                "name": det["message"],
                "res_model": record._name,
                "res_id": record.id,
                "owner_id": owner.id if owner else False,
                "severity": det.get("severity") or self.severity,
                "last_seen": now,
            }
            irr = existing.get(key)
            if irr is None:
                irr = Irregularity.create(dict(vals, detector_id=self.id, key=key, first_seen=now))
                irr._notify_new()
                continue
            if irr.state == "resolved" or (irr.state == "muted" and irr.mute_until and irr.mute_until <= today):
                vals.update(state="open", first_seen=now if irr.state == "resolved" else irr.first_seen,
                            resolved_date=False, mute_until=False)
            irr.write(vals)
        stale = [
            irr for key, irr in existing.items()
            if key not in seen and irr.state != "resolved"
            and (checked_keys is None or key in checked_keys)
        ]
        if stale:
            Irregularity.browse([irr.id for irr in stale]).write({"state": "resolved", "resolved_date": now})

    # ------------------------------------------------------------ helpers

    def _days_ago(self, days):
        return fields.Date.context_today(self) - timedelta(days=days)

    @api.model
    def _user_of_partner(self, partner):
        users = partner.with_context(active_test=False).user_ids if partner else self.env["res.users"]
        return users.filtered("active")[:1]

    @api.model
    def _source_record(self, model, res_id):
        """Document visé par un message ou une activité, s'il existe encore."""
        if not model or not res_id or model not in self.env:
            return None
        record = self.env[model].browse(res_id).exists()
        return record or None

    # ------------------------------------------------------ core detectors

    def _detect_activity_overdue(self):
        limit = self._days_ago(self.threshold or 7)
        activities = self.env["mail.activity"].sudo().search([("date_deadline", "<", limit)])
        today = fields.Date.context_today(self)
        detections = []
        for act in activities:
            record = self._source_record(act.res_model, act.res_id) or act
            days = (today - act.date_deadline).days
            detections.append({
                "record": record,
                "key": "mail.activity,%s" % act.id,
                "owner": act.user_id if act.user_id.active else None,
                "message": _("Activity “%(summary)s” overdue for %(days)s days", summary=act.summary or act.activity_type_id.name, days=days),
            })
        return detections

    def _detect_activity_inactive_user(self):
        Users = self.env["res.users"].with_context(active_test=False)
        inactive = Users.search([("active", "=", False)])
        activities = self.env["mail.activity"].sudo().search([("user_id", "in", inactive.ids)])
        detections = []
        for act in activities:
            record = self._source_record(act.res_model, act.res_id) or act
            detections.append({
                "record": record,
                "key": "mail.activity,%s" % act.id,
                "message": _("Activity “%(summary)s” is assigned to %(user)s, whose account is archived", summary=act.summary or act.activity_type_id.name, user=act.user_id.name),
            })
        return detections

    def _detect_scheduled_message_stuck(self):
        # Le cron natif poste chaque minute : une heure de retard, c'est un envoi coincé.
        limit = fields.Datetime.now() - timedelta(hours=self.threshold or 1)
        messages = self.env["mail.scheduled.message"].sudo().search([("scheduled_date", "<", limit)])
        detections = []
        for msg in messages:
            detections.append({
                "record": self._source_record(msg.model, msg.res_id) or msg,
                "key": "mail.scheduled.message,%s" % msg.id,
                "owner": self._user_of_partner(msg.author_id) or msg.create_uid,
                "message": _("Scheduled message “%(subject)s” should have gone out on %(date)s", subject=msg.subject or _("(no subject)"), date=fields.Datetime.to_string(msg.scheduled_date)),
            })
        return detections

    def _detect_mail_failed(self):
        mails = self.env["mail.mail"].sudo().search([("state", "=", "exception")])
        detections = []
        for mail in mails:
            reason = (mail.failure_reason or "").strip().splitlines()
            detections.append({
                "record": mail,
                "owner": self._user_of_partner(mail.author_id),
                "message": _("Email “%(subject)s” failed: %(reason)s", subject=mail.subject or _("(no subject)"), reason=reason[-1] if reason else _("unknown reason")),
            })
        return detections
