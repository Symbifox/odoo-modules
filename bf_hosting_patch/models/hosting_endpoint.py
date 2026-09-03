# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Extension de `hosting.endpoint` : la MACHINE, et l'état de ses systèmes.

Le parc Blue Fox est presque tout en double amorçage. Une fiche de parc décrit
donc un actif physique (série, garantie, achat, clé BitLocker, sièges de
licence) et porte une ligne `bf.patch.system` par système installé dessus.

⚠️ Ce partage n'est pas cosmétique. Le `/etc/machine-id` identifie
l'INSTALLATION : le poser sur la fiche de parc aurait fabriqué deux fiches par
portable, donc exactement le doublon `vir` / `VIR` qu'on venait de fusionner.

Règle qui commande tout le reste :

    Un relevé absent est une alerte, jamais un silence.
"""

import logging
import secrets

from markupsafe import escape as _esc

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .bf_patch_system import PATCH_STATES, STATE_SEVERITY

_logger = logging.getLogger(__name__)

ENROL_CODE_MINUTES = 30


class HostingEndpoint(models.Model):
    _inherit = "hosting.endpoint"

    # ------------------------------------------------------------------
    # Identité de la MACHINE, pas d'un de ses systèmes
    # ------------------------------------------------------------------
    # Lu en root une seule fois, à l'enrôlement : les identifiants DMI
    # (`/sys/class/dmi/id/product_uuid`) ne sont pas lisibles autrement, et
    # l'agent quotidien tourne sans privilège.
    machine_uuid = fields.Char(
        string="UUID matériel", index=True, copy=False,
        help="UUID DMI de la carte mère, relevé en root à l'enrôlement. "
             "Identifie la machine physique, quel que soit le système démarré.",
    )
    system_ids = fields.One2many(
        comodel_name="bf.patch.system", inverse_name="endpoint_id",
        string="Systèmes installés",
    )
    system_count = fields.Integer(
        string="Systèmes", compute="_compute_system_count"
    )
    server_id = fields.Many2one(
        comodel_name="hosting.server", string="Serveur d'hébergement",
        ondelete="set null",
        help="Quand ce poste EST un serveur du parc, le rattacher ici donne le "
             "rayon de souffle d'un redémarrage.",
    )
    hosted_service_count = fields.Integer(
        string="Services portés", compute="_compute_hosted_service_count",
        help="Nombre de services d'hébergement qui tombent si la machine "
             "redémarre.",
    )

    # ------------------------------------------------------------------
    # Enrôlement : un humain choisit la MACHINE, l'agent déclare son système
    # ------------------------------------------------------------------
    agent_enrol_code = fields.Char(
        string="Code d'enrôlement", copy=False,
        groups="hosting_management.group_hosting_manager",
    )
    agent_enrol_expiry = fields.Datetime(
        string="Code valide jusqu'à", copy=False,
        groups="hosting_management.group_hosting_manager",
    )

    patch_state = fields.Selection(
        selection=PATCH_STATES, string="État des mises à jour",
        compute="_compute_patch_state", store=True, index=True,
        help="Le pire état parmi les systèmes installés sur la machine.",
    )
    patch_managed = fields.Boolean(
        string="Suivi des mises à jour", compute="_compute_patch_state",
        store=True,
    )
    agent_last_report = fields.Datetime(
        string="Dernier relevé", compute="_compute_patch_state", store=True,
        help="Le plus récent parmi les systèmes de la machine.",
    )

    _sql_constraints = [
        ("machine_uuid_uniq", "unique(machine_uuid)",
         "Cet UUID matériel est déjà porté par une autre fiche du parc : "
         "c'est la même machine, vue deux fois."),
    ]

    # ------------------------------------------------------------------
    def _compute_system_count(self):
        for endpoint in self:
            endpoint.system_count = len(endpoint.system_ids)

    def _compute_hosted_service_count(self):
        for endpoint in self:
            endpoint.hosted_service_count = endpoint.server_id.service_count or 0

    @api.depends("system_ids.patch_state", "system_ids.patch_managed",
                 "system_ids.agent_last_report")
    def _compute_patch_state(self):
        """Le pire de ses systèmes.

        Une machine dont le côté Linux est à jour et le côté Windows muet
        n'est pas « à jour » : elle est muette. C'est le seul agrégat qui ne
        cache rien.
        """
        for endpoint in self:
            managed = endpoint.system_ids.filtered("patch_managed")
            endpoint.patch_managed = bool(managed)
            if not managed:
                endpoint.patch_state = "unmanaged"
                endpoint.agent_last_report = False
                continue
            endpoint.patch_state = min(
                managed.mapped("patch_state"),
                key=lambda state: STATE_SEVERITY.index(state),
            )
            reports = [r for r in managed.mapped("agent_last_report") if r]
            endpoint.agent_last_report = max(reports) if reports else False

    # ------------------------------------------------------------------
    def _check_patch_manager(self):
        """⚠️ `has_group` rend Faux pour le superutilisateur, qui n'appartient
        à aucun groupe. Un garde qui ne teste que le groupe bloquerait les
        crons, les migrations et tout `sudo()`."""
        if not self.env.su and not self.env.user.has_group(
            "hosting_management.group_hosting_manager"
        ):
            raise AccessError(
                _("Seul un gestionnaire d'hébergement peut poser ou révoquer "
                  "un agent de mise à jour.")
            )

    def action_generate_enrol_code(self):
        """Poser un code à usage unique pour UN système de cette machine.

        Un portable en double amorçage demande donc deux enrôlements, un par
        côté. C'est un clic de plus, et ça garde le code à usage unique.
        """
        self.ensure_one()
        self._check_patch_manager()
        code = secrets.token_urlsafe(9)
        self.sudo().write({
            "agent_enrol_code": code,
            "agent_enrol_expiry": fields.Datetime.add(
                fields.Datetime.now(), minutes=ENROL_CODE_MINUTES
            ),
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Code d'enrôlement"),
                "message": _(
                    "%(code)s — valide %(minutes)s minutes, pour UN système. "
                    "Pour l'autre côté d'un double amorçage, en générer un "
                    "second.", code=code, minutes=ENROL_CODE_MINUTES,
                ),
                "type": "success",
                "sticky": True,
            },
        }

    @api.model
    def _enrol_agent(self, code, machine_id, hostname=None, os_family="linux",
                     machine_uuid=None, os_release=None):
        """Échanger un code à usage unique contre un jeton, pour un système.

        Rend `(system, token)`. L'agent ne peut JAMAIS créer une fiche de parc :
        il ne fait que déclarer un système sur celle qu'un humain a créée.
        """
        if not code or not machine_id:
            raise UserError(_("Code d'enrôlement ou machine-id manquant."))
        now = fields.Datetime.now()
        endpoint = self.sudo().search(
            [("agent_enrol_code", "=", code), ("agent_enrol_expiry", ">=", now)],
            limit=1,
        )
        if not endpoint:
            raise UserError(_("Code d'enrôlement inconnu ou expiré."))

        system_model = self.env["bf.patch.system"].sudo()
        system = system_model.search([("machine_id", "=", machine_id)], limit=1)
        if system and system.endpoint_id != endpoint:
            raise UserError(
                _("Ce système est déjà enregistré sur « %(name)s » (%(code)s). "
                  "L'enrôler ici créerait un doublon.",
                  name=system.endpoint_id.name, code=system.endpoint_id.code)
            )

        token = system_model._new_token()
        values = {
            "endpoint_id": endpoint.id,
            "machine_id": machine_id,
            "hostname": hostname or False,
            "os_family": os_family,
            "os_release": os_release or False,
            "agent_token_hash": system_model._hash_token(token),
            "patch_managed": True,
        }
        if system:
            # Ré-enrôlement du même système : on fait tourner le jeton plutôt
            # que d'empiler une deuxième fiche.
            system.write(values)
        else:
            values["name"] = hostname or os_release or machine_id[:12]
            system = system_model.create(values)

        endpoint_values = {"agent_enrol_code": False, "agent_enrol_expiry": False}
        # L'UUID matériel n'est lisible qu'en root : l'agent ne l'envoie qu'à
        # l'enrôlement, et on ne l'écrase jamais en silence.
        if machine_uuid and not endpoint.machine_uuid:
            endpoint_values["machine_uuid"] = machine_uuid
        endpoint.write(endpoint_values)

        endpoint.message_post(body=_(
            "Système « %(name)s » enrôlé (machine-id %(mid)s, %(fam)s).",
            name=_esc(system.name), mid=_esc(machine_id), fam=os_family,
        ))
        return system, token

    # ------------------------------------------------------------------
    def action_view_patch_reports(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Relevés de %s", self.name),
            "res_model": "bf.patch.report",
            "view_mode": "list,form",
            "domain": [("endpoint_id", "=", self.id)],
        }

    def action_view_systems(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Systèmes de %s", self.name),
            "res_model": "bf.patch.system",
            "view_mode": "list,form",
            "domain": [("endpoint_id", "=", self.id)],
            "context": {"default_endpoint_id": self.id},
        }
