from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class Delegate(models.Model):
    """Un délégué syndical, et sa banque de libérations.

    La convention accorde un nombre d'heures de libération par période. Ce qui
    se conteste, ce n'est pas le principe, c'est le solde : combien il en
    restait le jour où l'employeur a refusé une libération. D'où une banque
    datée, et non un compteur qui se remet à zéro sans laisser de trace.
    """

    _name = "bf.labour.delegate"
    _description = "Délégué syndical"
    _inherit = ["mail.thread"]
    _order = "unit_id, date_start desc, id desc"

    membership_id = fields.Many2one(
        "bf.labour.membership", string="Appartenance", required=True,
        ondelete="cascade", index=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Personne", related="membership_id.employee_id",
        store=True, readonly=True,
    )
    unit_id = fields.Many2one(
        "bf.labour.unit", string="Unité", related="membership_id.unit_id",
        store=True, readonly=True, index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="membership_id.company_id",
        store=True, readonly=True, index=True,
    )
    role = fields.Selection(
        [
            ("delegate", "Délégué"),
            ("chief_delegate", "Délégué en chef"),
            ("executive", "Exécutif"),
            ("health_safety", "Santé et sécurité"),
        ],
        string="Fonction", default="delegate", required=True,
    )
    date_start = fields.Date(
        string="Depuis", required=True, default=fields.Date.context_today,
    )
    date_end = fields.Date(string="Jusqu'au")
    hours_granted = fields.Float(
        string="Heures accordées",
        help="Ce que la convention accorde pour la période du mandat.",
    )
    release_ids = fields.One2many(
        "bf.labour.delegate.release", "delegate_id", string="Libérations",
    )
    hours_taken = fields.Float(string="Heures prises", compute="_compute_hours")
    hours_left = fields.Float(string="Heures restantes", compute="_compute_hours")
    is_over = fields.Boolean(string="Banque dépassée", compute="_compute_hours")

    # ⚠️ NON stocké : contre aujourd'hui.
    is_current = fields.Boolean(string="En fonction", compute="_compute_is_current")

    @api.depends("release_ids.hours", "release_ids.state", "hours_granted")
    def _compute_hours(self):
        for delegate in self:
            taken = sum(
                delegate.release_ids.filtered(lambda r: r.state == "taken").mapped("hours")
            )
            delegate.hours_taken = taken
            delegate.hours_left = (delegate.hours_granted or 0.0) - taken
            delegate.is_over = bool(
                delegate.hours_granted and taken > delegate.hours_granted
            )

    @api.depends("date_start", "date_end")
    def _compute_is_current(self):
        today = fields.Date.context_today(self)
        for delegate in self:
            delegate.is_current = bool(
                delegate.date_start <= today
                and (not delegate.date_end or delegate.date_end >= today)
            )

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for delegate in self:
            if delegate.date_end and delegate.date_end < delegate.date_start:
                raise ValidationError(_(
                    "La fin d'un mandat ne précède pas son début."
                ))


class DelegateRelease(models.Model):
    """Une libération syndicale, demandée puis prise.

    🔴 Une libération REFUSÉE compte pour zéro heure mais reste au dossier :
    c'est elle qu'on ressort en grief, et la supprimer effacerait le refus.
    """

    _name = "bf.labour.delegate.release"
    _description = "Libération syndicale"
    _order = "date desc, id desc"

    delegate_id = fields.Many2one(
        "bf.labour.delegate", string="Délégué", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="delegate_id.company_id",
        store=True, readonly=True, index=True,
    )
    date = fields.Date(string="Date", required=True, default=fields.Date.context_today)
    hours = fields.Float(string="Heures", required=True)
    reason = fields.Char(string="Motif", required=True)
    grievance_id = fields.Many2one(
        "bf.labour.grievance", string="Grief concerné", ondelete="set null",
        help="Le grief du socle, pas une copie.",
    )
    state = fields.Selection(
        [
            ("requested", "Demandée"),
            ("taken", "Prise"),
            ("refused", "Refusée"),
        ],
        string="État", default="requested", required=True,
    )
    refusal_reason = fields.Text(string="Motif du refus")

    @api.constrains("state", "refusal_reason")
    def _check_refusal_reason(self):
        for release in self:
            if release.state == "refused" and not (release.refusal_reason or "").strip():
                raise ValidationError(_(
                    "Refuser une libération demande un motif écrit : c'est ce "
                    "texte qui se relit en grief."
                ))

    @api.constrains("hours")
    def _check_hours(self):
        for release in self:
            if release.hours <= 0:
                raise ValidationError(_("Une libération se compte en heures positives."))
