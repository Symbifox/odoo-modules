# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Relevés périodiques d'un produit de sauvegarde infonuagique.

Le journal du produit dit ce qui a tourné cette nuit. Il ne dit PAS deux choses
que seul le rapport périodique du fournisseur porte : l'état de la **licence**
et celui du **stockage**. Chez CubeBackup, elles sont chiffrées sur le disque
(`cbaesv1`) et derrière une session dans la console : le sommaire hebdomadaire
est la seule source lisible.

Un relevé est donc un constat daté, pas une exécution : une ligne par période et
par produit, avec ce que le fournisseur affirme lui-même sur chaque
organisation. Il sert à trois choses :

1. Voir venir la saturation d'une licence. Sur une base réelle, elle s'est
   remplie jusqu'au dernier siège, et personne ne l'a vue passer.
2. Suivre le stockage, que rien d'autre ne mesure.
3. **Recouper** : le fournisseur compte ses erreurs de son côté, on compte les
   nôtres depuis le journal. Deux sources qui divergent, ça se remarque.

⚠️ Le module ne lit aucun courriel. Il reçoit une charge normalisée, comme pour
les exécutions. Un lecteur peut passer par `bf.email`, par IMAP ou par l'API
de la console, sans rien changer ici.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class HostingSaasBackupReading(models.Model):
    _name = "hosting.saas.backup.reading"
    _description = "Relevé de sauvegarde infonuagique"
    _order = "period_end desc, id desc"
    _rec_name = "display_name"

    provider = fields.Selection(
        selection=[("cubebackup", "CubeBackup")],
        string="Produit",
        required=True,
        default="cubebackup",
        index=True,
    )
    period_start = fields.Date(string="Début de la période")
    period_end = fields.Date(string="Fin de la période", required=True, index=True)
    display_name = fields.Char(compute="_compute_display_name", store=True)

    # ── Licence ──────────────────────────────────────────────────────────
    seats_total = fields.Integer(string="Comptes achetés")
    seats_used = fields.Integer(string="Comptes utilisés")
    seats_free = fields.Integer(
        string="Comptes libres",
        compute="_compute_seats_free",
        store=True,
    )
    saturated = fields.Boolean(
        string="Licence saturée",
        compute="_compute_seats_free",
        store=True,
    )
    license_expiry = fields.Date(string="Expiration de la licence")

    # ── Stockage ─────────────────────────────────────────────────────────
    storage_kind = fields.Char(string="Stockage")
    storage_location = fields.Char(string="Emplacement")
    storage_available = fields.Char(string="Espace disponible")
    index_path = fields.Char(string="Index")
    index_free_display = fields.Char(string="Espace de l'index")
    index_free_pct = fields.Float(string="Index libre (%)", digits=(5, 1))

    # ── Provenance ───────────────────────────────────────────────────────
    ingest_source = fields.Selection(
        selection=[
            ("email", "Rapport par courriel"),
            ("api", "API de la console"),
            ("manual", "Saisie manuelle"),
        ],
        string="Lu par",
        default="email",
    )
    source_ref = fields.Char(
        string="Référence de la source",
        help="De quoi le relevé a été tiré, du point de vue du lecteur "
             "(par exemple l'identifiant du courriel). Le module ne sait pas "
             "ce que cette référence désigne et ne la suit jamais.",
    )
    line_ids = fields.One2many(
        comodel_name="hosting.saas.backup.reading.line",
        inverse_name="reading_id",
        string="Par organisation",
    )
    error_count = fields.Integer(
        string="Erreurs de la période",
        compute="_compute_error_count",
        store=True,
    )

    _sql_constraints = [
        (
            "saas_reading_unique",
            "unique(provider, period_end)",
            "Un relevé existe déjà pour ce produit et cette période.",
        ),
    ]

    @api.depends("period_start", "period_end", "provider")
    def _compute_display_name(self):
        for rec in self:
            label = dict(self._fields["provider"].selection).get(rec.provider, "")
            if rec.period_start and rec.period_end:
                rec.display_name = f"{label} {rec.period_start} au {rec.period_end}"
            else:
                rec.display_name = f"{label} {rec.period_end or ''}".strip()

    @api.depends("seats_total", "seats_used")
    def _compute_seats_free(self):
        for rec in self:
            rec.seats_free = (rec.seats_total or 0) - (rec.seats_used or 0)
            rec.saturated = bool(rec.seats_total) and rec.seats_free <= 0

    @api.depends("line_ids.errors")
    def _compute_error_count(self):
        for rec in self:
            rec.error_count = sum(rec.line_ids.mapped("errors"))


class HostingSaasBackupReadingLine(models.Model):
    _name = "hosting.saas.backup.reading.line"
    _description = "Relevé de sauvegarde infonuagique (organisation)"
    _order = "reading_id desc, organization_name"

    reading_id = fields.Many2one(
        comodel_name="hosting.saas.backup.reading",
        string="Relevé",
        required=True,
        ondelete="cascade",
        index=True,
    )
    organization_name = fields.Char(string="Organisation", required=True)
    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        index=True,
        ondelete="set null",
        help="⚠️ Résolu par le NOM de l'organisation, faute de mieux : le "
             "rapport du fournisseur ne porte pas son identifiant. La "
             "correspondance passe par les exécutions déjà versées, qui, elles, "
             "portent les deux. Un nom changé chez le fournisseur laisse donc "
             "une ligne non rattachée plutôt qu'une ligne fausse.",
    )
    errors = fields.Integer(string="Erreurs")
    items = fields.Integer(string="Éléments nouveaux ou modifiés")
    size_display = fields.Char(
        string="Volume",
        help="Tel que le fournisseur l'écrit. Le courriel n'a qu'un chiffre "
             "significatif (« 5 GB ») : on garde le mot, pas une fausse "
             "précision en octets.",
    )
    period_end = fields.Date(related="reading_id.period_end", store=True, index=True)
