"""Le générateur : un exercice, une société, un geste.

La personne choisit la société, sa forme juridique et la fin de l'exercice; le
générateur propose une ligne par gabarit applicable, avec l'échéance que la règle
donne. On décoche ce qui ne s'applique pas, on corrige une date au besoin, et on
génère. Une obligation déjà générée pour cette société et cet exercice est montrée,
grisée, et jamais recréée : générer deux fois ne double rien.
"""

import calendar

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from ..models.corporate_compliance_template import LEGAL_FORMS, date_bornee


class CorporateComplianceGenerate(models.TransientModel):
    _name = 'corporate.compliance.generate'
    _description = "Generate a fiscal year's compliance events"

    company_id = fields.Many2one(
        'res.company', string="Company", required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [('id', 'in', self.env.companies.ids)])
    legal_form = fields.Selection(selection=LEGAL_FORMS, string="Legal form", required=True)
    fiscal_year_end = fields.Date(string="Fiscal year end", required=True)
    fiscal_year = fields.Char(
        string="Fiscal year", required=True,
        help="Label printed on the events, the year the fiscal year ends by default.")
    incorporation_date = fields.Date(
        string="Date of incorporation",
        help="Bounds the first annual meeting when there is no previous one.")
    include_tax = fields.Boolean(string="Tax obligations", default=True)
    include_ohs = fields.Boolean(string="Health and safety obligations", default=True)
    remember = fields.Boolean(
        string="Remember on the company", default=True,
        help="Keep the legal form, incorporation date and fiscal year end on the "
             "company, to prefill the next generation.")
    line_ids = fields.One2many(
        'corporate.compliance.generate.line', 'wizard_id', string="Obligations")

    # --- Valeurs de départ ------------------------------------------------------

    @api.model
    def _profil(self, company):
        """Forme, constitution et dernière fin d'exercice CLOSE, lues sur la société."""
        valeurs = {
            'legal_form': company.corporate_legal_form or False,
            'incorporation_date': company.corporate_incorporation_date or False,
            'fiscal_year_end': False,
        }
        if company.corporate_fy_end_month and 1 <= (company.corporate_fy_end_day or 0) <= 31:
            aujourdhui = fields.Date.context_today(self)
            mois, jour = int(company.corporate_fy_end_month), company.corporate_fy_end_day
            fin = date_bornee(aujourdhui.year, mois, jour)
            if fin > aujourdhui:
                fin = date_bornee(aujourdhui.year - 1, mois, jour)
            valeurs['fiscal_year_end'] = fin
        return valeurs

    @api.model
    def default_get(self, fields_list):
        valeurs = super().default_get(fields_list)
        company = self.env['res.company'].browse(
            valeurs.get('company_id') or self.env.company.id)
        for cle, valeur in self._profil(company).items():
            valeurs.setdefault(cle, valeur)
        if valeurs.get('fiscal_year_end') and not valeurs.get('fiscal_year'):
            valeurs['fiscal_year'] = str(fields.Date.to_date(valeurs['fiscal_year_end']).year)
        return valeurs

    @api.onchange('company_id')
    def _onchange_company_id(self):
        for cle, valeur in self._profil(self.company_id).items():
            if valeur:  # une société sans profil ne vide pas ce que la personne a choisi
                self[cle] = valeur

    @api.onchange('fiscal_year_end')
    def _onchange_fiscal_year_end(self):
        if self.fiscal_year_end:
            self.fiscal_year = str(self.fiscal_year_end.year)

    @api.onchange('company_id', 'legal_form', 'fiscal_year_end', 'incorporation_date',
                  'include_tax', 'include_ohs')
    def _onchange_lines(self):
        self.line_ids = [Command.clear()] + [
            Command.create(valeurs) for valeurs in self._line_values()]

    # --- Les lignes ---------------------------------------------------------------

    def _templates(self):
        self.ensure_one()
        domaine = [('legal_form', 'in', [self.legal_form, 'any'])]
        portees = ['corporate'] + (['tax'] if self.include_tax else []) \
            + (['ohs'] if self.include_ohs else [])
        domaine.append(('scope', 'in', portees))
        return self.env['corporate.compliance.template'].search(domaine)

    def _domaine_existant(self, tpl):
        """L'obligation existe déjà pour cette société et cet exercice : même fin d'exercice,
        ou, pour un événement sans fin d'exercice (un ancien événement livré, gardé et
        rattaché à son gabarit), même libellé d'exercice."""
        return [
            ('template_id', '=', tpl.id),
            ('company_id', '=', self.company_id.id),
            '|', ('fiscal_year_end', '=', self.fiscal_year_end),
            '&', ('fiscal_year_end', '=', False), ('fiscal_year', '=', self.fiscal_year),
        ]

    def _line_values(self):
        self.ensure_one()
        if not (self.company_id and self.legal_form and self.fiscal_year_end):
            return []
        Event = self.env['corporate.compliance.event']
        lignes = []
        for tpl in self._templates():
            deja = Event.search_count(self._domaine_existant(tpl))
            precedent = Event.search([
                ('template_id', '=', tpl.id),
                ('company_id', '=', self.company_id.id),
                ('fiscal_year_end', '<', self.fiscal_year_end),
            ], order='fiscal_year_end desc', limit=1)
            lignes.append({
                'template_id': tpl.id,
                'sequence': tpl.sequence,
                'already_generated': bool(deja),
                'selected': not deja and not tpl.condition,
                'due_date': tpl._due_date(
                    self.fiscal_year_end,
                    previous_date=precedent.completed_date or precedent.due_date,
                    incorporation_date=self.incorporation_date),
            })
        return lignes

    # --- Le geste -------------------------------------------------------------------

    def action_generate(self):
        self.ensure_one()
        if not self.line_ids:
            self.line_ids = [Command.create(v) for v in self._line_values()]
        Event = self.env['corporate.compliance.event']
        valeurs = []
        for ligne in self.line_ids.filtered(lambda l: l.selected and not l.already_generated):
            tpl = ligne.template_id
            # Revérifié ici, pas seulement à l'ouverture : deux personnes, ou deux
            # onglets, peuvent générer le même exercice.
            if Event.search_count(self._domaine_existant(tpl)):
                continue
            valeurs.append({
                'name': "%s %s" % (tpl.name, self.fiscal_year),
                'event_type': tpl.event_type,
                'template_id': tpl.id,
                'fiscal_year': self.fiscal_year,
                'fiscal_year_end': self.fiscal_year_end,
                'due_date': ligne.due_date,
                'legal_basis': tpl.legal_basis,
                'notes': tpl.notes,
                'company_id': self.company_id.id,
            })
        if not valeurs:
            raise UserError(self.env._(
                "Nothing to generate: every ticked obligation already exists for this "
                "company and fiscal year."))
        evenements = Event.create(valeurs)
        if self.remember:
            self.company_id.sudo().write({
                'corporate_legal_form': self.legal_form,
                'corporate_incorporation_date': self.incorporation_date,
                'corporate_fy_end_month': str(self.fiscal_year_end.month),
                # Une fin de mois est retenue comme 31 : une fin au 28 février devient le
                # 29 les années bissextiles.
                'corporate_fy_end_day': 31 if self.fiscal_year_end.day == calendar.monthrange(
                    self.fiscal_year_end.year, self.fiscal_year_end.month)[1]
                else self.fiscal_year_end.day,
            })
        action = self.env['ir.actions.act_window']._for_xml_id(
            'bf_corporate_governance.corporate_compliance_action')
        action.update({
            'domain': [('id', 'in', evenements.ids)],
            'context': {},
        })
        return action


class CorporateComplianceGenerateLine(models.TransientModel):
    _name = 'corporate.compliance.generate.line'
    _description = "Obligation offered by the generator"
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'corporate.compliance.generate', required=True, ondelete='cascade')
    sequence = fields.Integer()
    template_id = fields.Many2one(
        'corporate.compliance.template', string="Obligation", required=True,
        ondelete='cascade')
    scope = fields.Selection(related='template_id.scope')
    legal_basis = fields.Char(related='template_id.legal_basis')
    condition = fields.Char(related='template_id.condition')
    selected = fields.Boolean(string="Generate")
    due_date = fields.Date(string="Deadline", required=True)
    already_generated = fields.Boolean(string="Already generated", readonly=True)

