"""Gabarits d'obligations de conformité, et le calcul de leur échéance.

Un gabarit porte une obligation récurrente (son type, sa base légale, sa règle
d'échéance), JAMAIS une date. Le module livrait avant 18.0.2.0.0 cinq événements
datés de l'éditeur dans la table de travail de chaque base : des échéances en
retard qui n'étaient celles de personne d'autre. Les gabarits sont le
catalogue ; les événements, eux, sortent du générateur (`corporate.compliance.generate`),
par exercice et par société, au geste d'une personne.

Un gabarit livré est réécrit à chaque montée (`noupdate="0"`) : c'est ainsi qu'une
montée corrige un article ou une règle. Elle ne touche jamais un événement déjà
généré, qui a sa propre copie du nom, des notes et de la base légale. Pour adapter
un gabarit livré, on le duplique (la copie n'a pas d'identifiant externe, donc
aucune montée ne la touche) et on archive l'original.
"""

import calendar
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

LEGAL_FORMS = [
    ('qc_business_corporation', "Business corporation (Québec)"),
    ('qc_nonprofit', "Non-profit legal person (Québec)"),
    ('qc_cooperative', "Cooperative (Québec)"),
]

EVENT_TYPES = [
    ('annual_declaration', "Annual declaration"),
    ('agm', "Annual general meeting"),
    ('director_election', "Election of directors"),
    ('auditor_appointment', "Auditor appointment"),
    ('financial_approval', "Financial approval"),
    ('bylaw_review', "By-law review"),
    ('annual_report', "Annual report"),
    ('tax_return', "Tax return"),
    ('tax_payment', "Tax payment"),
    ('information_return', "Information return"),
    ('ohs_filing', "Health and safety filing"),
    ('ohs_program', "Health and safety program"),
    ('other', "Other"),
]

MONTHS = [(str(m), calendar.month_name[m]) for m in range(1, 13)]


def ajouter(depart, mois=0, jours=0, fin_de_mois=True):
    """`depart` + `mois` + `jours`, en suivant la fin de mois.

    Si `depart` est le dernier jour de son mois, l'arrivée est le dernier jour du mois
    d'arrivée : 30 juin + 6 mois = 31 décembre, pas le 30 (c'est ainsi que l'ARC compte
    l'échéance de la T2). `fin_de_mois=False` pour une borne comptée depuis un événement
    (assemblée tenue, constitution) : « dans les 15 mois » d'une AGA du 28 février tombe le
    28 mai, pas le 31. Sinon, le jour est borné au dernier du mois d'arrivée (30 mai
    + 9 mois = 28 ou 29 février). Les jours s'ajoutent après.
    """
    arrivee = depart + relativedelta(months=mois)
    if fin_de_mois and mois and depart.day == calendar.monthrange(depart.year, depart.month)[1]:
        arrivee = date_bornee(arrivee.year, arrivee.month, 31)
    return arrivee + relativedelta(days=jours)


def date_bornee(annee, mois, jour):
    """`annee-mois-jour`, le jour ramené au dernier du mois (31 = fin de mois, 29 février)."""
    return date(annee, mois, min(jour, calendar.monthrange(annee, mois)[1]))


class CorporateComplianceTemplate(models.Model):
    _name = 'corporate.compliance.template'
    _description = "Corporate compliance template"
    _order = 'legal_form, scope, sequence, id'

    name = fields.Char(string="Name", required=True, translate=True)
    code = fields.Char(
        string="Code", required=True, copy=False,
        help="Stable identifier of the obligation, used to recognise it from one "
             "fiscal year to the next.")
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    legal_form = fields.Selection(
        selection=LEGAL_FORMS + [('any', "Any legal form")],
        string="Legal form", required=True, default='any')
    scope = fields.Selection(
        selection=[
            ('corporate', "Corporate law"),
            ('tax', "Tax"),
            ('ohs', "Health and safety"),
        ],
        string="Area", required=True, default='corporate')
    event_type = fields.Selection(
        selection=EVENT_TYPES, string="Event type", required=True, default='other')
    legal_basis = fields.Char(
        string="Legal basis", translate=True,
        help="Act and section the obligation comes from, as it will be printed on "
             "the generated event.")
    reference_url = fields.Char(string="Reference link")
    notes = fields.Text(string="Notes", translate=True)
    condition = fields.Char(
        string="Applies when", translate=True,
        help="Condition under which the obligation applies (employees, dividends, "
             "registered charity...). An obligation with a condition is offered "
             "unticked by the generator.")

    # --- La règle d'échéance -------------------------------------------------
    anchor = fields.Selection(
        selection=[
            ('fiscal_year_end', "End of the fiscal year"),
            ('fixed_date', "Fixed date, the year after the fiscal year ends"),
        ],
        string="Deadline counted from", required=True, default='fiscal_year_end')
    offset_months = fields.Integer(string="Months after")
    offset_days = fields.Integer(string="Days after")
    fixed_month = fields.Selection(selection=MONTHS, string="Month")
    fixed_day = fields.Integer(
        string="Day", default=31,
        help="31 means the last day of the month, whatever its length.")
    cap_months_after_previous = fields.Integer(
        string="No later than (months after the previous one)",
        help="Second bound: the event is also due within this many months of the "
             "previous occurrence of the same obligation (completed date, failing "
             "that its deadline). 0: no second bound.")
    first_cap_months_after_incorporation = fields.Integer(
        string="First one: no later than (months after incorporation)",
        help="Bound used when there is no previous occurrence, counted from the "
             "date of incorporation. 0: none.")
    deadline_rule = fields.Char(
        string="Deadline rule", compute='_compute_deadline_rule')

    _sql_constraints = [
        ('code_uniq', 'unique(code)', "Two compliance templates cannot share a code."),
    ]

    def copy_data(self, default=None):
        """Dupliquer, c'est le geste qu'on recommande pour adapter un gabarit livré : le
        code est unique, la copie reçoit donc le sien."""
        valeurs = super().copy_data(default)
        for tpl, val in zip(self, valeurs):
            if 'code' not in (default or {}):
                base, n = '%s_copy' % tpl.code, 1
                code = base
                while self.with_context(active_test=False).search_count([('code', '=', code)]):
                    n += 1
                    code = '%s%s' % (base, n)
                val['code'] = code
            if 'name' not in (default or {}):
                val['name'] = self.env._("%s (copy)", tpl.name)
        return valeurs

    @api.constrains('anchor', 'fixed_month', 'fixed_day', 'offset_months', 'offset_days')
    def _check_rule(self):
        for tpl in self:
            if tpl.anchor == 'fixed_date':
                if not tpl.fixed_month or not 1 <= tpl.fixed_day <= 31:
                    raise ValidationError(self.env._(
                        "A fixed-date template needs a month and a day between 1 and 31."))
            elif tpl.offset_months < 0 or tpl.offset_days < 0:
                raise ValidationError(self.env._(
                    "A deadline is counted after the end of the fiscal year, not before."))

    @api.depends('anchor', 'offset_months', 'offset_days', 'fixed_month', 'fixed_day',
                 'cap_months_after_previous', 'first_cap_months_after_incorporation')
    @api.depends_context('lang')
    def _compute_deadline_rule(self):
        mois = dict(self._fields['fixed_month']._description_selection(self.env))
        for tpl in self:
            if tpl.anchor == 'fixed_date' and tpl.fixed_day >= 31:
                texte = tpl.env._("Last day of %(month)s of the year after the fiscal year ends",
                                  month=mois.get(tpl.fixed_month, '?'))
            elif tpl.anchor == 'fixed_date':
                texte = tpl.env._("%(day)s %(month)s of the year after the fiscal year ends",
                                  day=tpl.fixed_day, month=mois.get(tpl.fixed_month, '?'))
            elif tpl.offset_days:
                texte = tpl.env._("%(months)s months and %(days)s days after the fiscal year ends",
                           months=tpl.offset_months, days=tpl.offset_days)
            else:
                texte = tpl.env._("%(months)s months after the fiscal year ends",
                           months=tpl.offset_months)
            if tpl.cap_months_after_previous:
                texte += tpl.env._(", and within %(n)s months of the previous one",
                            n=tpl.cap_months_after_previous)
            tpl.deadline_rule = texte

    def _due_date(self, fiscal_year_end, previous_date=False, incorporation_date=False):
        """L'échéance de ce gabarit pour l'exercice clos le `fiscal_year_end`.

        Deux bornes au plus, et la plus proche commande. C'est le cas de l'AGA d'une
        société par actions : six mois après la fin de l'exercice (art. 225 LSAQ) ET
        quinze mois après la précédente (art. 163). La première assemblée se borne par
        la constitution, faute de précédente.
        """
        self.ensure_one()
        if self.anchor == 'fixed_date':
            echeance = date_bornee(fiscal_year_end.year + 1, int(self.fixed_month), self.fixed_day)
        else:
            echeance = ajouter(fiscal_year_end, self.offset_months, self.offset_days)
        bornes = [echeance]
        if previous_date and self.cap_months_after_previous:
            bornes.append(ajouter(previous_date, self.cap_months_after_previous, fin_de_mois=False))
        elif not previous_date and incorporation_date and self.first_cap_months_after_incorporation:
            premiere = ajouter(incorporation_date, self.first_cap_months_after_incorporation,
                               fin_de_mois=False)
            # Seulement si c'est vraiment la première : une société qui commence à tenir
            # son calendrier dans Odoo à sa cinquième année n'a pas de « précédente » en
            # base, et la borne de constitution, échue depuis des années, la mettrait en
            # retard à tort.
            if premiere >= fiscal_year_end:
                bornes.append(premiere)
        return min(bornes)
