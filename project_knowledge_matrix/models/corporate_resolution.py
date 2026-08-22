from odoo import _, api, fields, models


class CorporateResolution(models.Model):
    _name = 'corporate.resolution'
    _description = 'Résolution corporative'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'meeting_date desc, sequence desc'

    name = fields.Char(
        string='Titre',
        required=True,
        tracking=True,
    )
    sequence = fields.Char(
        string='Référence',
        readonly=True,
        copy=False,
        default='New',
    )
    resolution_type = fields.Selection(
        selection=[
            ('board', 'Résolution du conseil'),
            ('shareholder', 'Résolution des actionnaires'),
            ('written_board', 'Résolution écrite du conseil'),
            ('written_shareholder', 'Résolution écrite des actionnaires'),
        ],
        string='Type de résolution',
        required=True,
        default='board',
        tracking=True,
    )
    meeting_type = fields.Selection(
        selection=[
            ('regular', 'Ordinaire'),
            ('special', 'Extraordinaire'),
            ('agm', 'AGA'),
            ('written', 'Écrite'),
        ],
        string='Type de séance',
        default='regular',
        tracking=True,
    )
    subject_category = fields.Selection(
        selection=[
            ('officer_appointment', 'Nomination de dirigeant'),
            ('director_election', "Élection d'administrateur"),
            ('dividend_declaration', 'Déclaration de dividende'),
            ('share_issuance', "Émission d'actions"),
            ('bylaw_amendment', 'Modification de règlements'),
            ('contract_approval', 'Approbation de contrat'),
            ('bank_authorization', 'Autorisation bancaire'),
            ('auditor_appointment', 'Nomination du vérificateur'),
            ('fiscal_year', 'Année fiscale'),
            ('financial_approval', 'Approbation financière'),
            ('dissolution', 'Dissolution'),
            ('other', 'Autre'),
        ],
        string='Catégorie de sujet',
        tracking=True,
    )
    status = fields.Selection(
        selection=[
            ('draft', 'Brouillon'),
            ('proposed', 'Proposé'),
            ('adopted', 'Adopté'),
            ('rejected', 'Rejeté'),
            ('superseded', 'Remplacé'),
        ],
        string='Statut',
        default='draft',
        required=True,
        tracking=True,
        index=True,
    )
    meeting_date = fields.Date(
        string='Date de la séance',
        required=True,
        tracking=True,
    )
    whereas_text = fields.Html(
        string='ATTENDU QUE...',
        help='Préambule / clauses « attendu que »',
    )
    resolved_text = fields.Html(
        string='IL EST RÉSOLU QUE...',
        help='Corps de la résolution',
    )
    mover_id = fields.Many2one(
        'res.partner',
        string='Proposeur',
        tracking=True,
    )
    seconder_id = fields.Many2one(
        'res.partner',
        string='Secondeur',
        tracking=True,
    )
    vote_for = fields.Integer(string='Votes pour')
    vote_against = fields.Integer(string='Votes contre')
    vote_abstain = fields.Integer(string='Abstentions')
    unanimously_adopted = fields.Boolean(
        string='Adopté à l\'unanimité',
        tracking=True,
    )
    effective_date = fields.Date(
        string="Date d'entrée en vigueur",
        tracking=True,
    )
    superseded_by_id = fields.Many2one(
        'corporate.resolution',
        string='Remplacé par',
    )
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'corporate_resolution_attachment_rel',
        'resolution_id',
        'attachment_id',
        string='Pièces jointes',
    )
    document_ids = fields.Many2many(
        'project.document',
        'corporate_resolution_document_rel',
        'resolution_id',
        'document_id',
        string='Documents du livre des minutes',
    )
    company_id = fields.Many2one(
        'res.company',
        string='Société',
        required=True,
        default=lambda self: self.env.company,
    )
    signatory_ids = fields.One2many(
        'corporate.resolution.signatory',
        'resolution_id',
        string='Signataires',
        # Un One2many ne se copie PAS par défaut, mais le texte de la
        # résolution et son proposeur, eux, se copient. Une résolution
        # dupliquée repartirait donc avec son dispositif intact et un bloc de
        # signature vide, qui se rabattrait en silence sur le seul proposeur :
        # exactement le document faux que cette fiche corrige.
        copy=True,
        help="Les personnes qui signent la résolution, avec la qualité en "
             "laquelle elles la signent. Laissé vide, seule une résolution du "
             "conseil se déduit — de ses administrateurs en poste à la date "
             "de la séance.",
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
        3. sinon — résolution des actionnaires — le proposeur et le secondeur,
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
                'capacity': _('Administrateur'),
                'purpose': False,
            } for administrateur in self._get_directors_at_date()]
        return [{
            'name': partenaire.name,
            'capacity': False,
            'purpose': False,
        } for partenaire in (self.mover_id | self.seconder_id)]

    def _get_resolution_type_label(self):
        """Retourner le titre d'en-tête français selon le type de résolution."""
        if self.resolution_type in ('board', 'written_board'):
            return "Résolution écrite des administrateurs"
        return "Résolution écrite des actionnaires"
