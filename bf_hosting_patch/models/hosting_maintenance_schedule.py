# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Une maintenance peut enfin viser une MACHINE, pas seulement un service.

`hosting.maintenance.schedule` exigeait un `service_id`. Conséquence mesurée le
2026-08-30 : le type `security_patch` existait depuis toujours et portait
**zéro planification sur 83**, parce qu'une mise à jour de poste n'avait
aucun endroit où exister. La tâche « mettre à jour les paquets du poste X » vivait
donc dans les to-dos personnels, pour une machine sur quatre.

⚠️ Rien n'est modifié dans `hosting_management` : tout passe par l'héritage, et
les 83 planifications existantes gardent exactement le comportement du module
de base. Les méthodes surchargées délèguent au parent pour les planifications
qui portent un service, et ne traitent elles-mêmes que le cas neuf.
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class HostingMaintenanceSchedule(models.Model):
    _inherit = "hosting.maintenance.schedule"

    # Le champ de base est `required=True`. C'est cette seule ligne qui
    # empêchait de céduler quoi que ce soit sur une machine.
    service_id = fields.Many2one(required=False)

    endpoint_id = fields.Many2one(
        comodel_name="hosting.endpoint",
        string="Poste du parc",
        ondelete="cascade",
        tracking=True,
        index=True,
        help="Viser une machine plutôt qu'un service. C'est ce qui rend le "
             "type « Correctifs de sécurité » utilisable.",
    )
    system_id = fields.Many2one(
        comodel_name="bf.patch.system",
        string="Système visé",
        ondelete="cascade",
        tracking=True,
        help="Optionnel : sur une machine en double amorçage, viser un seul "
             "côté. Laissé vide, la maintenance vise la machine entière.",
    )

    # ⚠️ Le champ de base est un `related` sur `service_id.partner_id`, stocké.
    # Sans cette surcharge, une planification visant une machine aurait un
    # client VIDE, ce qui emporterait le cloisonnement par client des règles
    # d'enregistrement : la planification deviendrait invisible pour les uns et
    # visible pour les autres, en silence.
    # ⚠️ Redéclarer le comodèle EXPLICITEMENT. Sans lui, la redéfinition perd
    # la relation et Odoo lève « Wrong value for ... : res.partner(65,) » à la
    # première écriture du calcul. `related=False` seul ne suffit pas.
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Client",
        related=False,
        compute="_compute_partner_id",
        store=True,
        readonly=True,
    )

    target_label = fields.Char(
        string="Cible", compute="_compute_target_label",
        help="Ce que la maintenance vise, quel que soit le porteur.",
    )

    @api.depends("service_id.partner_id", "endpoint_id.partner_id",
                 "system_id.endpoint_id.partner_id")
    def _compute_partner_id(self):
        for record in self:
            record.partner_id = (
                record.service_id.partner_id
                or record.endpoint_id.partner_id
                or record.system_id.endpoint_id.partner_id
            )

    @api.depends("service_id.name", "endpoint_id.name", "system_id.name")
    def _compute_target_label(self):
        for record in self:
            if record.service_id:
                record.target_label = record.service_id.name
            elif record.system_id:
                record.target_label = _(
                    "%(machine)s / %(systeme)s",
                    machine=record.system_id.endpoint_id.name,
                    systeme=record.system_id.name,
                )
            elif record.endpoint_id:
                record.target_label = record.endpoint_id.name
            else:
                record.target_label = False

    # 🔴 Pourquoi une contrainte SQL et PAS un `@api.constrains`.
    #
    # Odoo ne joue une contrainte Python que pour les champs PRÉSENTS dans les
    # valeurs écrites. Une création sans aucune cible ne mentionne ni
    # `service_id` ni `endpoint_id` : la contrainte n'est jamais appelée et la
    # ligne passe. Mesuré le 2026-08-31, elle était acceptée en silence.
    #
    # La contrainte SQL, elle, s'applique à chaque ligne quoi qu'il arrive, et
    # dès l'INSERT, donc AVANT que le vidage ne joue le Python. Un
    # `@api.constrains` posé en plus serait du code mort qui a l'air d'un
    # garde : il n'y en a pas. Odoo affiche le message ci-dessous tel quel.
    _sql_constraints = [
        (
            "cible_unique",
            "CHECK ((service_id IS NULL) <> (endpoint_id IS NULL))",
            "Une planification de maintenance vise un service OU un poste du "
            "parc, jamais les deux ni aucun des deux.",
        ),
        (
            "systeme_implique_poste",
            "CHECK (system_id IS NULL OR endpoint_id IS NOT NULL)",
            "Un système visé suppose la machine qui le porte.",
        ),
    ]

    @api.onchange("system_id")
    def _onchange_system_id(self):
        """Choisir un système renseigne sa machine : les deux doivent rester
        d'accord, sinon la fiche dit une chose et l'échéance une autre."""
        if self.system_id:
            self.endpoint_id = self.system_id.endpoint_id

    # ------------------------------------------------------------------
    # Les deux méthodes du parent qui lisaient `service_id` sans garde
    # ------------------------------------------------------------------
    def _create_maintenance_activity(self):
        """Le parent écrit « Service : {service_id.name} ». Sur une
        planification sans service, ça donnerait « Service : False »."""
        avec_service = self.filtered("service_id")
        if avec_service:
            super(HostingMaintenanceSchedule,
                  avec_service)._create_maintenance_activity()

        sans_service = self - avec_service
        if not sans_service:
            return

        activity_type = self.env.ref(
            "hosting_management.mail_activity_type_hosting_maintenance",
            raise_if_not_found=False,
        )
        if not activity_type:
            return

        for record in sans_service:
            if not record.next_due or not record.active:
                continue
            record.activity_ids.filtered(
                lambda a: a.activity_type_id == activity_type
            ).unlink()
            parts = [
                f"<strong>{record.name}</strong>",
                _("Poste : %s", record.target_label or _("non précisé")),
                _("Client : %s", record.partner_id.name or _("N/D")),
                _("Type : %s", record._get_type_display()),
                _("Fréquence : %s", record._get_frequency_display()),
            ]
            if record.instructions:
                instructions = record.instructions.replace(chr(10), "<br/>")
                parts.append(f"<br/><strong>Instructions :</strong><br/>{instructions}")
            record.activity_schedule(
                "hosting_management.mail_activity_type_hosting_maintenance",
                date_deadline=record.next_due,
                summary=record.name,
                note="<br/>".join(parts),
                user_id=record.user_id.id or self.env.user.id,
            )

    def action_mark_done(self):
        """Le parent journalise `service_id.name` et `service_id.server_id`."""
        avec_service = self.filtered("service_id")
        sans_service = self - avec_service
        if avec_service:
            super(HostingMaintenanceSchedule, avec_service).action_mark_done()
        if not sans_service:
            return True

        today = fields.Date.today()
        activity_type = self.env.ref(
            "hosting_management.mail_activity_type_hosting_maintenance",
            raise_if_not_found=False,
        )
        audit = self.env["hosting.audit.log"]
        for record in sans_service:
            audit._log_event(
                action_type="maintenance",
                category="ops",
                description=_(
                    "Maintenance complétée : %(nom)s (poste : %(cible)s)",
                    nom=record.name, cible=record.target_label or "?",
                ),
                res_model=self._name,
                res_id=record.id,
                res_name=record.display_name,
                server_id=(record.endpoint_id.server_id.id
                           if record.endpoint_id.server_id else None),
            )
            record.last_performed = today
            if activity_type:
                record.activity_ids.filtered(
                    lambda a: a.activity_type_id == activity_type
                ).action_feedback(feedback=_("Complété le %s", today))
            record.message_post(
                body=_("Tâche de maintenance complétée le %s", today),
                message_type="notification",
            )
        sans_service._create_maintenance_activity()
        return True
