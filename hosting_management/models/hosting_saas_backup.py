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
    ("succeeded", "Réussie"),
    ("with_errors", "Réussie avec erreurs"),
    ("canceled", "Annulée"),
    ("failed", "Échouée"),
    ("unknown", "Indéterminée"),
]


class HostingSaasBackupRun(models.Model):
    """Une exécution de sauvegarde infonuagique, pour une organisation, une nuit."""

    _name = "hosting.saas.backup.run"
    _description = "Exécution de sauvegarde infonuagique"
    _order = "run_date desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(
        string="Référence",
        readonly=True,
        copy=False,
        default="New",
    )
    provider = fields.Selection(
        selection=[("cubebackup", "CubeBackup")],
        string="Produit",
        required=True,
        default="cubebackup",
        index=True,
        help="Produit de sauvegarde tiers d'où vient l'état.",
    )
    organization_ref = fields.Char(
        string="Identifiant de l'organisation",
        required=True,
        index=True,
        help="Identifiant de l'organisation CHEZ LE PRODUIT (un UUID chez "
             "CubeBackup). C'est lui qui fait foi, pas le nom : les noms "
             "changent, les identifiants non.",
    )
    organization_name = fields.Char(
        string="Organisation",
        index=True,
    )
    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        index=True,
        ondelete="set null",
        help="Résolu à l'arrivée par « Identifiant de sauvegarde infonuagique » "
             "sur la fiche de service. Vide = l'organisation n'est fichée nulle "
             "part ; l'exécution est gardée quand même.",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Client",
        related="service_id.partner_id",
        store=True,
        index=True,
    )
    external_id = fields.Char(
        string="Identifiant de la tâche",
        required=True,
        help="Numéro de tâche chez le produit. Avec le produit et "
             "l'organisation, il rend le versement rejouable sans doublon.",
    )
    run_date = fields.Datetime(
        string="Départ",
        required=True,
        index=True,
    )
    end_date = fields.Datetime(string="Fin")
    duration_sec = fields.Float(
        string="Durée (s)",
        digits=(16, 1),
    )
    duration_display = fields.Char(
        string="Durée",
        compute="_compute_duration_display",
    )
    result = fields.Selection(
        selection=RESULT_SELECTION,
        string="Résultat",
        required=True,
        default="unknown",
        index=True,
        tracking=True,
    )
    result_raw = fields.Char(
        string="Résultat (mot d'origine)",
        help="Le verdict tel que le produit l'écrit. Gardé pour qu'un mot "
             "inconnu ne se perde pas derrière « Indéterminée ».",
    )
    apps_total = fields.Integer(string="Applications")
    apps_failed = fields.Integer(string="Applications en échec")
    failure_ids = fields.One2many(
        comodel_name="hosting.saas.backup.failure",
        inverse_name="run_id",
        string="Échecs",
    )
    ingest_source = fields.Selection(
        selection=[
            ("log", "Journal du conteneur"),
            ("api", "API de la console"),
            ("email", "Rapport par courriel"),
            ("manual", "Saisie manuelle"),
        ],
        string="Lu par",
        default="log",
        help="Par où l'état a été lu. Note de provenance : rien dans le module "
             "ne doit en dépendre.",
    )
    hostname = fields.Char(string="Hôte du lecteur")
    notes = fields.Text(string="Notes")

    _sql_constraints = [
        (
            "saas_run_unique",
            "unique(provider, organization_ref, external_id)",
            "Cette exécution est déjà versée pour cette organisation.",
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
    _description = "Échec de sauvegarde infonuagique"
    _order = "run_id desc, id"

    run_id = fields.Many2one(
        comodel_name="hosting.saas.backup.run",
        string="Exécution",
        required=True,
        ondelete="cascade",
        index=True,
    )
    app_type = fields.Char(
        string="Application",
        help="mail, drive, site, team, contacts, calendar.",
    )
    subject_name = fields.Char(
        string="Personne ou site",
        help="Le nom d'affichage tel que le produit le connaît.",
    )
    subject_login = fields.Char(
        string="Compte",
        help="L'adresse du compte, quand le produit la nomme. C'est elle qu'on "
             "tape dans la console d'administration du fournisseur.",
    )
    subject_ref = fields.Char(string="Identifiant")
    error_code = fields.Char(
        string="Code",
        help="Code d'erreur du fournisseur infonuagique (p. ex. "
             "ErrorQuotaExceeded, resourceLocked, throttledRequest).",
    )
    http_code = fields.Char(string="Code HTTP")
    error_message = fields.Char(
        string="Cause",
        help="Le message court du fournisseur. Jamais le contenu de l'objet "
             "sauvegardé.",
    )
    organization_name = fields.Char(
        related="run_id.organization_name",
        string="Organisation",
        store=True,
    )
    run_date = fields.Datetime(
        related="run_id.run_date",
        string="Départ",
        store=True,
        index=True,
    )
