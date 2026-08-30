# Part of bf_music_licensing. Voir LICENSE.
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

SQFT_PER_SQM = 10.7639104167


class MusicEstablishment(models.Model):
    """Le lieu où la musique joue, et l'assiette des redevances qu'il doit."""

    _name = "bf.music.establishment"
    _description = "Établissement diffusant de la musique"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "partner_id, name"

    name = fields.Char(string="Nom", required=True, tracking=True)
    partner_id = fields.Many2one(
        "res.partner", string="Client", required=True, tracking=True,
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, required=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )

    area_value = fields.Float(string="Superficie", tracking=True)
    area_uom = fields.Selection(
        selection=[("sqm", "m²"), ("sqft", "pi²")],
        string="Unité", default="sqm", required=True,
    )
    area_sqm = fields.Float(
        string="Superficie (m²)", compute="_compute_area", store=True, digits=(16, 2),
    )
    area_sqft = fields.Float(
        string="Superficie (pi²)", compute="_compute_area", store=True, digits=(16, 2),
    )

    usage_background = fields.Boolean(
        string="Musique d'ambiance", default=True, tracking=True,
    )
    usage_on_hold = fields.Boolean(
        string="Attente téléphonique", tracking=True,
    )
    on_hold_lines = fields.Integer(string="Lignes téléphoniques", default=0)
    days_of_operation = fields.Integer(
        string="Jours d'exploitation par année", default=365,
        help="Nombre de jours où de la musique joue. Le tarif Ré:Sonne 3.B "
             "multiplie la superficie par ce nombre.",
    )
    seasonal = fields.Boolean(
        string="Établissement saisonnier",
        help="Exploité moins de six mois par année. Certains tarifs prévoient "
             "alors la moitié du taux.",
    )

    music_source = fields.Selection(
        selection=[
            ("radio", "Radio commerciale"),
            ("consumer_streaming", "Service d'écoute grand public"),
            ("business_streaming", "Service d'écoute commercial"),
            ("supplier", "Fournisseur de musique d'ambiance"),
            ("own", "Enregistrements de l'établissement"),
            ("live", "Musique en direct"),
            ("unknown", "Inconnu"),
        ],
        string="Provenance de la musique",
        default="unknown",
        required=True,
        tracking=True,
    )
    source_warning = fields.Char(
        string="Constat sur la provenance", compute="_compute_source_warning",
    )
    entandem_account = fields.Char(string="Compte Entandem", tracking=True)

    licence_ids = fields.One2many(
        "bf.music.licence", "establishment_id", string="Périodes de licence",
    )
    licence_count = fields.Integer(compute="_compute_totals", store=True)
    amount_reference = fields.Monetary(
        string="Redevances de référence", compute="_compute_totals", store=True,
        currency_field="currency_id",
    )
    amount_at_risk = fields.Monetary(
        string="Payé sous tarif non homologué", compute="_compute_totals", store=True,
        currency_field="currency_id",
        help="Ce que l'établissement a versé sur des périodes que la Commission "
             "n'a pas encore homologuées. Ce montant reste révisable.",
    )
    adjustment_total = fields.Monetary(
        string="Rajustement constaté", compute="_compute_totals", store=True,
        currency_field="currency_id",
        help="L'écart réel entre ce qui a été payé et ce que le tarif homologué "
             "commande, sur les périodes déjà homologuées.",
    )
    uncertified_period_count = fields.Integer(
        string="Périodes non homologuées", compute="_compute_totals", store=True,
    )

    @api.depends("area_value", "area_uom")
    def _compute_area(self):
        for rec in self:
            if rec.area_uom == "sqft":
                rec.area_sqft = rec.area_value
                rec.area_sqm = rec.area_value / SQFT_PER_SQM if rec.area_value else 0.0
            else:
                rec.area_sqm = rec.area_value
                rec.area_sqft = rec.area_value * SQFT_PER_SQM

    @api.depends("music_source")
    def _compute_source_warning(self):
        for rec in self:
            if rec.music_source == "consumer_streaming":
                rec.source_warning = _(
                    "Un service d'écoute grand public utilisé dans un commerce "
                    "s'ajoute aux redevances : ses conditions d'utilisation "
                    "réservent l'écoute à un usage personnel. À vérifier au "
                    "contrat du service avant d'en faire un constat écrit."
                )
            elif rec.music_source == "supplier":
                rec.source_warning = _(
                    "Un fournisseur de musique d'ambiance détient normalement "
                    "les licences de fourniture. Vérifier ce que l'abonnement "
                    "couvre avant de facturer une redevance en double."
                )
            elif rec.music_source == "unknown":
                rec.source_warning = _(
                    "Provenance non établie. C'est la première question du "
                    "diagnostic : sans elle, aucune conclusion ne tient."
                )
            else:
                rec.source_warning = False

    @api.depends(
        "licence_ids.amount_reference",
        "licence_ids.amount_at_risk",
        "licence_ids.adjustment_total",
        "licence_ids.has_uncertified",
    )
    def _compute_totals(self):
        for rec in self:
            rec.licence_count = len(rec.licence_ids)
            rec.amount_reference = sum(rec.licence_ids.mapped("amount_reference"))
            rec.amount_at_risk = sum(rec.licence_ids.mapped("amount_at_risk"))
            rec.adjustment_total = sum(rec.licence_ids.mapped("adjustment_total"))
            rec.uncertified_period_count = len(
                rec.licence_ids.filtered("has_uncertified")
            )

    def _usages(self):
        self.ensure_one()
        usages = set()
        if self.usage_background:
            usages.add("background")
        if self.usage_on_hold:
            usages.add("on_hold")
        return usages

    def action_build_history(self, year_from=2020, year_to=None):
        """Crée une période de licence par année manquante, lignes comprises.

        C'est le geste qui produit le chiffre d'exposition : une année civile
        par ligne, chacune évaluée au tarif de sa période.
        """
        year_to = year_to or date.today().year
        if year_from > year_to:
            raise UserError(_("L'année de départ suit l'année de fin."))
        created = self.env["bf.music.licence"]
        for rec in self:
            if not rec._usages():
                raise UserError(
                    _("Aucun usage n'est coché sur %s : rien à évaluer.", rec.name)
                )
            # `mapped(callable)` applique la fonction au RECORDSET entier en
            # Odoo 18, pas ligne par ligne : la compréhension est le seul
            # moyen d'obtenir une valeur par période.
            existing = {lic.date_start.year for lic in rec.licence_ids}
            for year in range(year_from, year_to + 1):
                if year in existing:
                    continue
                licence = self.env["bf.music.licence"].create({
                    "establishment_id": rec.id,
                    "date_start": date(year, 1, 1),
                    "date_end": date(year, 12, 31),
                    # Les trois tarifs relevés fixent le paiement au plus tard
                    # le 31 janvier de l'année visée.
                    "due_date": date(year, 1, 31),
                    "entandem_account": rec.entandem_account,
                })
                licence.action_generate_lines()
                created |= licence
        return created

    def action_open_licences(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Périodes de licence"),
            "res_model": "bf.music.licence",
            "view_mode": "list,form",
            "domain": [("establishment_id", "=", self.id)],
            "context": {"default_establishment_id": self.id},
        }
