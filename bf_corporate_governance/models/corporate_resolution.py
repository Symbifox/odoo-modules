from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CorporateResolution(models.Model):
    _name = 'corporate.resolution'
    _description = "Corporate resolution"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'meeting_date desc, sequence desc'

    name = fields.Char(
        string="Title",
        required=True,
        tracking=True,
    )
    sequence = fields.Char(
        string="Reference",
        readonly=True,
        copy=False,
        default='New',
    )
    resolution_type = fields.Selection(
        selection=[
            ('board', "Board resolution"),
            ('shareholder', "Shareholder resolution"),
            ('written_board', "Written board resolution"),
            ('written_shareholder', "Written shareholder resolution"),
            # Une association, un OBNL, une coopérative : la résolution est
            # adoptée en assemblée des membres. Sans ce type, elle s'inscrivait
            # comme une résolution d'actionnaires, et son PDF le disait.
            ('members', "Members' resolution"),
        ],
        string="Resolution type",
        required=True,
        default='board',
        tracking=True,
    )
    meeting_type = fields.Selection(
        selection=[
            ('regular', "Regular"),
            ('special', "Special"),
            ('agm', "AGM"),
            ('written', "Written"),
        ],
        string="Meeting type",
        default='regular',
        tracking=True,
    )
    subject_category = fields.Selection(
        selection=[
            ('officer_appointment', "Officer appointment"),
            ('director_election', "Director election"),
            ('dividend_declaration', "Dividend declaration"),
            ('share_issuance', "Share issuance"),
            ('bylaw_amendment', "By-law amendment"),
            ('contract_approval', "Contract approval"),
            ('bank_authorization', "Banking authorization"),
            ('auditor_appointment', "Auditor appointment"),
            ('fiscal_year', "Fiscal year"),
            ('financial_approval', "Financial approval"),
            ('dissolution', 'Dissolution'),
            ('other', "Other"),
        ],
        string="Subject category",
        tracking=True,
    )
    status = fields.Selection(
        selection=[
            ('draft', "Draft"),
            ('proposed', "Proposed"),
            ('adopted', "Adopted"),
            ('rejected', "Rejected"),
            ('superseded', "Superseded"),
        ],
        string="Status",
        default='draft',
        required=True,
        tracking=True,
        index=True,
    )
    meeting_date = fields.Date(
        string="Meeting date",
        required=True,
        tracking=True,
    )
    whereas_text = fields.Html(
        string="WHEREAS...",
        help="Preamble / \"whereas\" clauses",
    )
    resolved_text = fields.Html(
        string="BE IT RESOLVED THAT...",
        help="Body of the resolution",
    )
    mover_id = fields.Many2one(
        'res.partner',
        string="Mover",
        tracking=True,
    )
    seconder_id = fields.Many2one(
        'res.partner',
        string="Seconder",
        tracking=True,
    )
    vote_for = fields.Integer(string="Votes for")
    vote_against = fields.Integer(string="Votes against")
    vote_abstain = fields.Integer(string='Abstentions')
    unanimously_adopted = fields.Boolean(
        string="Unanimously adopted",
        tracking=True,
    )
    effective_date = fields.Date(
        string="Effective date",
        tracking=True,
    )
    superseded_by_id = fields.Many2one(
        'corporate.resolution',
        string="Superseded by",
    )
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'corporate_resolution_attachment_rel',
        'resolution_id',
        'attachment_id',
        string="Attachments",
    )
    document_ids = fields.Many2many(
        'project.document',
        'corporate_resolution_document_rel',
        'resolution_id',
        'document_id',
        string="Minute book documents",
    )
    company_id = fields.Many2one(
        'res.company',
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    signatory_ids = fields.One2many(
        'corporate.resolution.signatory',
        'resolution_id',
        string="Signatories",
        # Un One2many ne se copie PAS par défaut, mais le texte de la
        # résolution et son proposeur, eux, se copient. Une résolution
        # dupliquée repartirait donc avec son dispositif intact et un bloc de
        # signature vide, qui se rabattrait en silence sur le seul proposeur :
        # exactement le document faux que cette fiche corrige.
        copy=True,
        help="The people who sign the resolution, with the capacity in "
             "which they sign it. Left empty, only a board resolution can "
             "be inferred, from its directors in office on the meeting "
             "date.",
    )
    notes = fields.Text(string='Notes')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('sequence', 'New') == 'New':
                vals['sequence'] = self.env['ir.sequence'].next_by_code(
                    'corporate.resolution'
                ) or 'New'
        return super().create(vals_list)

    def action_propose(self):
        self.write({'status': 'proposed'})

    def action_adopt(self):
        vals = {'status': 'adopted'}
        for rec in self:
            if not rec.effective_date:
                vals['effective_date'] = rec.meeting_date
        self.write(vals)

    def action_reject(self):
        self.write({'status': 'rejected'})

    def action_reset_draft(self):
        self.write({'status': 'draft'})

    def _get_active_directors(self):
        """Retourner les administrateurs actifs pour la société de la résolution."""
        return self.env['corporate.director'].search([
            ('company_id', '=', self.company_id.id),
            ('is_active', '=', True),
        ])

    def _get_directors_at_date(self, date=None):
        """Administrateurs en poste à une date donnée, la séance par défaut.

        ``is_active`` répond « aujourd'hui ». Une résolution s'imprime des mois
        ou des années après sa séance : sans borne de date, le document nomme
        des administrateurs qui n'étaient pas encore élus. Même convention de
        bornes que ``_compute_is_active`` — un mandat qui se termine le jour J
        n'est plus en cours le jour J.
        """
        self.ensure_one()
        date = date or self.meeting_date or fields.Date.today()
        return self.env['corporate.director'].search([
            ('company_id', '=', self.company_id.id),
            ('appointment_date', '<=', date),
            '|', ('end_date', '=', False), ('end_date', '>', date),
        ])

    def _get_signatories(self):
        """Bloc de signature du rapport : qui signe, en quelle qualité, à quelle fin.

        Trois cas, dans l'ordre :

        1. des signataires sont saisis — ils font foi, avec leur qualité;
        2. sinon, résolution du CONSEIL — les administrateurs en poste à la
           date de la séance, en qualité d'administrateur;
        3. sinon, résolution des MEMBRES : deux lignes à remplir, sous
           « Présidence d'assemblée » et « Secrétariat d'assemblée ». Une
           résolution des membres se signe par les personnes qui ont tenu
           l'assemblée, pas par celles qui ont proposé ou appuyé;
        4. sinon, résolution des actionnaires : le proposeur et le secondeur,
           NOMMÉS SANS QUALITÉ. Le module ne tient pas de registre des
           actionnaires : il sait qui a porté la résolution, pas en quelle
           qualité cette personne l'a signée. Le gabarit le nomme donc sans
           rien affirmer, et une ligne vide reste s'il n'y a personne.

        Le cas 2 est un repli, pas la règle : le conseil signe bien par ses
        administrateurs, mais dès qu'un signataire s'écarte de cette liste
        (dénonciation d'intérêt contresignée, fondé de pouvoir), il faut le
        saisir.
        """
        self.ensure_one()
        if self.signatory_ids:
            return [{
                'name': ligne.partner_id.name,
                'capacity': ligne.capacity_label,
                'purpose': ligne.purpose,
            } for ligne in self.signatory_ids]
        if self.resolution_type in ('board', 'written_board'):
            return [{
                'name': administrateur.partner_id.name,
                'capacity': self.env._("Director"),
                'purpose': False,
            } for administrateur in self._get_directors_at_date()]
        if self.resolution_type == 'members':
            qualites = dict(self.env['corporate.resolution.signatory']._fields['capacity']
                            ._description_selection(self.env))
            return [{
                'name': '',
                'capacity': qualites[qualite],
                'purpose': False,
            } for qualite in ('assembly_chair', 'assembly_secretary')]
        return [{
            'name': partenaire.name,
            'capacity': False,
            'purpose': False,
        } for partenaire in (self.mover_id | self.seconder_id)]

    @api.constrains('resolution_type')
    def _check_members_type_signatories(self):
        self._check_members_signatories()

    def _check_members_signatories(self):
        """Aucun signataire d'une résolution des membres en qualité d'actionnaire."""
        for rec in self.filtered(lambda r: r.resolution_type == 'members'):
            actionnaires = rec.signatory_ids.filtered(
                lambda l: l.capacity in ('shareholder', 'sole_shareholder'))
            if actionnaires:
                raise ValidationError(self.env._(
                    "A members' resolution is not signed in the capacity "
                    "of shareholder: choose another capacity for %s.",
                    ', '.join(actionnaires.partner_id.mapped('name'))))

    def _get_resolution_type_label(self):
        """Le titre d'en-tête du PDF selon le type de résolution.

        Lu par les administrateurs dans le PDF, rendu dans la langue de qui
        l'imprime (le gabarit ne fixe pas `t-lang`) : `self.env._`.
        """
        if self.resolution_type in ('board', 'written_board'):
            return self.env._("Written directors' resolution")
        if self.resolution_type == 'members':
            return self.env._("Members' meeting resolution")
        return self.env._("Written shareholder resolution")
