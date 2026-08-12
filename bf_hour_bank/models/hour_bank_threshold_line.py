from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class HourBankThresholdLine(models.Model):
    _name = 'hour.bank.threshold.line'
    _description = "Palier de notification d'une banque d'heures"
    _order = 'bank_id, value'

    bank_id = fields.Many2one(
        'hour.bank.client', required=True, ondelete='cascade',
        string="Banque d'heures", index=True,
    )
    mode = fields.Selection(
        related='bank_id.threshold_mode', store=True, readonly=True,
        string="Mode",
    )
    value = fields.Float(
        required=True, string="Seuil",
        help="Heures pour les modes 'Non facturées' et 'Solde résiduel'. "
             "Pourcentage (0-100) pour le mode '% du budget'. "
             "En 'Solde résiduel', une valeur négative pose le palier sous zéro : "
             "c'est la forme utile sur un compte postpayé, où les heures "
             "s'accumulent en dette entre deux factures.",
    )
    name = fields.Char(
        compute='_compute_name', store=True, string="Libellé",
    )
    active = fields.Boolean(default=True)
    last_fired_date = fields.Datetime(
        readonly=True, string="Dernier déclenchement",
    )
    last_fired_period_key = fields.Char(
        readonly=True, string="Clé de période",
        help="Clé d'idempotence : tant qu'elle ne change pas, le palier "
             "ne se redéclenche pas. Reset selon le mode "
             "(nouvelle facture, changement de budget, ou remontée du solde).",
    )
    is_armed = fields.Boolean(
        compute='_compute_is_armed', string="Armé",
        help="Vrai si le palier est prêt à déclencher (clé de période courante différente de la dernière).",
    )

    _sql_constraints = [
        (
            'unique_bank_value',
            'UNIQUE(bank_id, value)',
            "Un palier avec cette valeur existe déjà sur cette banque.",
        ),
    ]

    @api.depends('mode', 'value')
    def _compute_name(self):
        for rec in self:
            if rec.mode == 'budget_pct':
                rec.name = _("%(value)s %% du budget", value=int(rec.value) if rec.value == int(rec.value) else rec.value)
            elif rec.mode == 'unbilled':
                rec.name = _("%(value)sh non facturées", value=rec.value)
            elif rec.mode == 'balance_floor':
                if rec.value < 0:
                    # Compte postpayé : le solde plonge sous zéro, le palier
                    # se lit comme une dette d'heures et non comme un reste.
                    rec.name = _("Dette de %(value)sh", value=abs(rec.value))
                else:
                    rec.name = _("Solde sous %(value)sh", value=rec.value)
            else:
                rec.name = _("%(value)s", value=rec.value)

    @api.depends('last_fired_period_key', 'bank_id.threshold_mode', 'bank_id.threshold_budget_hours')
    def _compute_is_armed(self):
        for rec in self:
            if not rec.bank_id or rec.bank_id.threshold_mode == 'disabled':
                rec.is_armed = False
                continue
            current_key = rec.bank_id._current_period_key()
            rec.is_armed = (rec.last_fired_period_key or '') != current_key

    @api.constrains('value', 'mode')
    def _check_value(self):
        for rec in self:
            if rec.mode == 'budget_pct':
                if rec.value <= 0 or rec.value > 100:
                    raise ValidationError(_(
                        "Pour le mode '%% du budget', le seuil doit être strictement supérieur à 0 et au plus égal à 100."
                    ))
            elif rec.mode == 'balance_floor':
                # Une valeur négative est légitime sur un compte postpayé, où
                # les heures s'accumulent en dette avant d'être facturées : le
                # palier se pose alors sous zéro. Zéro reste refusé, sinon un
                # palier créé sans valeur alerterait dès le premier solde nul.
                if rec.value == 0:
                    raise ValidationError(_(
                        "Le seuil de solde résiduel ne peut pas être zéro."
                    ))
            else:
                if rec.value <= 0:
                    raise ValidationError(_(
                        "Le seuil doit être strictement positif."
                    ))
