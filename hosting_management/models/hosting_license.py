# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""hosting.license — Pool de licences logicielles.

Représente un lot/batch de licences acheté (ex. : 5 clés Windows 11 Pro, ou
1 JWT Grist Enterprise pour 15 sièges). Les sièges individuels sont modélisés
dans hosting.license.seat.
"""
import base64
import json
import logging
from datetime import date, datetime, timedelta

from markupsafe import escape as _esc

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class HostingLicense(models.Model):
    _name = "hosting.license"
    _description = "Pool de licences logicielles"
    _order = "name"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Nom", required=True, tracking=True)
    code = fields.Char(
        string="Référence",
        readonly=True,
        copy=False,
        default="New",
    )
    software_id = fields.Many2one(
        comodel_name="hosting.software",
        string="Logiciel (catalogue)",
        tracking=True,
        help="Optionnel — référencer un logiciel du catalogue Hébergement.",
    )
    product_name = fields.Char(
        string="Produit (texte libre)",
        help="Utilisé si le logiciel n'est pas au catalogue.",
    )
    vendor = fields.Char(string="Éditeur")
    license_type = fields.Selection(
        selection=[
            ("per_device", "Une clé par poste"),
            ("per_user", "Une clé par utilisateur"),
            ("volume", "Clé volume (N sièges)"),
            ("jwt_token", "Token signé (JWT)"),
            ("subscription", "Abonnement"),
        ],
        string="Type",
        default="per_device",
        required=True,
        tracking=True,
    )

    owner_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Propriétaire",
        required=True,
        tracking=True,
        index=True,
        default=lambda self: self.env.company.partner_id,
        help="Entité qui a acheté la licence (Blue Fox Inc. pour les pools "
        "mutualisés, ou le partenaire client).",
    )
    purchase_date = fields.Date(string="Date d'achat", tracking=True)
    purchase_invoice_id = fields.Many2one(
        comodel_name="account.move",
        string="Facture d'achat",
        domain="[('move_type', 'in', ['in_invoice', 'in_refund'])]",
        help="Facture fournisseur pour traçabilité financière.",
    )

    seats_total = fields.Integer(string="Sièges achetés", default=1, tracking=True)
    seats_used = fields.Integer(
        string="Sièges activés",
        compute="_compute_seat_stats",
        store=True,
    )
    seats_free = fields.Integer(
        string="Sièges disponibles",
        compute="_compute_seat_stats",
        store=True,
    )
    over_allocated = fields.Boolean(
        string="Sur-allocation",
        compute="_compute_seat_stats",
        store=True,
    )

    expiry_date = fields.Date(string="Date d'expiration", tracking=True)

    # JWT (champs sensibles)
    jwt_token = fields.Char(
        string="JWT (token signé)",
        groups="hosting_management.group_hosting_manager",
        help="Pour licences de type JWT (ex. Grist Enterprise). Stocké en clair, "
        "lisible uniquement par les gestionnaires.",
    )
    jwt_installation_id = fields.Char(
        string="Installation ID (JWT)",
        readonly=True,
        copy=False,
    )

    seat_ids = fields.One2many(
        comodel_name="hosting.license.seat",
        inverse_name="license_id",
        string="Sièges",
    )

    notes = fields.Html(string="Notes")
    active = fields.Boolean(default=True)

    _SENSITIVE_FIELDS = ("jwt_token",)

    @api.depends("seat_ids.state", "seats_total")
    def _compute_seat_stats(self):
        for lic in self:
            used = len(lic.seat_ids.filtered(lambda s: s.state == "activated"))
            lic.seats_used = used
            lic.seats_free = max(lic.seats_total - used, 0)
            lic.over_allocated = used > lic.seats_total

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code") or vals.get("code") == "New":
                vals["code"] = (
                    self.env["ir.sequence"].next_by_code("hosting.license")
                    or "LIC-NEW"
                )
            # Décode JWT à la création si fourni
            if vals.get("jwt_token") and vals.get("license_type") == "jwt_token":
                decoded = self._decode_jwt(vals["jwt_token"])
                vals.setdefault("expiry_date", decoded.get("expiry_date"))
                vals.setdefault("seats_total", decoded.get("seats_total") or vals.get("seats_total", 1))
                vals.setdefault("jwt_installation_id", decoded.get("installation_id"))
        records = super().create(vals_list)
        for rec, vals in zip(records, vals_list):
            sensitive = [f for f in self._SENSITIVE_FIELDS if vals.get(f)]
            if sensitive:
                rec._audit_sensitive_writes(sensitive, action="set")
        return records

    def write(self, vals):
        sensitive_changed = [f for f in self._SENSITIVE_FIELDS if f in vals]
        if vals.get("jwt_token"):
            decoded = self._decode_jwt(vals["jwt_token"])
            # Auto-remplir uniquement si l'utilisateur ne saisit pas explicitement
            for key, mapped in (
                ("expiry_date", "expiry_date"),
                ("seats_total", "seats_total"),
                ("jwt_installation_id", "installation_id"),
            ):
                if mapped in decoded and key not in vals:
                    vals[key] = decoded[mapped]
        result = super().write(vals)
        if sensitive_changed:
            for rec in self:
                actions = {
                    f: ("set" if vals.get(f) else "cleared")
                    for f in sensitive_changed
                }
                rec._audit_sensitive_writes(
                    list(actions.keys()),
                    action=",".join(f"{k}={v}" for k, v in actions.items()),
                )
        return result

    def _audit_sensitive_writes(self, field_names, action):
        AuditLog = self.env.get("hosting.audit.log")
        if AuditLog is None:
            return
        for rec in self:
            AuditLog._log_event(
                action_type="config_change",
                category="security",
                description=(
                    f"Champ sensible modifié sur la licence {rec.name} : "
                    f"{', '.join(field_names)} ({action}). Valeur non journalisée."
                ),
                res_model="hosting.license",
                res_id=rec.id,
                res_name=rec.name,
                field_name=",".join(field_names),
                severity="warning",
                status="success",
            )

    # ------------------------------------------------------------------
    # JWT decoding
    # ------------------------------------------------------------------
    @api.model
    def _decode_jwt(self, token):
        """Décode le segment payload d'un JWT (sans vérifier la signature).

        Retourne un dict avec autant de champs reconnus que possible :
        ``expiry_date`` (date), ``seats_total`` (int), ``installation_id`` (str).
        Renvoie un dict vide si le token est invalide.
        """
        try:
            if not token or "." not in token:
                return {}
            parts = token.strip().split(".")
            if len(parts) < 2:
                return {}
            payload_b64 = parts[1]
            padding = 4 - (len(payload_b64) % 4)
            if padding != 4:
                payload_b64 += "=" * padding
            raw = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(raw)
        except Exception:
            _logger.exception("JWT decode failed")
            return {}

        result = {}
        end = payload.get("end") or payload.get("exp")
        if end:
            try:
                if isinstance(end, (int, float)):
                    result["expiry_date"] = date.fromtimestamp(int(end))
                else:
                    # ISO 8601 ex. "2026-11-18T00:00:00.000Z"
                    result["expiry_date"] = datetime.fromisoformat(
                        end.replace("Z", "+00:00")
                    ).date()
            except Exception:
                pass
        if payload.get("installationId"):
            result["installation_id"] = str(payload["installationId"])
        features = payload.get("features") or {}
        if features.get("installationSeats"):
            try:
                result["seats_total"] = int(features["installationSeats"])
            except (TypeError, ValueError):
                pass
        return result

    def action_decode_jwt(self):
        """Bouton form pour relancer le decode manuellement."""
        for lic in self:
            if not lic.jwt_token:
                continue
            decoded = self._decode_jwt(lic.jwt_token)
            update = {}
            if "expiry_date" in decoded:
                update["expiry_date"] = decoded["expiry_date"]
            if "seats_total" in decoded:
                update["seats_total"] = decoded["seats_total"]
            if "installation_id" in decoded:
                update["jwt_installation_id"] = decoded["installation_id"]
            if update:
                lic.write(update)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Décodage JWT"),
                "message": _("Champs synchronisés depuis le token."),
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    # Cron expiry alerts
    # ------------------------------------------------------------------
    @api.model
    def _cron_expiry_alerts(self):
        """Digest hebdo des licences expirant dans ≤ 90 jours."""
        today = fields.Date.today()
        horizon = today + timedelta(days=90)
        licenses = self.search(
            [
                ("expiry_date", "!=", False),
                ("expiry_date", "<=", horizon),
                ("active", "=", True),
            ],
            order="owner_partner_id, expiry_date",
        )
        if not licenses:
            return

        by_partner = {}
        for lic in licenses:
            by_partner.setdefault(lic.owner_partner_id, self.env["hosting.license"])
            by_partner[lic.owner_partner_id] |= lic

        Mail = self.env["mail.mail"].sudo()
        for partner, lics in by_partner.items():
            try:
                # Destinataire : responsable hosting principal du partner,
                # ou admin si propriétaire = company root
                if partner == self.env.company.partner_id:
                    recipient_user = self.env.user  # admin sera env.user dans le cron
                else:
                    service = self.env["hosting.service"].sudo().search(
                        [("partner_id", "=", partner.id)], limit=1
                    )
                    recipient_user = service.user_id if service else False
                if not recipient_user or not recipient_user.email:
                    continue

                rows = []
                for lic in lics:
                    expired = lic.expiry_date < today
                    days_left = (lic.expiry_date - today).days
                    status = _("Expirée") if expired else f"{days_left} j"
                    rows.append(
                        "<tr>"
                        f"<td>{_esc(lic.code or '')}</td>"
                        f"<td>{_esc(lic.name)}</td>"
                        f"<td>{_esc(lic.vendor or '')}</td>"
                        f"<td>{_esc(str(lic.expiry_date))}</td>"
                        f"<td>{lic.seats_total}</td>"
                        f"<td style='color:{'#c00' if expired else '#c80'};'>"
                        f"{_esc(status)}</td>"
                        "</tr>"
                    )
                body = (
                    f"<p>Bonjour {_esc(recipient_user.name)},</p>"
                    f"<p>Licences appartenant à <strong>{_esc(partner.name)}</strong> "
                    "qui expirent dans les 90 prochains jours :</p>"
                    "<table border='1' cellpadding='6' cellspacing='0' "
                    "style='border-collapse:collapse;font-family:sans-serif;font-size:13px;'>"
                    "<thead><tr style='background:#eee;'>"
                    "<th>Réf.</th><th>Nom</th><th>Éditeur</th><th>Expiration</th>"
                    "<th>Sièges</th><th>Statut</th></tr></thead>"
                    f"<tbody>{''.join(rows)}</tbody></table>"
                    "<p>— Module Hébergement</p>"
                )
                Mail.create({
                    "subject": f"[Licences] {partner.name} — {len(lics)} expiration(s) ≤ 90 j",
                    "body_html": body,
                    "email_to": recipient_user.email,
                    "author_id": self.env.user.partner_id.id,
                }).send()
            except Exception:
                _logger.exception(
                    "hosting.license: échec digest expiry pour partner_id=%s",
                    partner.id,
                )
                continue

    @api.depends("code", "name")
    def _compute_display_name(self):
        for lic in self:
            lic.display_name = f"[{lic.code}] {lic.name}" if lic.code and lic.code != "New" else (lic.name or "")
