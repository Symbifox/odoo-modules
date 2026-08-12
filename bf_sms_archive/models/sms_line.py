"""Inventaire des lignes (DID) VOIP.ms autorisées à envoyer/recevoir.

Générique : aucun numéro codé en dur. Chaque ligne rattache un DID VOIP.ms à un
propriétaire Odoo (``owner_id``), porte le jeton secret du webhook entrant et les
indicateurs SMS/MMS. Peuplable automatiquement via ``getDIDsInfo`` ou manuellement.
"""
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from odoo import api, fields, models
from odoo.exceptions import UserError

from .voipms_time import voipms_date_to_ms

_logger = logging.getLogger(__name__)


class SmsArchiveLine(models.Model):
    _name = "sms.archive.line"
    _description = "Ligne SMS/MMS VOIP.ms"
    _rec_name = "label"
    _order = "label, did"

    label = fields.Char(
        string="Libellé",
        required=True,
        help="Nom lisible de la ligne (ex. « Ligne principale », « Téléphone bureau »).",
    )
    did = fields.Char(
        string="DID (VOIP.ms)",
        required=True,
        help="Numéro tel que présent dans le compte VOIP.ms (10 chiffres, ex. 5145551234).",
    )
    did_normalized = fields.Char(
        string="DID (E.164)",
        compute="_compute_did_normalized",
        store=True,
        index=True,
    )
    owner_id = fields.Many2one(
        comodel_name="res.users",
        string="Propriétaire",
        required=True,
        default=lambda self: self.env.user,
        index=True,
        help="Les messages envoyés/reçus sur cette ligne seront imputés à cet utilisateur.",
    )
    is_default = fields.Boolean(
        string="Ligne par défaut",
        help="Ligne d'envoi par défaut pour ce propriétaire.",
    )
    sms_enabled = fields.Boolean(string="SMS activé", default=True)
    mms_enabled = fields.Boolean(string="MMS activé", default=True)
    active = fields.Boolean(string="Actif", default=True)
    voipms_subaccount = fields.Char(string="Sous-compte VOIP.ms")
    webhook_token = fields.Char(
        string="Jeton webhook",
        required=True,
        copy=False,
        default=lambda self: secrets.token_urlsafe(24),
        groups="bf_sms_archive.group_sms_manager",
        help="Secret inclus dans l'URL de callback VOIP.ms pour authentifier les "
             "messages entrants de cette ligne.",
    )
    webhook_url = fields.Char(
        string="URL de callback",
        compute="_compute_webhook_url",
        groups="bf_sms_archive.group_sms_manager",
        help="À copier dans VOIP.ms (ou poser via le bouton « Configurer le webhook »). "
             "Contient le jeton secret : traiter comme un secret (tenir hors des logs).",
    )
    note = fields.Text(string="Notes")

    _sql_constraints = [
        ("did_uniq", "UNIQUE(did)", "Cette ligne (DID) existe déjà."),
        ("token_uniq", "UNIQUE(webhook_token)", "Jeton webhook déjà utilisé."),
    ]

    @api.depends("did")
    def _compute_did_normalized(self):
        Thread = self.env["sms.archive.thread"]
        for line in self:
            line.did_normalized = Thread.normalize_phone(line.did) if line.did else False

    @api.depends("webhook_token")
    def _compute_webhook_url(self):
        base = (
            self.env["ir.config_parameter"].sudo().get_param("bf_sms_archive.public_base_url", "")
            or self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
            or ""
        ).rstrip("/")
        for line in self:
            # webhook_token est restreint au groupe gestionnaire → lecture sudo.
            token = line.sudo().webhook_token or "<jeton>"
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
            raise UserError(
                "Réglez d'abord « URL publique » (bf_sms_archive.public_base_url) "
                "dans les paramètres."
            )
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
        return True
