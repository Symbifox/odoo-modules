# Part of bf_music_licensing. Voir LICENSE.
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MusicLicence(models.Model):
    """Une période de licence pour un établissement : l'échéance et la preuve.

    Le moteur d'échéance reprend le patron éprouvé de
    `corporate.compliance.event` (bf_corporate_governance) : un statut calculé
    et stocké, un rappel qui pose une activité, et une date de complétion qui
    ferme le dossier. Il est recopié plutôt qu'hérité : le livre des minutes
    d'une société n'a pas à devenir une dépendance d'un module client.
    """

    _name = "bf.music.licence"
    _description = "Période de licence musicale"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, establishment_id"

    name = fields.Char(compute="_compute_name", store=True)
    establishment_id = fields.Many2one(
        "bf.music.establishment", string="Établissement",
        required=True, ondelete="cascade", tracking=True,
    )
    partner_id = fields.Many2one(
        related="establishment_id.partner_id", store=True, readonly=True,
    )
    company_id = fields.Many2one(
        related="establishment_id.company_id", store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id", readonly=True,
    )

    date_start = fields.Date(string="Début", required=True)
    date_end = fields.Date(string="Fin", required=True)
    due_date = fields.Date(
        string="Échéance de renouvellement", required=True, tracking=True,
    )
    completed_date = fields.Date(string="Réglée le", tracking=True)
    status = fields.Selection(
        selection=[
            ("upcoming", "À venir"),
            ("due_soon", "Échéance proche"),
            ("overdue", "En retard"),
            ("completed", "Réglée"),
        ],
        string="Statut", compute="_compute_status", store=True, index=True,
    )
    reminder_sent = fields.Boolean(string="Rappel envoyé", default=False)

    entandem_account = fields.Char(string="Compte Entandem", tracking=True)
    payment_reference = fields.Char(
        string="Référence de paiement",
        help="Le numéro de reçu ou de facture qui prouve le versement.",
    )

    line_ids = fields.One2many(
        "bf.music.licence.line", "licence_id", string="Tarifs applicables",
    )
    amount_paid = fields.Monetary(
        string="Payé", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_reference = fields.Monetary(
        string="Redevance de référence", compute="_compute_amounts", store=True,
        currency_field="currency_id",
        help="Ce qui a été payé si le montant est connu, sinon ce que le tarif commande.",
    )
    amount_at_risk = fields.Monetary(
        string="Sous tarif non homologué", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    adjustment_total = fields.Monetary(
        string="Rajustement", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    has_uncertified = fields.Boolean(
        string="Période non homologuée", compute="_compute_amounts", store=True,
    )

    @api.depends("establishment_id.name", "date_start")
    def _compute_name(self):
        for rec in self:
            year = rec.date_start.year if rec.date_start else ""
            rec.name = f"{rec.establishment_id.name or ''} {year}".strip()

    @api.depends("due_date", "completed_date")
    def _compute_status(self):
        today = fields.Date.today()
        for rec in self:
            if rec.completed_date:
                rec.status = "completed"
            elif not rec.due_date:
                rec.status = "upcoming"
            elif rec.due_date < today:
                rec.status = "overdue"
            elif rec.due_date <= today + timedelta(days=30):
                rec.status = "due_soon"
            else:
                rec.status = "upcoming"

    @api.depends(
        "line_ids.amount_paid",
        "line_ids.amount_reference",
        "line_ids.amount_at_risk",
        "line_ids.adjustment",
        "line_ids.is_certified",
    )
    def _compute_amounts(self):
        for rec in self:
            rec.amount_paid = sum(rec.line_ids.mapped("amount_paid"))
            rec.amount_reference = sum(rec.line_ids.mapped("amount_reference"))
            rec.amount_at_risk = sum(rec.line_ids.mapped("amount_at_risk"))
            rec.adjustment_total = sum(rec.line_ids.mapped("adjustment"))
            rec.has_uncertified = any(not l.is_certified for l in rec.line_ids)

    def action_generate_lines(self):
        """Pose une ligne par tarif qui touche la période, selon les usages cochés."""
        for rec in self:
            usages = rec.establishment_id._usages()
            tariffs = self.env["bf.music.tariff"]._find_for_period(
                rec.date_start, rec.date_end, usages, company=rec.company_id,
            )
            already = rec.line_ids.mapped("tariff_id")
            for tariff in tariffs - already:
                self.env["bf.music.licence.line"].create({
                    "licence_id": rec.id,
                    "tariff_id": tariff.id,
                })
        return True

    def action_complete(self):
        self.write({"completed_date": fields.Date.today()})

    def action_reset(self):
        self.write({"completed_date": False, "reminder_sent": False})

    @api.model
    def _cron_check_licence_deadlines(self):
        """Rappel quotidien : pose une activité sur les licences qui viennent à échéance."""
        today = fields.Date.today()
        licences = self.search([
            ("completed_date", "=", False),
            ("due_date", "<=", today + timedelta(days=30)),
            ("reminder_sent", "=", False),
        ])
        group = self.env.ref(
            "bf_music_licensing.group_music_manager", raise_if_not_found=False,
        )
        if not group:
            return
        for licence in licences:
            days = (licence.due_date - today).days
            label = _("EN RETARD") if days <= 0 else _("%s jours", days)
            for user in group.users:
                licence.activity_schedule(
                    "mail.mail_activity_data_todo",
                    date_deadline=licence.due_date,
                    user_id=user.id,
                    note=_(
                        "<p><strong>Licence musicale (%(label)s)</strong></p>"
                        "<p>%(name)s — échéance : %(due)s</p>",
                        label=label, name=licence.name, due=licence.due_date,
                    ),
                )
            licence.reminder_sent = True


class MusicLicenceLine(models.Model):
    """Un tarif appliqué à une période, avec ce qui a été payé en face."""

    _name = "bf.music.licence.line"
    _description = "Ligne de redevance musicale"
    _order = "licence_id, tariff_id"

    licence_id = fields.Many2one(
        "bf.music.licence", required=True, ondelete="cascade", index=True,
    )
    tariff_id = fields.Many2one("bf.music.tariff", string="Tarif", required=True)
    establishment_id = fields.Many2one(
        related="licence_id.establishment_id", store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related="licence_id.currency_id", readonly=True,
    )
    society = fields.Selection(related="tariff_id.society", readonly=True)
    basis = fields.Selection(
        related="tariff_id.basis", string="Assiette du tarif", readonly=True,
    )
    tariff_state = fields.Selection(related="tariff_id.state", readonly=True)
    rate_confirmed = fields.Boolean(
        related="tariff_id.rate_confirmed", readonly=True,
    )
    is_certified = fields.Boolean(
        string="Homologué", compute="_compute_amounts", store=True,
    )

    base_quantity = fields.Float(
        string="Assiette",
        compute="_compute_base_quantity", store=True, readonly=False,
        digits=(16, 2),
        help="Reprise de l'établissement selon l'assiette du tarif. "
             "Modifiable quand le tarif compte autrement.",
    )
    amount_paid = fields.Monetary(
        string="Payé", currency_field="currency_id",
    )
    amount_proposed = fields.Monetary(
        string="Au taux proposé", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_certified = fields.Monetary(
        string="Au taux homologué", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_reference = fields.Monetary(
        string="Référence", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    amount_at_risk = fields.Monetary(
        string="Sous tarif non homologué", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )
    adjustment = fields.Monetary(
        string="Rajustement", compute="_compute_amounts", store=True,
        currency_field="currency_id",
    )

    @api.depends(
        "tariff_id",
        "tariff_id.basis",
        "licence_id.establishment_id.area_sqm",
        "licence_id.establishment_id.area_sqft",
        "licence_id.establishment_id.on_hold_lines",
        "licence_id.establishment_id.days_of_operation",
    )
    def _compute_base_quantity(self):
        for rec in self:
            est = rec.licence_id.establishment_id
            days = est.days_of_operation or 0
            basis = rec.tariff_id.basis
            if basis == "area_sqm":
                rec.base_quantity = est.area_sqm
            elif basis == "area_sqft":
                rec.base_quantity = est.area_sqft
            elif basis == "area_sqm_day":
                rec.base_quantity = est.area_sqm * days
            elif basis == "area_sqft_day":
                rec.base_quantity = est.area_sqft * days
            elif basis == "line":
                rec.base_quantity = est.on_hold_lines
            else:
                rec.base_quantity = 1.0

    @api.depends(
        "base_quantity", "amount_paid",
        "tariff_id.state", "tariff_id.basis", "tariff_id.seasonal_half",
        "tariff_id.rate_proposed", "tariff_id.rate_certified",
        "tariff_id.rate_base_proposed", "tariff_id.rate_base_certified",
        "tariff_id.minimum_proposed", "tariff_id.minimum_certified",
        "licence_id.establishment_id.seasonal",
    )
    def _compute_amounts(self):
        for rec in self:
            tariff = rec.tariff_id
            certified = tariff.state == "certified"
            rec.is_certified = certified
            seasonal = (
                tariff.seasonal_half and rec.licence_id.establishment_id.seasonal
            )
            rec.amount_proposed = rec._gross(
                tariff.rate_base_proposed, tariff.rate_proposed,
                tariff.minimum_proposed, seasonal,
            )
            rec.amount_certified = rec._gross(
                tariff.rate_base_certified, tariff.rate_certified,
                tariff.minimum_certified, seasonal,
            ) if certified else 0.0
            # Ce qui a été payé prime : le tarif ne sert de référence que tant
            # qu'on ignore le montant réel.
            rec.amount_reference = rec.amount_paid or rec.amount_proposed
            rec.amount_at_risk = 0.0 if certified else rec.amount_reference
            rec.adjustment = (
                rec.amount_certified - rec.amount_reference if certified else 0.0
            )

    def _gross(self, rate_base, rate, minimum, seasonal):
        """Montant brut d'une ligne, selon la façon dont le tarif est rédigé.

        Deux rédactions coexistent dans les tarifs réels. La plupart posent un
        taux par unité d'assiette. Ceux de l'attente téléphonique posent un
        montant pour la PREMIÈRE ligne, puis un taux par ligne de plus : les
        multiplier par le nombre total surfacturerait la première.
        """
        self.ensure_one()
        qty = self.base_quantity
        if rate_base:
            amount = rate_base + rate * max(qty - 1.0, 0.0) if qty else 0.0
        else:
            amount = rate * qty
        if seasonal:
            amount /= 2.0
        return max(amount, minimum)

    @api.constrains("licence_id", "tariff_id")
    def _check_tariff_period(self):
        for rec in self:
            licence = rec.licence_id
            tariff = rec.tariff_id
            if tariff.date_start > licence.date_end or tariff.date_end < licence.date_start:
                raise ValidationError(_(
                    "Le tarif %(tariff)s ne couvre pas la période %(licence)s.",
                    tariff=tariff.name, licence=licence.name,
                ))
