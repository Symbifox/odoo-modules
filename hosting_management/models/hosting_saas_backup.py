# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Sauvegardes infonuagiques : l'état d'un produit de sauvegarde tiers.

Pourquoi un modèle à part plutôt qu'un troisième `report_type` sur
`hosting.backup.run` : trois lecteurs prennent « la dernière exécution » de
cette table SANS filtrer le type (la vérification mensuelle, le bouton de
rapport de test, et le cron du rapport quotidien). Un type de plus leur ferait
noter et poster la mauvaise nuit. Le coût n'est pas les trois correctifs, c'est
que le prochain lecteur répéterait le piège : « le dernier run » veut dire « la
nuit restic » dans toutes les têtes.

La nature de la donnée le justifie de toute façon : une exécution est ici
**par organisation cliente**, pas par hôte, et son détail est **par personne ou
par site**, pas par service.

⚠️ Ce modèle ne sait jamais COMMENT l'état a été lu. Il reçoit une charge
normalisée sur `/api/hosting/backup/report(/public)`. Le lecteur d'aujourd'hui
lit le journal du conteneur sur l'hôte ; un lecteur d'une instance hébergée
ailleurs passerait par l'API de sa console et émettrait la même charge. Le champ
`ingest_source` note la provenance pour qu'on puisse en juger, pas pour qu'on en
dépende.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


RESULT_SELECTION = [
    ("succeeded", "Successful"),
    ("with_errors", "Successful with errors"),
    ("canceled", "Canceled"),
    ("failed", "Failed"),
    ("unknown", "Undetermined"),
]


class HostingSaasBackupRun(models.Model):
    """Une exécution de sauvegarde infonuagique, pour une organisation, une nuit."""

    _name = "hosting.saas.backup.run"
    _description = "Cloud backup run"
    _order = "run_date desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(
        string="Reference",
        readonly=True,
        copy=False,
        default="New",
    )
    provider = fields.Selection(
        selection=[("cubebackup", "CubeBackup")],
        string="Product",
        required=True,
        default="cubebackup",
        index=True,
        help="Third-party backup product the state comes from.",
    )
    organization_ref = fields.Char(
        string="Organization identifier",
        required=True,
        index=True,
        help="Organization identifier IN THE PRODUCT (a UUID in "
             "CubeBackup). It is the reference, not the name: names "
             "change, identifiers do not.",
    )
    organization_name = fields.Char(
        string="Organization",
        index=True,
    )
    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        index=True,
        ondelete="set null",
        help="Resolved on arrival through \"Cloud backup identifier\" on "
             "the service record. Empty = the organization is not "
             "recorded anywhere; the run is kept anyway.",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Client",
        related="service_id.partner_id",
        store=True,
        index=True,
    )
    external_id = fields.Char(
        string="Task identifier",
        required=True,
        help="Task number in the product. With the product and the "
             "organization, it makes filing replayable without duplicates.",
    )
    run_date = fields.Datetime(
        string="Started",
        required=True,
        index=True,
    )
    end_date = fields.Datetime(string="End")
    duration_sec = fields.Float(
        string="Duration (s)",
        digits=(16, 1),
    )
    duration_display = fields.Char(
        string="Duration",
        compute="_compute_duration_display",
    )
    result = fields.Selection(
        selection=RESULT_SELECTION,
        string="Result",
        required=True,
        default="unknown",
        index=True,
        tracking=True,
    )
    result_raw = fields.Char(
        string="Result (original word)",
        help="The verdict as the product writes it. Kept so that an "
             "unknown word is not lost behind \"Undetermined\".",
    )
    apps_total = fields.Integer(string="Applications")
    apps_failed = fields.Integer(string="Failed applications")
    failure_ids = fields.One2many(
        comodel_name="hosting.saas.backup.failure",
        inverse_name="run_id",
        string="Failures",
    )
    ingest_source = fields.Selection(
        selection=[
            ("log", "Container log"),
            ("api", "Console API"),
            ("email", "Email report"),
            ("manual", "Manual entry"),
        ],
        string="Read via",
        default="log",
        help="Where the state was read from. Provenance note: nothing in "
             "the module should depend on it.",
    )
    hostname = fields.Char(string="Reader host")
    notes = fields.Text(string="Notes")

    _sql_constraints = [
        (
            "saas_run_unique",
            "unique(provider, organization_ref, external_id)",
            "This run is already filed for this organization.",
        ),
    ]

    @api.depends("duration_sec")
    def _compute_duration_display(self):
        for run in self:
            secs = int(run.duration_sec or 0)
            if secs >= 3600:
                run.duration_display = f"{secs // 3600} h {(secs % 3600) // 60} min"
            elif secs >= 60:
                run.duration_display = f"{secs // 60} min {secs % 60} s"
            else:
                run.duration_display = f"{secs} s"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "hosting.saas.backup.run"
                ) or "New"
        return super().create(vals_list)

    def _sync_service_state(self):
        """Reporter sur la fiche de service la dernière exécution connue.

        ⚠️ On ne prend pas « la dernière versée », on prend « la plus récente
        par date de départ » : un rattrapage qui rejoue trois mois d'archives
        arrive dans le désordre, et écrire au fil de l'eau ferait reculer l'état
        du service au dernier enregistrement créé.
        """
        services = self.mapped("service_id")
        for service in services:
            latest = self.search(
                [("service_id", "=", service.id)],
                order="run_date desc, id desc",
                limit=1,
            )
            service.write({
                "saas_backup_last_date": latest.run_date or False,
                "saas_backup_last_result": latest.result or False,
            })
        return services


class HostingSaasBackupFailure(models.Model):
    """Une application en échec dans une exécution : une boîte, un site, une équipe.

    ⚠️ Ce modèle ne porte JAMAIS de contenu : ni objet de courriel, ni nom de
    fichier. Le journal du produit en contient (les lignes d'export portent le
    JSON complet d'une demande), donc le lecteur extrait des champs et ne
    recopie jamais une ligne brute.
    """

    _name = "hosting.saas.backup.failure"
    _description = "Cloud backup failure"
    _order = "run_id desc, id"

    run_id = fields.Many2one(
        comodel_name="hosting.saas.backup.run",
        string="Run",
        required=True,
        ondelete="cascade",
        index=True,
    )
    app_type = fields.Char(
        string="Application",
        help="mail, drive, site, team, contacts, calendar.",
    )
    subject_name = fields.Char(
        string="Person or site",
        help="The display name as the product knows it.",
    )
    subject_login = fields.Char(
        string="Account",
        help="The account address, when the product names it. This is "
             "what you type in the provider's admin console.",
    )
    subject_ref = fields.Char(string="Identifier")
    error_code = fields.Char(
        string="Code",
        help="Error code from the cloud provider (e.g. "
             "ErrorQuotaExceeded, resourceLocked, throttledRequest).",
    )
    http_code = fields.Char(string="HTTP code")
    error_message = fields.Char(
        string="Cause",
        help="The provider's short message. Never the content of the "
             "backed-up item.",
    )
    organization_name = fields.Char(
        related="run_id.organization_name",
        string="Organization",
        store=True,
    )
    run_date = fields.Datetime(
        related="run_id.run_date",
        string="Started",
        store=True,
        index=True,
    )
