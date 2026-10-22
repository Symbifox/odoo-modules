# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import email_normalize

from .hosting_maintenance_window import client_de


class ResPartner(models.Model):
    _inherit = "res.partner"

    has_sla = fields.Boolean(
        string="SLA client",
        tracking=True,
        help="Enable for clients with a service level agreement. Unlocks "
             "the IT fleet section and related features.",
    )
    hosting_service_ids = fields.One2many(
        comodel_name="hosting.service",
        inverse_name="partner_id",
        string="Hosting services",
    )
    hosting_service_count = fields.Integer(
        string="Hosting service count",
        compute="_compute_hosting_service_count",
    )
    hosting_endpoint_ids = fields.One2many(
        comodel_name="hosting.endpoint",
        inverse_name="partner_id",
        string="Fleet endpoints",
    )
    hosting_endpoint_count = fields.Integer(
        string="Endpoint count",
        compute="_compute_hosting_endpoint_count",
    )
    hosting_license_seat_ids = fields.One2many(
        comodel_name="hosting.license.seat",
        inverse_name="partner_id",
        string="Assigned license seats",
    )
    hosting_license_count = fields.Integer(
        string="License count",
        compute="_compute_hosting_license_count",
    )
    voip_did_ids = fields.One2many(
        comodel_name="hosting.voip.did",
        inverse_name="partner_id",
        string="Phone DIDs",
    )
    voip_did_count = fields.Integer(
        string="DID count",
        compute="_compute_voip_stats",
    )
    voip_monthly_cost = fields.Float(
        string="Monthly DID cost",
        compute="_compute_voip_stats",
    )
    voip_usage_cost_month = fields.Float(
        string="Call usage cost (month)",
        compute="_compute_voip_stats",
    )
    hosting_services_expiring_count = fields.Integer(
        string="Expiring services",
        compute="_compute_hosting_alerts",
    )
    hosting_services_updates_count = fields.Integer(
        string="Available updates",
        compute="_compute_hosting_alerts",
    )
    hosting_services_storage_alert_count = fields.Integer(
        string="Storage alerts",
        compute="_compute_hosting_alerts",
    )
    # Les personnes qui reçoivent les avis de maintenance de ce client et qui, avec un
    # compte portail, voient sa page d'état. Une liste tenue à la main : ni « tous les
    # contacts » (un employé parti garde l'accès), ni le seul contact principal (un client
    # n'a pas toujours de courriel sur la fiche de la société).
    # 🔴 `groups=` sur le champ ET sur le nœud de vue : un interne sans le groupe doit
    # toujours ouvrir la fiche contact.
    # 🔴 `copy=False` : un Many2many se copie par défaut, et `copy_data` lit le champ. Copiable,
    # il interdisait à un interne sans le groupe de dupliquer un contact ou un usager
    # (`res.users.copy()` passe par `_inherits`) : AccessError. Et la copie d'une société
    # reprenait une liste de contacts qui ne sont pas les siens : ValidationError.
    hosting_recipient_ids = fields.Many2many(
        comodel_name="res.partner",
        relation="res_partner_hosting_recipient_rel",
        column1="client_id",
        column2="recipient_id",
        string="Hosting recipients",
        groups="hosting_management.group_hosting_user",
        copy=False,
        help="The people who receive this client's maintenance notices and, with a portal "
             "account, see its status page: the company itself or its own contacts.",
    )

    @api.depends("hosting_service_ids")
    def _compute_hosting_service_count(self):
        for partner in self:
            partner.hosting_service_count = len(partner.hosting_service_ids)

    @api.depends("hosting_endpoint_ids")
    def _compute_hosting_endpoint_count(self):
        for partner in self:
            partner.hosting_endpoint_count = len(partner.hosting_endpoint_ids)

    def action_view_hosting_endpoints(self):
        """Ouvrir les postes du parc pour ce partenaire."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("IT fleet - %s", self.name),
            "res_model": "hosting.endpoint",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    @api.depends("hosting_license_seat_ids")
    def _compute_hosting_license_count(self):
        for partner in self:
            partner.hosting_license_count = len(partner.hosting_license_seat_ids)

    def action_view_hosting_licenses(self):
        """Ouvrir les sièges de licence affectés à ce partenaire."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Licenses - %s", self.name),
            "res_model": "hosting.license.seat",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id, "default_assignee_type": "partner"},
        }

    @api.depends("voip_did_ids", "voip_did_ids.monthly_cost", "voip_did_ids.state")
    def _compute_voip_stats(self):
        Cdr = self.env["hosting.voip.cdr"]
        month_start = fields.Date.context_today(self).replace(day=1)
        for partner in self:
            active_dids = partner.voip_did_ids.filtered(
                lambda d: d.state == "active")
            partner.voip_did_count = len(partner.voip_did_ids)
            partner.voip_monthly_cost = sum(active_dids.mapped("monthly_cost"))
            if partner.id:
                groups = Cdr.read_group(
                    [("partner_id", "=", partner.id),
                     ("call_date", ">=", str(month_start))],
                    ["cost:sum"], [])
                partner.voip_usage_cost_month = (
                    (groups[0].get("cost") if groups else 0.0) or 0.0)
            else:
                partner.voip_usage_cost_month = 0.0

    def action_view_voip_dids(self):
        """Ouvrir les DID téléphoniques pour ce partenaire."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("DID - %s", self.name),
            "res_model": "hosting.voip.did",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    @api.depends(
        "hosting_service_ids",
        "hosting_service_ids.state",
        "hosting_service_ids.date_expiration",
        "hosting_service_ids.update_available",
        "hosting_service_ids.storage_alert",
    )
    def _compute_hosting_alerts(self):
        today = fields.Date.today()
        warning_date = today + timedelta(days=90)
        for partner in self:
            services = partner.hosting_service_ids.filtered(
                lambda s: s.state == "active"
            )
            partner.hosting_services_expiring_count = len(
                services.filtered(
                    lambda s: s.date_expiration
                    and today < s.date_expiration <= warning_date
                )
            )
            partner.hosting_services_updates_count = len(
                services.filtered(lambda s: s.update_available)
            )
            partner.hosting_services_storage_alert_count = len(
                services.filtered(lambda s: s.storage_alert)
            )

    def action_view_hosting_services(self):
        """Ouvrir les services d'hébergement pour ce partenaire."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Hosting services - %s", self.name),
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def action_view_hosting_services_expiring(self):
        """Ouvrir les services d'hébergement expirant pour ce partenaire."""
        self.ensure_one()
        today = fields.Date.today()
        warning_date = today + timedelta(days=90)
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Expiring services - %s", self.name),
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("partner_id", "=", self.id),
                ("state", "=", "active"),
                ("date_expiration", ">", today),
                ("date_expiration", "<=", warning_date),
            ],
            "context": {"default_partner_id": self.id},
        }

    def action_view_hosting_services_updates(self):
        """Ouvrir les services d'hébergement avec mises à jour disponibles."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Available updates - %s", self.name),
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("partner_id", "=", self.id),
                ("state", "=", "active"),
                ("update_available", "=", True),
            ],
            "context": {"default_partner_id": self.id},
        }

    def action_view_hosting_services_storage_alert(self):
        """Ouvrir les services d'hébergement avec alertes de stockage."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Storage alerts - %s", self.name),
            "res_model": "hosting.service",
            "views": [[False, "list"], [False, "form"], [False, "kanban"]],
            "domain": [
                ("partner_id", "=", self.id),
                ("state", "=", "active"),
                ("storage_alert", "=", True),
            ],
            "context": {"default_partner_id": self.id},
        }

    # ------------------------------------------------------------------
    # Destinataires hébergement
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, liste_valeurs):
        """🔴 Odoo 18 ne contrôle PAS le `groups=` d'un champ à la création : un gestionnaire
        de contacts sans le groupe hébergement créait une société avec `child_ids` (le
        contact existant d'un autre client, rattaché du coup) et `hosting_recipient_ids` sur
        ce même contact. La contrainte passait (le contact appartient bien à la société
        neuve) et, avec un compte portail, il obtenait la page d'état. Contrôlé APRÈS
        super(), qui a appliqué les défauts (clé `default_` du contexte comprise) : tous les
        chemins passent ici. Même garde que `bf_hosting_status` pour la publication."""
        partenaires = super().create(liste_valeurs)
        if not self.env.su and not self.env.user.has_group("hosting_management.group_hosting_user"):
            if any(p.hosting_recipient_ids for p in partenaires.sudo()):
                raise AccessError(_(
                    "Only a hosting user can set a client's hosting recipients."))
        return partenaires

    def write(self, vals):
        """🔴 Sans le groupe hébergement, le courriel d'un destinataire NOMMÉ ne change pas.

        Le changer suffisait à prendre sa place : ses avis, avec le lien de la page d'état,
        partent à la nouvelle adresse, et le portail s'ouvre à elle (invitation, ou
        réinitialisation du mot de passe d'un compte existant : le courriel d'un usager est
        celui de sa fiche). `portal.wizard` écrit ce courriel sans sudo : il passe ici.
        """
        if "email" in vals and not self.env.su \
                and not self.env.user.has_group("hosting_management.group_hosting_user"):
            nouveau = email_normalize(vals.get("email") or "") or (vals.get("email") or "")
            changes = self.sudo().filtered(
                lambda p: (email_normalize(p.email or "") or (p.email or "")) != nouveau)
            if changes and self.sudo().with_context(active_test=False).search_count(
                    [("hosting_recipient_ids", "in", changes.ids)]):
                raise AccessError(_(
                    "This contact receives a client's maintenance notices and status page. "
                    "Only a hosting user can change their email."))
        return super().write(vals)

    @api.constrains("hosting_recipient_ids")
    def _check_hosting_recipients(self):
        """Un destinataire est la société elle-même ou l'un de SES contacts, par la règle
        d'Odoo (`client_de`), jamais le contact d'un autre client.

        Elle ne garde pas le droit : à la création, Odoo 18 ne contrôle pas le `groups=` du
        champ, et une société neuve peut rattacher un contact existant (`child_ids`) puis le
        nommer ; c'est la garde de `create` qui refuse alors la liste à qui n'a pas le groupe.
        L'écriture, elle, contrôle le groupe.

        Déclenchée sur la seule liste, PAS sur `parent_id` ni `is_company` : sinon un
        particulier destinataire de lui-même ne pouvait plus être rattaché à une société par
        un usager qui ne voit pas le champ, et une ligne périmée bloquait toute écriture du
        parent du client. Une ligne devenue fausse reste en base sans effet : l'usage
        (`_hosting_destinataires`, et la page d'état de `bf_hosting_status`) revérifie
        `client_de` à chaque fois."""
        for client in self.sudo():
            if not client.hosting_recipient_ids:
                continue
            if client_de(client) != client:
                raise ValidationError(_(
                    "Hosting recipients belong to the client company (%(company)s), not to "
                    "one of its contacts.", company=client_de(client).display_name))
            etrangers = client.hosting_recipient_ids.filtered(lambda p: client_de(p) != client)
            if etrangers:
                raise ValidationError(_(
                    "A hosting recipient must be the company itself or one of its contacts: "
                    "%(names)s.", names=", ".join(etrangers.mapped("display_name"))))

    def _hosting_destinataires(self):
        """Les personnes à qui écrire pour ce client : la liste tenue, sinon l'ancienne règle
        (le courriel de la société, sinon son premier contact qui en a un). Seulement des
        fiches actives qui ont un courriel. Rend des res.partner, possiblement vides : ne
        jamais inventer une adresse.

        Le repli passe par le même filtre `client_de(p) == client` que la liste : il exclut
        une société enfant (`is_company`, enfant du client), qui est son propre client et
        dont le contexte d'avis remonterait à elle-même, pas au client."""
        self.ensure_one()
        client = client_de(self).sudo()
        liste = client.hosting_recipient_ids.filtered(
            lambda p: p.active and p.email and client_de(p) == client)
        if liste:
            return liste
        if client.email:
            return client
        return client.child_ids.filtered(
            lambda p: p.active and p.email and client_de(p) == client)[:1]
