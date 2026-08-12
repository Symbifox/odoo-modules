# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""hosting.endpoint — Poste de travail / serveur du parc client (clients SLA).

Section ajoutée au module hosting_management pour suivre l'inventaire matériel
des clients sous SLA, avec champs opérationnels (BitLocker, Action1, Intune,
Entra) et alertes de fin de garantie / OS EOL.
"""
import logging
from datetime import date, timedelta

from markupsafe import escape as _esc

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


# OS dont la fin de support tombe avant aujourd'hui → tag EOL automatique.
# Sources : Microsoft Lifecycle, Apple, Ubuntu LTS.
OS_EOL_DATES = {
    "windows_10": date(2025, 10, 14),
}


class HostingEndpoint(models.Model):
    _name = "hosting.endpoint"
    _description = "Poste / Serveur du parc client"
    _order = "partner_id, name"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    # --- Identification ---
    name = fields.Char(
        string="Nom",
        required=True,
        tracking=True,
        help="Hostname, nom convivial ou tag d'inventaire",
    )
    code = fields.Char(
        string="Référence",
        readonly=True,
        copy=False,
        default="New",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Client",
        required=True,
        tracking=True,
        domain="[('is_company', '=', True)]",
        index=True,
    )
    endpoint_type = fields.Selection(
        selection=[
            ("workstation", "Poste de travail"),
            ("laptop", "Portable"),
            ("server", "Serveur"),
            ("vm", "VM"),
            ("mobile", "Mobile"),
            ("other", "Autre"),
        ],
        string="Type",
        default="workstation",
        required=True,
        tracking=True,
    )
    lifecycle_state = fields.Selection(
        selection=[
            ("in_stock", "En stock"),
            ("deployed", "Déployé"),
            ("in_repair", "En réparation"),
            ("retired", "Retiré"),
        ],
        string="Cycle de vie",
        default="deployed",
        required=True,
        tracking=True,
    )

    # --- Utilisateur assigné (côté client, pas un res.users BF) ---
    assigned_type = fields.Selection(
        selection=[
            ("contact", "Contact"),
            ("matrix_item", "Élément de matrice"),
            ("free_text", "Texte libre"),
        ],
        string="Type d'usager",
        default="contact",
        required=True,
        tracking=True,
    )
    assigned_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contact usager",
        domain="[('is_company', '=', False)]",
        tracking=True,
        help="Personne (contact) qui utilise ce poste.",
    )
    assigned_matrix_item_id = fields.Many2one(
        comodel_name="project.knowledge.item",
        string="Élément de matrice",
        tracking=True,
        help="Élément de matrice de connaissances correspondant à l'usager / rôle.",
    )
    assigned_user_name = fields.Char(string="Usager (texte libre)", tracking=True)
    assigned_email = fields.Char(string="Courriel usager")
    assigned_display = fields.Char(
        string="Usager",
        compute="_compute_assigned_display",
        store=True,
    )

    # --- Matériel ---
    serial = fields.Char(string="Numéro de série", tracking=True)
    manufacturer = fields.Char(string="Fabricant")
    model_name = fields.Char(string="Modèle")

    # --- Système & réseau ---
    os = fields.Selection(
        selection=[
            ("windows_10", "Windows 10"),
            ("windows_11", "Windows 11"),
            ("windows_server", "Windows Server"),
            ("macos", "macOS"),
            ("linux_ubuntu", "Ubuntu"),
            ("linux_debian", "Debian"),
            ("linux_other", "Linux (autre)"),
            ("ios", "iOS / iPadOS"),
            ("android", "Android"),
            ("other", "Autre"),
        ],
        string="OS",
    )
    os_build = fields.Char(string="Build OS")
    hostname = fields.Char(string="Hostname")
    mac_address = fields.Char(string="Adresse MAC")
    ip_address = fields.Char(string="Adresse IP")

    # --- Lifecycle financier ---
    purchase_date = fields.Date(string="Date d'achat")
    warranty_end = fields.Date(string="Fin de garantie", tracking=True)

    # --- Tag EOL (computed stored) ---
    os_eol_tag = fields.Char(
        string="Étiquette EOL",
        compute="_compute_os_eol_tag",
        store=True,
        help="Renseigné automatiquement si l'OS est en fin de support.",
    )

    # --- RMM / MDM links ---
    action1_url = fields.Char(
        string="URL console Action1",
        help="Lien direct vers la fiche endpoint dans la console Action1.",
    )
    action1_endpoint_id = fields.Char(
        string="ID endpoint Action1",
        index=True,
        copy=False,
        help="ID unique de l'endpoint dans Action1. Renseigné automatiquement par "
        "la synchronisation. Sert de clé idempotente.",
    )
    action1_last_sync = fields.Datetime(
        string="Dernière synchro Action1",
        readonly=True,
        copy=False,
    )
    manual_override = fields.Boolean(
        string="Saisie manuelle prioritaire",
        default=False,
        tracking=True,
        help="Si activé, la synchronisation Action1 N'ÉCRIT PAS sur ce poste — "
        "seule la saisie manuelle fait foi. Utile quand un poste a des données "
        "manuelles plus fiables que celles d'Action1.",
    )
    group_ids = fields.Many2many(
        comodel_name="hosting.endpoint.group",
        relation="hosting_endpoint_group_rel",
        column1="endpoint_id",
        column2="group_id",
        string="Groupes",
    )
    license_seat_ids = fields.One2many(
        comodel_name="hosting.license.seat",
        inverse_name="endpoint_id",
        string="Licences activées",
    )
    license_seat_count = fields.Integer(
        string="Nombre de licences",
        compute="_compute_license_seat_count",
    )
    intune_device_id = fields.Char(
        string="Intune Device ID",
        help="ID Microsoft Intune (managedDevices), base pour synchro Graph future.",
    )
    entra_object_id = fields.Char(
        string="Entra Object ID",
        help="ID d'objet Entra ID / Azure AD pour cet appareil.",
    )

    # --- BitLocker (champs sensibles, restreints au manager) ---
    bitlocker_recovery_key = fields.Char(
        string="Clé de récupération BitLocker",
        groups="hosting_management.group_hosting_manager",
        help="Clé numérique de récupération BitLocker. Visible uniquement par les gestionnaires.",
    )
    bitlocker_volume_id = fields.Char(
        string="ID volume BitLocker",
        groups="hosting_management.group_hosting_manager",
        help="GUID du volume protégé — utile pour matcher avec Microsoft Graph.",
    )

    notes = fields.Html(string="Notes")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "unique_serial_per_partner",
            "EXCLUDE (partner_id WITH =, serial WITH =) WHERE (serial IS NOT NULL AND serial != '' AND active = true)",
            "Un numéro de série doit être unique par client.",
        ),
    ]

    @api.depends("license_seat_ids")
    def _compute_license_seat_count(self):
        for ep in self:
            ep.license_seat_count = len(ep.license_seat_ids)

    @api.depends(
        "assigned_type",
        "assigned_partner_id",
        "assigned_matrix_item_id",
        "assigned_user_name",
    )
    def _compute_assigned_display(self):
        for ep in self:
            if ep.assigned_type == "contact" and ep.assigned_partner_id:
                ep.assigned_display = ep.assigned_partner_id.display_name
            elif ep.assigned_type == "matrix_item" and ep.assigned_matrix_item_id:
                ep.assigned_display = ep.assigned_matrix_item_id.display_name
            else:
                ep.assigned_display = ep.assigned_user_name or ""

    @api.onchange("assigned_partner_id")
    def _onchange_assigned_partner_id(self):
        if self.assigned_partner_id:
            if not self.assigned_email:
                self.assigned_email = self.assigned_partner_id.email
            if not self.assigned_user_name:
                self.assigned_user_name = self.assigned_partner_id.name

    def action_view_licenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"Licences — {self.display_name}",
            "res_model": "hosting.license.seat",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("endpoint_id", "=", self.id)],
            "context": {
                "default_endpoint_id": self.id,
                "default_assignee_type": "endpoint",
                "default_partner_id": self.partner_id.id,
            },
        }

    @api.depends("os")
    def _compute_os_eol_tag(self):
        today = fields.Date.today()
        labels = {
            "windows_10": _("EOL Windows 10 (oct. 2025)"),
        }
        for ep in self:
            eol = OS_EOL_DATES.get(ep.os)
            if eol and today >= eol:
                ep.os_eol_tag = labels.get(ep.os) or _("OS en fin de support")
            else:
                ep.os_eol_tag = False

    # Champs sensibles dont les mutations sont auditées (présence uniquement,
    # JAMAIS la valeur). Voir _audit_sensitive_writes ci-dessous.
    _SENSITIVE_FIELDS = ("bitlocker_recovery_key", "bitlocker_volume_id")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code") or vals.get("code") == "New":
                vals["code"] = self.env["ir.sequence"].next_by_code(
                    "hosting.endpoint"
                ) or "EP-NEW"
        records = super().create(vals_list)
        for rec, vals in zip(records, vals_list):
            sensitive_set = [f for f in self._SENSITIVE_FIELDS if vals.get(f)]
            if sensitive_set:
                rec._audit_sensitive_writes(sensitive_set, action="set")
        return records

    def write(self, vals):
        sensitive_changed = [f for f in self._SENSITIVE_FIELDS if f in vals]
        result = super().write(vals)
        if sensitive_changed:
            for rec in self:
                # 'set' si nouvelle valeur non vide, 'cleared' sinon
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
        """Journaliser la mutation d'un champ sensible (présence, pas valeur)."""
        AuditLog = self.env.get("hosting.audit.log")
        if AuditLog is None:
            return
        for rec in self:
            AuditLog._log_event(
                action_type="config_change",
                category="security",
                description=(
                    f"Champ sensible modifié sur le poste {rec.display_name} : "
                    f"{', '.join(field_names)} ({action}). Valeur non journalisée."
                ),
                res_model="hosting.endpoint",
                res_id=rec.id,
                res_name=rec.display_name,
                field_name=",".join(field_names),
                severity="warning",
                status="success",
            )

    @api.depends("code", "name")
    def _compute_display_name(self):
        for ep in self:
            ep.display_name = f"[{ep.code}] {ep.name}" if ep.code and ep.code != "New" else (ep.name or "")

    # ------------------------------------------------------------------
    # Cron — alerte garantie expirante (quick win)
    # ------------------------------------------------------------------
    @api.model
    def _cron_warranty_alerts(self):
        """Envoyer un digest hebdomadaire au responsable hébergement de chaque
        partner ayant des endpoints dont la garantie expire dans <= 90 jours.

        Cron : tous les 7 jours (déclenché par hosting_cron.xml). Recalcule
        également ``os_eol_tag`` pour absorber les passages d'OS en fin de
        support depuis la dernière saisie.
        """
        today = fields.Date.today()
        horizon = today + timedelta(days=90)

        # Rafraîchir os_eol_tag pour tous les endpoints actifs dont l'OS figure
        # dans OS_EOL_DATES — couvre les postes saisis AVANT la date EOL.
        stale = self.search([("os", "in", list(OS_EOL_DATES.keys()))])
        if stale:
            stale.modified(["os"])  # recompute via depends("os")

        endpoints = self.search(
            [
                ("warranty_end", "!=", False),
                ("warranty_end", "<=", horizon),
                ("lifecycle_state", "in", ["deployed", "in_stock"]),
                ("partner_id.has_sla", "=", True),
            ],
            order="partner_id, warranty_end",
        )
        if not endpoints:
            return

        by_partner = {}
        for ep in endpoints:
            by_partner.setdefault(ep.partner_id, self.env["hosting.endpoint"])
            by_partner[ep.partner_id] |= ep

        Mail = self.env["mail.mail"].sudo()
        type_labels = dict(self._fields["endpoint_type"].selection)
        for partner, eps in by_partner.items():
            try:
                service = self.env["hosting.service"].sudo().search(
                    [("partner_id", "=", partner.id)], limit=1
                )
                recipient_user = service.user_id if service and service.user_id else None
                if not recipient_user or not recipient_user.email:
                    continue

                rows = []
                for ep in eps:
                    expired = ep.warranty_end < today
                    days_left = (ep.warranty_end - today).days
                    status_label = _("Expirée") if expired else f"{days_left} j"
                    rows.append(
                        "<tr>"
                        f"<td>{_esc(ep.code or '')}</td>"
                        f"<td>{_esc(ep.name or '')}</td>"
                        f"<td>{_esc(type_labels.get(ep.endpoint_type, ''))}</td>"
                        f"<td>{_esc(str(ep.warranty_end))}</td>"
                        f"<td style='color:{'#c00' if expired else '#c80'};'>"
                        f"{_esc(status_label)}"
                        "</td>"
                        "</tr>"
                    )
                body = (
                    f"<p>Bonjour {_esc(recipient_user.name)},</p>"
                    f"<p>Voici les postes du parc <strong>{_esc(partner.name)}</strong> "
                    "dont la garantie est expirée ou expire dans les 90 prochains jours :</p>"
                    "<table border='1' cellpadding='6' cellspacing='0' "
                    "style='border-collapse:collapse;font-family:sans-serif;font-size:13px;'>"
                    "<thead><tr style='background:#eee;'>"
                    "<th>Réf.</th><th>Nom</th><th>Type</th><th>Fin garantie</th><th>Statut</th>"
                    "</tr></thead>"
                    f"<tbody>{''.join(rows)}</tbody></table>"
                    "<p>— Module Hébergement</p>"
                )
                Mail.create(
                    {
                        "subject": f"[Parc] {partner.name} — {len(eps)} poste(s) garantie ≤ 90 j",
                        "body_html": body,
                        "email_to": recipient_user.email,
                        "author_id": self.env.user.partner_id.id,
                    }
                ).send()
            except Exception:
                _logger.exception(
                    "hosting.endpoint: échec digest garantie pour partner_id=%s",
                    partner.id,
                )
                continue

    # ------------------------------------------------------------------
    # Action1 sync
    # ------------------------------------------------------------------
    #
    # Champs que la synchro Action1 peut écrire (les autres sont pilotés
    # manuellement et jamais touchés par la sync).
    _ACTION1_MANAGED_FIELDS = (
        "name",
        "hostname",
        "os",
        "os_build",
        "ip_address",
        "mac_address",
        "manufacturer",
        "model_name",
        "serial",
        "action1_url",
    )

    # Mapping OS Action1 → selection Odoo. Couvre les chaînes vues couramment
    # dans la console ; tout ce qui n'est pas mappé tombe sur "other".
    _ACTION1_OS_MAP = {
        "windows 10": "windows_10",
        "windows 11": "windows_11",
        "windows server": "windows_server",
        "macos": "macos",
        "mac os": "macos",
        "ubuntu": "linux_ubuntu",
        "debian": "linux_debian",
    }

    @api.model
    def _action1_normalize_os(self, raw):
        if not raw:
            return False
        raw_l = raw.strip().lower()
        for needle, value in self._ACTION1_OS_MAP.items():
            if needle in raw_l:
                return value
        if "linux" in raw_l:
            return "linux_other"
        if "windows" in raw_l:
            return "windows_10"  # fallback
        return "other"

    @api.model
    def _action1_get_config(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return {
            "enabled": (ICP.get_param("hosting.action1_enabled", "0") or "").strip().lower()
            in ("1", "true", "yes", "on", "t"),
            "token": ICP.get_param("hosting.action1_api_token", ""),
            "base_url": ICP.get_param(
                "hosting.action1_api_base_url",
                "https://app.action1.com/api/3.0",
            ).rstrip("/"),
            "strategy": ICP.get_param(
                "hosting.action1_conflict_strategy", "action1_wins"
            ),
            "console_base": ICP.get_param(
                "hosting.action1_console_base",
                "https://app.action1.com",
            ).rstrip("/"),
        }

    @api.model
    def _action1_get(self, path, params=None):
        """GET helper avec auth Bearer + gestion d'erreur."""
        import requests

        cfg = self._action1_get_config()
        if not cfg["token"]:
            raise ValueError("Action1 API token absent (hosting.action1_api_token).")
        url = f"{cfg['base_url']}{path}"
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {cfg['token']}",
                "Accept": "application/json",
            },
            params=params or {},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    @api.model
    def _action1_fetch_endpoints(self):
        """Récupérer la liste des endpoints depuis Action1.

        Méthode dédiée pour faciliter le mock en test : surcharger ceci
        suffit à fournir une fixture sans toucher au HTTP.
        Retour attendu : liste de dicts (cf. Action1 REST API 3.0).
        """
        data = self._action1_get("/endpoints")
        return data.get("endpoints", data) if isinstance(data, dict) else data

    @api.model
    def _action1_fetch_groups(self):
        data = self._action1_get("/endpoint_groups")
        return data.get("endpoint_groups", data) if isinstance(data, dict) else data

    @api.model
    def _action1_sync(self, manual=False):
        """Synchroniser les endpoints + memberships depuis Action1.

        Stratégie de résolution de conflits, dans l'ordre :
        1. ``manual_override=True`` sur le poste → AUCUNE écriture.
        2. Stratégie globale ``hosting.action1_conflict_strategy`` :
           - ``action1_wins`` (défaut) : Action1 écrase tout champ géré.
           - ``odoo_wins`` : seuls les champs vides côté Odoo sont remplis.
           - ``manual_only`` : la sync crée de nouveaux postes mais ne touche
             pas les postes existants.
        Retourne un dict de stats ``{created, updated, skipped, errors}``.
        """
        cfg = self._action1_get_config()
        if not cfg["enabled"] and not manual:
            _logger.info("Action1 sync désactivée — skip cron.")
            return {"created": 0, "updated": 0, "skipped": 0, "errors": 0}
        if not cfg["token"]:
            _logger.warning("Action1 sync : token absent, abandon.")
            return {"created": 0, "updated": 0, "skipped": 0, "errors": 1}

        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}
        now = fields.Datetime.now()
        Group = self.env["hosting.endpoint.group"]

        # ---- 1. Sync groups -------------------------------------------------
        try:
            groups_data = self._action1_fetch_groups()
        except Exception as e:
            _logger.exception("Action1 fetch_groups a échoué : %s", e)
            stats["errors"] += 1
            return stats

        a1_groups_by_id = {}
        for g in groups_data or []:
            a1_id = str(g.get("id") or g.get("group_id") or "")
            if not a1_id:
                continue
            existing = Group.search([("action1_group_id", "=", a1_id)], limit=1)
            if existing:
                existing.action1_last_sync = now
                a1_groups_by_id[a1_id] = existing
            else:
                # Groupe Action1 inconnu : ne pas créer automatiquement
                # (on a besoin d'un partner_id, choix qui revient à l'admin).
                _logger.info(
                    "Action1 groupe inconnu (id=%s name=%s) — à mapper manuellement.",
                    a1_id, g.get("name"),
                )

        # ---- 2. Sync endpoints ---------------------------------------------
        try:
            endpoints_data = self._action1_fetch_endpoints()
        except Exception as e:
            _logger.exception("Action1 fetch_endpoints a échoué : %s", e)
            stats["errors"] += 1
            return stats

        for e in endpoints_data or []:
            try:
                a1_ep_id = str(e.get("id") or e.get("endpoint_id") or "")
                if not a1_ep_id:
                    stats["skipped"] += 1
                    continue

                # Déterminer les groupes membres et donc le partner
                member_a1_group_ids = [str(g) for g in (e.get("group_ids") or [])]
                if not member_a1_group_ids and e.get("groups"):
                    member_a1_group_ids = [
                        str(g.get("id"))
                        for g in e["groups"]
                        if isinstance(g, dict) and g.get("id")
                    ]
                resolved_groups = [
                    a1_groups_by_id[g] for g in member_a1_group_ids
                    if g in a1_groups_by_id
                ]
                if not resolved_groups:
                    _logger.info(
                        "Action1 endpoint %s sans groupe mappé — skip.", a1_ep_id
                    )
                    stats["skipped"] += 1
                    continue

                partners = {g.partner_id for g in resolved_groups if g.partner_id}
                if len(partners) > 1:
                    _logger.warning(
                        "Action1 endpoint %s membre de groupes de partenaires "
                        "différents (%s) — skip pour éviter ambigüité.",
                        a1_ep_id, [p.name for p in partners],
                    )
                    stats["skipped"] += 1
                    continue
                if not partners:
                    stats["skipped"] += 1
                    continue
                partner = next(iter(partners))

                existing = self.search(
                    [("action1_endpoint_id", "=", a1_ep_id)], limit=1
                )
                payload = self._action1_endpoint_payload(e, cfg, partner)

                if existing:
                    if existing.manual_override:
                        # Pas de write sur champs gérés, mais on rafraîchit
                        # action1_last_sync et la liste de groupes.
                        existing.write({
                            "action1_last_sync": now,
                            "group_ids": [(6, 0, [g.id for g in resolved_groups])],
                        })
                        stats["skipped"] += 1
                        continue

                    if cfg["strategy"] == "manual_only":
                        existing.write({
                            "action1_last_sync": now,
                            "group_ids": [(6, 0, [g.id for g in resolved_groups])],
                        })
                        stats["skipped"] += 1
                        continue

                    write_vals = {"action1_last_sync": now}
                    if cfg["strategy"] == "odoo_wins":
                        # Ne remplir que les champs vides
                        for f in self._ACTION1_MANAGED_FIELDS:
                            if f in payload and not existing[f]:
                                write_vals[f] = payload[f]
                    else:  # action1_wins (defaut)
                        for f in self._ACTION1_MANAGED_FIELDS:
                            if f in payload:
                                write_vals[f] = payload[f]
                    write_vals["group_ids"] = [(6, 0, [g.id for g in resolved_groups])]
                    existing.write(write_vals)
                    stats["updated"] += 1
                else:
                    create_vals = {
                        "partner_id": partner.id,
                        "action1_endpoint_id": a1_ep_id,
                        "action1_last_sync": now,
                        "group_ids": [(6, 0, [g.id for g in resolved_groups])],
                        **{k: v for k, v in payload.items() if v},
                    }
                    if not create_vals.get("name"):
                        create_vals["name"] = a1_ep_id
                    self.create(create_vals)
                    stats["created"] += 1
            except Exception:
                _logger.exception(
                    "Action1 sync : erreur sur endpoint %s", e.get("id"),
                )
                stats["errors"] += 1
                continue

        # Mémoriser le timestamp de la dernière sync globale
        self.env["ir.config_parameter"].sudo().set_param(
            "hosting.action1_last_sync", fields.Datetime.to_string(now),
        )
        _logger.info("Action1 sync terminée : %s", stats)
        return stats

    @api.model
    def _action1_endpoint_payload(self, raw, cfg, partner):
        """Construire le dict de valeurs pour un endpoint à partir du JSON
        Action1. Tolère les variations de schéma (clés snake_case / camelCase)."""
        get = lambda *keys: next(
            (raw[k] for k in keys if k in raw and raw[k] not in (None, "")), False
        )
        a1_id = str(raw.get("id") or raw.get("endpoint_id") or "")
        return {
            "name": get("hostname", "name", "computer_name") or a1_id,
            "hostname": get("hostname", "computer_name") or False,
            "os": self._action1_normalize_os(get("os", "operating_system", "os_name")),
            "os_build": get("os_build", "os_version") or False,
            "ip_address": get("ip_address", "ip", "lan_ip") or False,
            "mac_address": get("mac_address", "mac") or False,
            "manufacturer": get("manufacturer", "vendor") or False,
            "model_name": get("model", "model_name") or False,
            "serial": get("serial", "serial_number") or False,
            "action1_url": f"{cfg['console_base']}/endpoints/{a1_id}" if a1_id else False,
        }

    @api.model
    def _cron_action1_sync(self):
        """Entry point pour le cron — wrappe ``_action1_sync`` en mode auto."""
        self._action1_sync(manual=False)
