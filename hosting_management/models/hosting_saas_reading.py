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

from .hosting_lang import reader_env

_logger = logging.getLogger(__name__)


class HostingSaasBackupReading(models.Model):
    _name = "hosting.saas.backup.reading"
    _description = "Cloud backup reading"
    _order = "period_end desc, id desc"
    _rec_name = "display_name"

    provider = fields.Selection(
        selection=[("cubebackup", "CubeBackup")],
        string="Product",
        required=True,
        default="cubebackup",
        index=True,
    )
    period_start = fields.Date(string="Period start")
    period_end = fields.Date(string="Period end", required=True, index=True)
    display_name = fields.Char(compute="_compute_display_name", store=True)

    # ── Licence ──────────────────────────────────────────────────────────
    seats_total = fields.Integer(string="Purchased seats")
    seats_used = fields.Integer(string="Used seats")
    seats_free = fields.Integer(
        string="Free seats",
        compute="_compute_seats_free",
        store=True,
    )
    saturated = fields.Boolean(
        string="License full",
        compute="_compute_seats_free",
        store=True,
    )
    license_expiry = fields.Date(string="License expiration")

    # ── Stockage ─────────────────────────────────────────────────────────
    storage_kind = fields.Char(string="Storage")
    storage_location = fields.Char(string="Location")
    storage_available = fields.Char(string="Available space")
    index_path = fields.Char(string="Index")
    index_free_display = fields.Char(string="Index space")
    index_free_pct = fields.Float(string="Free index space (%)", digits=(5, 1))

    # ── Provenance ───────────────────────────────────────────────────────
    ingest_source = fields.Selection(
        selection=[
            ("email", "Email report"),
            ("api", "Console API"),
            ("manual", "Manual entry"),
        ],
        string="Read via",
        default="email",
    )
    source_ref = fields.Char(
        string="Source reference",
        help="What the reading was taken from, from the reader's point of "
             "view (e.g. the email identifier in your mailbox). The module does "
             "not know what this reference points to and never follows it.",
    )
    line_ids = fields.One2many(
        comodel_name="hosting.saas.backup.reading.line",
        inverse_name="reading_id",
        string="By organization",
    )
    error_count = fields.Integer(
        string="Errors in the period",
        compute="_compute_error_count",
        store=True,
    )

    _sql_constraints = [
        (
            "saas_reading_unique",
            "unique(provider, period_end)",
            "A reading already exists for this product and this period.",
        ),
    ]

    @api.depends("period_start", "period_end", "provider")
    def _compute_display_name(self):
        # Nom STOCKÉ (calculé au dépôt du relevé, souvent par l'API) : libellé
        # de sélection traduit et texte dans la langue de la société.
        env = reader_env(self.env)
        labels = dict(self._fields["provider"]._description_selection(env))
        for rec in self:
            label = labels.get(rec.provider, "")
            if rec.period_start and rec.period_end:
                rec.display_name = env._(
                    "%(provider)s %(start)s to %(end)s",
                    provider=label, start=rec.period_start, end=rec.period_end,
                )
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
    _description = "Cloud backup reading (organization)"
    _order = "reading_id desc, organization_name"

    reading_id = fields.Many2one(
        comodel_name="hosting.saas.backup.reading",
        string="Reading",
        required=True,
        ondelete="cascade",
        index=True,
    )
    organization_name = fields.Char(string="Organization", required=True)
    service_id = fields.Many2one(
        comodel_name="hosting.service",
        string="Service",
        index=True,
        ondelete="set null",
        help="⚠️ Resolved by the organization NAME, for lack of anything "
             "better: the provider's report does not carry its "
             "identifier. The match goes through the runs already "
             "received, which carry both. A name changed at the provider "
             "therefore leaves an unlinked line rather than a wrong one.",
    )
    errors = fields.Integer(string="Errors")
    items = fields.Integer(string="New or changed items")
    size_display = fields.Char(
        string="Volume",
        help="As the provider writes it. The email has only one "
             "significant digit (\"5 GB\"): we keep the word, not a false "
             "precision in bytes.",
    )
    period_end = fields.Date(related="reading_id.period_end", store=True, index=True)
