"""Inventaire des lignes (DID) VOIP.ms autorisées à envoyer/recevoir.

Générique : aucun numéro codé en dur. Chaque ligne rattache un DID VOIP.ms à un
propriétaire Odoo (``owner_id``), porte le jeton secret du webhook entrant et les
indicateurs SMS/MMS. Peuplable automatiquement via ``getDIDsInfo`` ou manuellement.

Une ligne peut être *partagée* : ``user_ids`` liste les collègues qui peuvent
l'employer et lire les conversations tenues dessus, en plus du propriétaire. Le
partage se lit dans les règles d'accès (``security/sms_security.xml``) et dans
``_users_for_line`` ; un fil marqué « Confidentiel » reste au seul propriétaire.
"""
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .voipms_time import voipms_date_to_ms

_logger = logging.getLogger(__name__)


class SmsArchiveLine(models.Model):
    _name = "sms.archive.line"
    _description = "VOIP.ms SMS/MMS line"
    _rec_name = "label"
    _order = "label, did"

    label = fields.Char(
        string="Label",
        required=True,
        help="Readable name of the line (e.g. \"Main line\", \"Office "
             "phone\").",
    )
    did = fields.Char(
        string="DID (VOIP.ms)",
        required=True,
        help="Number as it appears in the VOIP.ms account (10 digits, "
             "e.g. 5145551234).",
    )
    did_normalized = fields.Char(
        string="DID (E.164)",
        compute="_compute_did_normalized",
        store=True,
        index=True,
    )
    owner_id = fields.Many2one(
        comodel_name="res.users",
        string="Owner",
        required=True,
        default=lambda self: self.env.user,
        index=True,
        help="Messages sent/received on this line will be assigned to "
             "this user.",
    )
    user_ids = fields.Many2many(
        comodel_name="res.users",
        relation="sms_archive_line_user_rel",
        column1="line_id",
        column2="user_id",
        string="Shared users",
        help="Colleagues allowed to send from this line and to read the "
             "conversations held on it, just like the owner. Threads "
             "marked \"Confidential\" stay hidden from them.",
    )
    is_shared = fields.Boolean(
        string="Shared",
        compute="_compute_is_shared",
        help="True as soon as at least one colleague has access to this line.",
    )
    is_default = fields.Boolean(
        string="Default line",
        help="Default sending line for this owner.",
    )
    sms_enabled = fields.Boolean(string="SMS enabled", default=True)
    mms_enabled = fields.Boolean(string="MMS enabled", default=True)
    active = fields.Boolean(string="Active", default=True)
    voipms_subaccount = fields.Char(string="VOIP.ms sub-account")
    webhook_token = fields.Char(
        string="Webhook token",
        required=True,
        copy=False,
        default=lambda self: secrets.token_urlsafe(24),
        groups="bf_sms_archive.group_sms_manager",
        help="Secret included in the VOIP.ms callback URL to authenticate "
             "this line's incoming messages.",
    )
    webhook_url = fields.Char(
        string="Callback URL",
        compute="_compute_webhook_url",
        groups="bf_sms_archive.group_sms_manager",
        help="To copy into VOIP.ms (or set with the \"Configure the "
             "webhook\" button). Contains the secret token: treat it as a "
             "secret (keep it out of logs).",
    )
    note = fields.Text(string="Notes")

    _sql_constraints = [
        ("did_uniq", "UNIQUE(did)", "This line (DID) already exists."),
        ("token_uniq", "UNIQUE(webhook_token)", "Webhook token already in use."),
    ]

    @api.depends("user_ids")
    def _compute_is_shared(self):
        for line in self:
            line.is_shared = bool(line.user_ids)

    def _users_for_line(self):
        """Propriétaire + collègues partagés, dédoublonnés (utilisateurs actifs)."""
        users = self.env["res.users"].browse()
        for line in self.sudo():
            users |= line.owner_id | line.user_ids
        return users.filtered(lambda u: u.active)

    @api.model
    def _lines_for_user(self, user=None):
        """Lignes utilisables par ``user`` : les siennes d'abord, puis les partagées.

        Lecture en ``sudo`` volontaire — le tri « les miennes d'abord » doit voir
        les deux familles, et la règle d'accès sur ``sms.archive.line`` rendrait
        déjà le même ensemble."""
        user = user or self.env.user
        lines = self.sudo().search([
            "|", ("owner_id", "=", user.id), ("user_ids", "in", [user.id]),
        ])
        return lines.sorted(key=lambda ln: (ln.owner_id.id != user.id, ln.label or "", ln.did or ""))

    def _is_usable_by(self, user=None):
        """Vrai si ``user`` peut envoyer depuis cette ligne (propriétaire, partagé
        ou gestionnaire)."""
        self.ensure_one()
        user = user or self.env.user
        line = self.sudo()
        return (
            line.owner_id.id == user.id
            or user.id in line.user_ids.ids
            or user.has_group("bf_sms_archive.group_sms_manager")
        )

    @api.depends("did")
    def _compute_did_normalized(self):
        Thread = self.env["sms.archive.thread"]
        for line in self:
            line.did_normalized = Thread.normalize_phone(line.did) if line.did else False

    # Non stocké ; le marqueur d'un jeton absent est un texte traduit.
    @api.depends("webhook_token")
    @api.depends_context("lang")
    def _compute_webhook_url(self):
        base = (
            self.env["ir.config_parameter"].sudo().get_param("bf_sms_archive.public_base_url", "")
            or self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
            or ""
        ).rstrip("/")
        for line in self:
            # webhook_token est restreint au groupe gestionnaire → lecture sudo.
            token = line.sudo().webhook_token or self.env._("<token>")
            line.webhook_url = (
                f"{base}/bf_sms_archive/api/voipms/sms"
                f"?token={token}&to={{TO}}&from={{FROM}}"
                f"&message={{MESSAGE}}&id={{ID}}&date={{TIMESTAMP}}&media={{MEDIA}}"
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._enforce_single_default()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get("is_default"):
            self._enforce_single_default()
        return res

    def _enforce_single_default(self):
        """Une seule ligne par défaut par propriétaire."""
        for line in self.filtered("is_default"):
            self.search([
                ("owner_id", "=", line.owner_id.id),
                ("is_default", "=", True),
                ("id", "!=", line.id),
            ]).write({"is_default": False})

    def action_regenerate_token(self):
        # L'écriture se fait en sudo : le droit se contrôle donc ici, sans
        # quoi le webhook d'une ligne pourrait être coupé par un tiers.
        # Le propriétaire garde le geste (l'ACL usager est en lecture seule,
        # c'est pour ça que la méthode passe en sudo), le gestionnaire aussi.
        if not self.env.su and not self.env.user.has_group("bf_sms_archive.group_sms_manager"):
            self.check_access("read")
            if self.sudo().filtered(lambda l: l.owner_id != self.env.user):
                raise AccessError(_("Only a line's owner can regenerate "
                                    "its token."))
        for line in self:
            line.sudo().write({"webhook_token": secrets.token_urlsafe(24)})
        return True

    @api.model
    def _voipms_did_value(self, did):
        """Forme attendue par l'API VOIP.ms pour un DID (chiffres, NANP → 10)."""
        digits = re.sub(r"\D", "", did or "")
        if len(digits) == 11 and digits.startswith("1"):
            return digits[1:]
        return digits

    def action_configure_webhook(self):
        """Pose l'URL de callback de cette ligne dans VOIP.ms (setSMS)."""
        Voipms = self.env["sms.archive.voipms"]
        cfg = Voipms._voipms_config()
        if not cfg["public_base_url"]:
            raise UserError(self.env._(
                "First set \"Public URL\" "
                "(bf_sms_archive.public_base_url) in the settings."
            ))
        for line in self:
            url = (
                f"{cfg['public_base_url']}/bf_sms_archive/api/voipms/sms"
                f"?token={line.webhook_token}&to={{TO}}&from={{FROM}}"
                f"&message={{MESSAGE}}&id={{ID}}&date={{TIMESTAMP}}&media={{MEDIA}}"
            )
            Voipms._voipms_set_callback(self._voipms_did_value(line.did), url)
        return True

    @api.model
    def action_sync_lines(self):
        """Crée/mets à jour les lignes depuis getDIDsInfo (sans écraser owner/jeton)."""
        Voipms = self.env["sms.archive.voipms"]
        dids = Voipms._voipms_fetch_dids()
        created = updated = 0
        for d in dids:
            number = str(d.get("did") or "").strip()
            if not number:
                continue
            sms_avail = str(d.get("sms_available", "1")) in ("1", "true", "True")
            existing = self.search([("did", "=", number)], limit=1)
            vals = {
                "sms_enabled": sms_avail,
                "voipms_subaccount": d.get("routing") or d.get("pop") or False,
            }
            if existing:
                # ne pas écraser le libellé/propriétaire fixés manuellement
                existing.write({k: v for k, v in vals.items() if not existing[k]})
                updated += 1
            else:
                vals.update({
                    "label": d.get("description") or number,
                    "did": number,
                })
                self.create(vals)
                created += 1
        _logger.info("VOIP.ms lignes : %s créées, %s mises à jour", created, updated)
        return True

    @api.model
    def _cron_poll_voipms(self):
        """Filet de sécurité : re-fetch des SMS récents (getSMS) et réconciliation.

        Idempotent grâce à la déduplication par ``voipms_id``. Couvre un message
        manqué par le webhook (callback perdu) sans jamais créer de doublon.
        """
        Voipms = self.env["sms.archive.voipms"]
        if not Voipms._voipms_config()["enabled"]:
            return
        Msg = self.env["sms.archive.message"].sudo()
        today = datetime.now(tz=timezone.utc).date()
        yday = today - timedelta(days=1)
        for line in self.search([("active", "=", True), ("sms_enabled", "=", True)]):
            try:
                rows = Voipms._voipms_get_sms(
                    did=self._voipms_did_value(line.did),
                    from_date=yday.strftime("%Y-%m-%d"),
                    to_date=today.strftime("%Y-%m-%d"),
                )
            except Exception:
                _logger.warning("getSMS échoué pour la ligne %s", line.id, exc_info=True)
                continue
            for row in rows:
                vid = str(row.get("id") or "")
                if not vid:
                    continue
                if Msg.search_count([("voipms_id", "=", vid)]):
                    continue
                typ = str(row.get("type") or "")
                direction = "in" if typ in ("1", "received", "in") else "out"
                rec, created = Msg._ingest_one(
                    phone_raw=row.get("contact") or "",
                    owner_id=line.owner_id.id,
                    direction=direction,
                    body=row.get("message") or "",
                    date_ms=voipms_date_to_ms(row.get("date"), env=self.env),
                    is_mms=False,
                    batch_id="voipms-poll",
                    voipms_id=vid,
                    line_id=line.id,
                    delivery_state="received" if direction == "in" else "sent",
                    is_read=(direction != "in"),
                )
                if created and direction == "in":
                    rec._notify_bus(kind="new")
                if created:
                    rec._auto_post_to_task()
        return True
