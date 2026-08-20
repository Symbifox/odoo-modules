import logging
import re

from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError, UserError

_logger = logging.getLogger(__name__)


class KnowledgeItem(models.Model):
    """Élément individuel de connaissance/décision dans une matrice.

    Correspond aux lignes du CSV de la matrice de connaissances, suivant les décisions,
    responsabilités et l'état d'avancement des implantations de projets.
    """
    _name = 'project.knowledge.item'
    _description = 'Élément de matrice de connaissances'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'phase, priority desc, deadline, section_id, sequence, decision_id'

    # Identification
    decision_id = fields.Char(
        string='ID de décision',
        required=True,
        index=True,
        tracking=True,
        help='Identifiant unique (ex. : A1, B6, IN55)',
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
        help='Ordre dans la section',
    )

    # Relations
    matrix_id = fields.Many2one(
        'project.knowledge.matrix',
        string='Matrice',
        required=True,
        ondelete='cascade',
        index=True,
    )
    section_id = fields.Many2one(
        'project.knowledge.section',
        string='Section',
        required=True,
        tracking=True,
        index=True,
    )
    project_id = fields.Many2one(
        related='matrix_id.project_id',
        store=True,
        string='Projet',
        index=True,
    )
    company_id = fields.Many2one(
        related='matrix_id.company_id',
        store=True,
        string='Société',
    )

    # Contenu principal (colonnes CSV)
    name = fields.Char(
        string='Élément de décision',
        required=True,
        tracking=True,
        help="La décision ou l'élément suivi",
    )
    questionnaire_location = fields.Char(
        string='Emplacement questionnaire',
        help="Où c'est décidé dans le questionnaire (ex. : Q1, Q2)",
    )
    phase_coverage = fields.Char(
        string='Couverture de phase',
        help='Phase du projet (ex. : S1, S3-S4)',
    )
    info_provider = fields.Char(
        string="Fournisseur d'information",
        help="Qui fournit l'information",
    )
    required_inputs = fields.Text(
        string='Intrants requis',
        help='Intrants à obtenir pour éviter les reprises',
    )
    deliverable = fields.Char(
        string='Livrable interne',
        help='Livrable que cet élément alimente',
    )
    notes = fields.Text(
        string='Notes',
        help='Notes ou contexte supplémentaire',
    )

    # Classification du type d'élément
    item_type = fields.Selection(
        selection=[
            ('info', 'Information'),
            ('decision', 'Décision'),
            ('content', 'Contenu'),
        ],
        string='Type',
        default='info',
        tracking=True,
        index=True,
    )
    decision_category = fields.Selection(
        selection=[
            ('technical', 'Technique'),
            ('business', 'Affaires'),
            ('financial', 'Financier'),
            ('contractual', 'Contractuel'),
            ('security', 'Sécurité'),
            ('operational', 'Opérationnel'),
            ('organizational', 'Organisationnel'),
        ],
        string='Catégorie de décision',
        tracking=True,
    )
    impact_level = fields.Selection(
        selection=[
            ('low', 'Faible'),
            ('medium', 'Moyen'),
            ('high', 'Élevé'),
        ],
        string="Niveau d'impact",
        tracking=True,
    )
    decision_date = fields.Date(
        string='Date de décision',
        tracking=True,
    )

    # Contenu de la décision
    context_description = fields.Html(
        string='Contexte',
        help='Énoncé du problème / forces en jeu',
    )
    decision_text = fields.Html(
        string='Décision',
        help='Ce qui a été décidé (commence par « Nous allons... »)',
    )
    alternatives_considered = fields.Html(
        string='Alternatives considérées',
        help='Autres options évaluées',
    )
    rationale = fields.Html(
        string='Justification',
        help='Pourquoi cette option a été choisie',
    )
    consequences = fields.Html(
        string='Conséquences',
        help='Impacts positifs et négatifs',
    )

    # Contenu de document (politique, procédure, rapport)
    content_html = fields.Html(
        string='Contenu',
        sanitize_style=True,
        help='Corps du contenu pour les sections de documents (politique, procédure, rapport)',
    )

    # Parties prenantes de la décision
    decision_maker_id = fields.Many2one(
        'res.partner',
        string='Décideur',
        tracking=True,
    )
    stakeholder_consulted_ids = fields.Many2many(
        'res.partner',
        'knowledge_item_consulted_rel',
        'item_id',
        'partner_id',
        string='Consultés',
    )
    stakeholder_informed_ids = fields.Many2many(
        'res.partner',
        'knowledge_item_informed_rel',
        'item_id',
        'partner_id',
        string='Informés',
    )

    # Chaîne de décision
    superseded_by_id = fields.Many2one(
        'project.knowledge.item',
        string='Remplacé par',
    )
    supersedes_id = fields.Many2one(
        'project.knowledge.item',
        string='Remplace',
    )

    # Flux de travail et assignation
    state = fields.Selection(
        selection=[
            ('pending', 'En attente'),
            ('in_progress', 'En cours'),
            ('done', 'Complété'),
            ('na', 'S/O'),
            ('proposed', 'Proposé'),
            ('accepted', 'Accepté'),
            ('rejected', 'Rejeté'),
            ('superseded', 'Remplacé'),
        ],
        string='Statut',
        default='pending',
        required=True,
        tracking=True,
        index=True,
        help='Statut actuel de cet élément',
    )
    assigned_user_id = fields.Many2one(
        'res.users',
        string='Assigné à',
        tracking=True,
        index=True,
        help='Personne responsable de cet élément',
    )
    completion_date = fields.Date(
        string='Date de complétion',
        tracking=True,
        help='Date de marquage comme complété',
    )

    # Planification et priorité
    deadline = fields.Date(
        string='Échéance',
        tracking=True,
        help='Date à laquelle cette information est requise',
    )
    priority = fields.Selection(
        selection=[
            ('0', 'Faible'),
            ('1', 'Moyen'),
            ('2', 'Élevé'),
            ('3', 'Urgent'),
        ],
        string='Priorité',
        default='1',
        tracking=True,
        index=True,
        help='Niveau de priorité pour la collecte de cette information',
    )
    phase = fields.Selection(
        selection=[
            ('1_discovery', 'Découverte'),
            ('2_requirements', 'Requis'),
            ('3_build', 'Construction'),
            ('4_testing', 'Tests'),
            ('5_golive', 'Mise en production'),
        ],
        string='Phase',
        default='1_discovery',
        tracking=True,
        index=True,
        help="Phase d'implantation où cette information est requise",
    )

    # Intégration des tâches
    task_ids = fields.Many2many(
        'project.task',
        'knowledge_item_task_rel',
        'item_id',
        'task_id',
        string='Tâches liées',
        help='Tâches de projet liées à la collecte de cette information',
    )
    task_count = fields.Integer(
        string='Nombre de tâches',
        compute='_compute_task_count',
        store=True,
    )

    # Dépendances
    blocked_by_ids = fields.Many2many(
        'project.knowledge.item',
        'knowledge_item_dependency_rel',
        'item_id',
        'blocked_by_id',
        string='Bloqué par',
        help='Éléments devant être complétés avant celui-ci',
    )
    blocking_ids = fields.Many2many(
        'project.knowledge.item',
        'knowledge_item_dependency_rel',
        'blocked_by_id',
        'item_id',
        string='Bloque',
        help='Éléments qui dépendent de celui-ci',
    )
    is_blocked = fields.Boolean(
        string='Est bloqué',
        compute='_compute_is_blocked',
        store=True,
        help='Vrai si bloqué par des éléments incomplets',
    )
    is_overdue = fields.Boolean(
        string='En retard',
        compute='_compute_is_overdue',
        search='_search_is_overdue',
        help="Vrai si l'échéance est passée et l'élément n'est pas complété",
    )
    followup_activity_created = fields.Boolean(
        string='Activité de suivi créée',
        default=False,
        copy=False,
    )

    # Pièces jointes
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'knowledge_item_attachment_rel',
        'item_id',
        'attachment_id',
        string='Pièces jointes',
        help='Documents justificatifs',
    )
    attachment_count = fields.Integer(
        string='Pièces jointes',
        compute='_compute_attachment_count',
    )

    # Couleur pour kanban
    color = fields.Integer(
        related='section_id.color',
        string='Couleur',
    )

    # Contraintes SQL
    _sql_constraints = [
        ('decision_id_matrix_uniq', 'UNIQUE(decision_id, matrix_id)',
         "L'ID de décision doit être unique au sein d'une matrice !"),
    ]

    @api.depends('attachment_ids')
    def _compute_attachment_count(self):
        for item in self:
            item.attachment_count = len(item.attachment_ids)

    @api.depends('task_ids')
    def _compute_task_count(self):
        for item in self:
            item.task_count = len(item.task_ids)

    @api.depends('blocked_by_ids', 'blocked_by_ids.state')
    def _compute_is_blocked(self):
        for item in self:
            item.is_blocked = any(
                blocker.state not in ('done', 'na')
                for blocker in item.blocked_by_ids
            )

    @api.depends('deadline', 'state')
    def _compute_is_overdue(self):
        today = fields.Date.today()
        for item in self:
            item.is_overdue = (
                item.deadline
                and item.deadline < today
                and item.state not in ('done', 'na', 'accepted', 'rejected', 'superseded')
            )

    def _search_is_overdue(self, operator, value):
        """Permettre la recherche d'éléments en retard."""
        today = fields.Date.today()
        terminal_states = ['done', 'na', 'accepted', 'rejected', 'superseded']
        if (operator == '=' and value) or (operator == '!=' and not value):
            return [
                ('deadline', '<', today),
                ('state', 'not in', terminal_states)
            ]
        return [
            '|',
            ('deadline', '>=', today),
            ('deadline', '=', False),
            ('state', 'in', terminal_states)
        ]

    # === Méthodes d'action ===
    def action_start(self):
        """Passer l'élément à l'état En cours."""
        self.ensure_one()
        self.write({'state': 'in_progress'})

    def action_done(self):
        """Marquer l'élément comme complété."""
        self.ensure_one()
        self.write({
            'state': 'done',
            'completion_date': fields.Date.today()
        })

    def action_reset(self):
        """Réinitialiser l'élément à l'état En attente."""
        self.ensure_one()
        self.write({
            'state': 'pending',
            'completion_date': False
        })

    def action_toggle_na(self):
        """Basculer le statut S/O."""
        self.ensure_one()
        if self.state == 'na':
            self.write({'state': 'pending'})
        else:
            self.write({'state': 'na'})

    def action_propose(self):
        """Passer la décision au statut proposé."""
        self.ensure_one()
        self.write({'state': 'proposed'})

    def action_accept(self):
        """Accepter la décision, définir la date de décision si vide."""
        self.ensure_one()
        vals = {'state': 'accepted'}
        if not self.decision_date:
            vals['decision_date'] = fields.Date.today()
        self.write(vals)

    def action_reject(self):
        """Rejeter la décision."""
        self.ensure_one()
        self.write({'state': 'rejected'})

    def _next_free_decision_id(self):
        """Prochain identifiant libre du même préfixe, dans la même matrice.

        ``decision_id`` est unique par matrice et doit rester au format
        « lettres puis chiffres ». Un successeur ne peut donc pas reprendre
        l'identifiant de son prédécesseur ni lui accoler un suffixe : on garde
        le préfixe de section et on prend le premier numéro encore libre.
        """
        self.ensure_one()
        correspondance = re.match(r'^([A-Za-z]+)(\d+)$', self.decision_id or '')
        if not correspondance:
            return self.decision_id
        prefixe = correspondance.group(1).upper()

        voisins = self.search([
            ('matrix_id', '=', self.matrix_id.id),
            ('decision_id', '=like', f'{prefixe}%'),
        ])
        pris = set()
        for voisin in voisins:
            autre = re.match(r'^([A-Za-z]+)(\d+)$', voisin.decision_id or '')
            if autre and autre.group(1).upper() == prefixe:
                pris.add(int(autre.group(2)))

        numero = max(pris) + 1 if pris else 1
        while f'{prefixe}{numero}' in {v.decision_id for v in voisins}:
            numero += 1
        return f'{prefixe}{numero}'

    def action_supersede(self):
        """Marquer la décision actuelle comme remplacée et créer un successeur.

        Le successeur reçoit un identifiant libre : la copie telle quelle
        violait la contrainte d'unicité ``decision_id`` + ``matrix_id`` et le
        bouton levait une erreur de base de données à chaque appel.
        """
        self.ensure_one()
        new_item = self.copy({
            'name': f"[RÉV] {self.name}",
            'decision_id': self._next_free_decision_id(),
            'state': 'pending',
            'supersedes_id': self.id,
            'decision_date': False,
            'completion_date': False,
        })
        self.write({
            'state': 'superseded',
            'superseded_by_id': new_item.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Nouvelle décision',
            'res_model': 'project.knowledge.item',
            'views': [[False, 'form']],
            'res_id': new_item.id,
            'target': 'current',
        }

    def action_assign_to_me(self):
        """Assigner cet élément à l'utilisateur courant."""
        self.ensure_one()
        self.write({'assigned_user_id': self.env.uid})

    def action_open_attachments(self):
        """Ouvrir la vue des pièces jointes pour cet élément."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Pièces jointes',
            'res_model': 'ir.attachment',
            'views': [[False, 'kanban'], [False, 'list'], [False, 'form']],
            'domain': [('id', 'in', self.attachment_ids.ids)],
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
            },
        }

    def action_create_task(self):
        """Créer une tâche de projet liée à cet élément de connaissance."""
        self.ensure_one()
        if not self.project_id:
            raise UserError(
                "Impossible de créer une tâche : la matrice de cet élément n'est pas liée à un projet."
            )

        # Construire la description de la tâche
        description_parts = []
        if self.info_provider:
            description_parts.append(f"**Fournisseur d'information :** {self.info_provider}")
        if self.required_inputs:
            description_parts.append(f"**Intrants requis :**\n{self.required_inputs}")
        if self.deliverable:
            description_parts.append(f"**Livrable :** {self.deliverable}")
        if self.notes:
            description_parts.append(f"**Notes :**\n{self.notes}")

        description = "\n\n".join(description_parts)

        # Créer la tâche
        task = self.env['project.task'].create({
            'name': f"[{self.section_id.code}] {self.decision_id}: {self.name}",
            'project_id': self.project_id.id,
            'user_ids': [(6, 0, [self.assigned_user_id.id])] if self.assigned_user_id else [],
            'date_deadline': self.deadline,
            'description': description,
            'priority': '1' if self.priority in ('2', '3') else '0',
        })

        # Lier la tâche à cet élément
        self.write({'task_ids': [(4, task.id)]})

        # Démarrer l'élément s'il est en attente
        if self.state == 'pending':
            self.write({'state': 'in_progress'})

        # Retourner l'action pour voir la nouvelle tâche
        return {
            'type': 'ir.actions.act_window',
            'name': 'Tâche',
            'res_model': 'project.task',
            'views': [[False, 'form']],
            'res_id': task.id,
            'target': 'current',
        }

    def action_view_tasks(self):
        """Voir toutes les tâches liées à cet élément."""
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': f'Tâches : {self.decision_id}',
            'res_model': 'project.task',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('id', 'in', self.task_ids.ids)],
            'context': {'default_project_id': self.project_id.id},
        }
        if len(self.task_ids) == 1:
            action['views'] = [[False, 'form']]
            action['res_id'] = self.task_ids.id
        return action

    @api.constrains('decision_id')
    def _check_decision_id_format(self):
        """Valider le format de l'ID de décision (lettres + chiffres)."""
        pattern = re.compile(r'^[A-Z]+\d+$', re.IGNORECASE)
        for item in self:
            if item.decision_id and not pattern.match(item.decision_id):
                raise ValidationError(
                    f"L'ID de décision « {item.decision_id} » doit être au format "
                    "lettres suivies de chiffres (ex. : « A1 », « B6 », « IN55 »)."
                )

    @api.model_create_multi
    def create(self, vals_list):
        """Mettre en majuscule l'ID de décision et définir les valeurs par défaut du contexte."""
        for vals in vals_list:
            if vals.get('decision_id'):
                vals['decision_id'] = vals['decision_id'].upper()
            if not vals.get('matrix_id') and self.env.context.get('default_matrix_id'):
                vals['matrix_id'] = self.env.context['default_matrix_id']
            # Auto-assign matrix when created inline from project form
            if not vals.get('matrix_id'):
                project_id = vals.get('project_id') or self.env.context.get('default_project_id')
                if project_id:
                    matrix = self.env['project.knowledge.matrix'].search([
                        ('project_id', '=', project_id),
                        ('is_template', '=', False),
                        ('active', '=', True),
                    ], limit=1, order='id asc')
                    if matrix:
                        vals['matrix_id'] = matrix.id
        records = super().create(vals_list)
        # Capture depuis le chatter d'une tâche : poster le courriel source (corps +
        # pièces jointes, dont le .eml) en note interne plutôt que dans la description.
        capture_message_id = self.env.context.get('capture_message_id')
        if capture_message_id:
            self._post_captured_message(records, capture_message_id)
        return records

    def _post_captured_message(self, records, message_id):
        """Poste le message source en note interne sur les éléments capturés.

        Copie les pièces jointes du message (pour ne pas dépointer celles de
        l'original) et publie le tout en ``mail.mt_note`` — aucune notification.

        Le message source est lu SANS ``sudo`` : l'ACL de l'utilisateur courant
        s'applique, sinon n'importe quel utilisateur pourrait exfiltrer un
        ``mail.message`` arbitraire via la clé de contexte ``capture_message_id``.
        """
        src = self.env['mail.message'].browse(message_id)
        if not src.exists():
            return
        try:
            src.check_access('read')
        except AccessError:
            return
        for rec in records:
            att_ids = [
                att.copy({
                    'res_model': rec._name,
                    'res_id': rec.id,
                    'res_field': False,
                }).id
                for att in src.attachment_ids
            ]
            rec.message_post(
                body=Markup(src.body) if src.body else Markup(''),
                subject=src.subject or False,
                author_id=src.author_id.id or False,
                email_from=src.email_from or False,
                message_type='comment',
                subtype_xmlid='mail.mt_note',
                attachment_ids=att_ids,
            )

    def write(self, vals):
        """Mettre en majuscule l'ID de décision à l'écriture.

        Lors d'un changement de statut, réinitialiser le drapeau de suivi
        et fermer les activités de suivi ouvertes.
        """
        if vals.get('decision_id'):
            vals['decision_id'] = vals['decision_id'].upper()
        if 'state' in vals:
            vals['followup_activity_created'] = False
            activity_type_xmlids = [
                'project_knowledge_matrix.mail_activity_type_knowledge_deadline',
                'project_knowledge_matrix.mail_activity_type_knowledge_overdue',
                'project_knowledge_matrix.mail_activity_type_knowledge_stale',
            ]
            activity_type_ids = []
            for xmlid in activity_type_xmlids:
                act_type = self.env.ref(xmlid, raise_if_not_found=False)
                if act_type:
                    activity_type_ids.append(act_type.id)
            if activity_type_ids:
                activities = self.env['mail.activity'].sudo().search([
                    ('res_model', '=', self._name),
                    ('res_id', 'in', self.ids),
                    ('activity_type_id', 'in', activity_type_ids),
                ])
                if activities:
                    activities.action_done()
        return super().write(vals)

    # === Cron : activités de suivi automatiques ===

    @api.model
    def _cron_create_followup_activities(self):
        """Créer des activités de suivi pour les éléments de matrice.

        3 passes :
        1. Échéance ≤7 jours (pas encore notifié)
        2. En retard (doublon évité)
        3. Sans progression >30 jours (doublon évité)
        """
        today = fields.Date.today()
        deadline_type = self.env.ref(
            'project_knowledge_matrix.mail_activity_type_knowledge_deadline',
            raise_if_not_found=False,
        )
        overdue_type = self.env.ref(
            'project_knowledge_matrix.mail_activity_type_knowledge_overdue',
            raise_if_not_found=False,
        )
        stale_type = self.env.ref(
            'project_knowledge_matrix.mail_activity_type_knowledge_stale',
            raise_if_not_found=False,
        )
        if not (deadline_type and overdue_type and stale_type):
            _logger.warning("Types d'activités de suivi matrice introuvables, cron ignoré.")
            return

        active_states = ('pending', 'in_progress')

        # --- Pass 1 : Échéance qui approche (≤7 jours) ---
        horizon = today + timedelta(days=7)
        approaching = self.search([
            ('state', 'in', active_states),
            ('deadline', '>=', today),
            ('deadline', '<=', horizon),
            ('assigned_user_id', '!=', False),
            ('followup_activity_created', '=', False),
        ])
        for item in approaching:
            item.activity_schedule(
                'project_knowledge_matrix.mail_activity_type_knowledge_deadline',
                date_deadline=item.deadline,
                user_id=item.assigned_user_id.id,
                note=f"{item.decision_id} : {item.name} — échéance le {item.deadline}",
            )
        if approaching:
            approaching.write({'followup_activity_created': True})
            _logger.info("Activités échéance créées pour %d éléments.", len(approaching))

        # --- Pass 2 : En retard (deadline dépassée) ---
        overdue = self.search([
            ('state', 'in', active_states),
            ('deadline', '<', today),
            ('assigned_user_id', '!=', False),
        ])
        if overdue:
            existing_overdue = self.env['mail.activity'].sudo().search([
                ('res_model', '=', self._name),
                ('res_id', 'in', overdue.ids),
                ('activity_type_id', '=', overdue_type.id),
            ])
            already_notified = set(existing_overdue.mapped('res_id'))
            count = 0
            for item in overdue:
                if item.id not in already_notified:
                    item.activity_schedule(
                        'project_knowledge_matrix.mail_activity_type_knowledge_overdue',
                        date_deadline=today,
                        user_id=item.assigned_user_id.id,
                        note=f"{item.decision_id} : {item.name} — en retard depuis le {item.deadline}",
                    )
                    count += 1
            if count:
                _logger.info("Activités retard créées pour %d éléments.", count)

        # --- Pass 3 : Sans progression (>30 jours) ---
        stale = self.search([
            ('state', '=', 'in_progress'),
            ('write_date', '<', fields.Datetime.to_string(
                fields.Datetime.now() - timedelta(days=30)
            )),
            ('assigned_user_id', '!=', False),
        ])
        if stale:
            existing_stale = self.env['mail.activity'].sudo().search([
                ('res_model', '=', self._name),
                ('res_id', 'in', stale.ids),
                ('activity_type_id', '=', stale_type.id),
            ])
            already_notified = set(existing_stale.mapped('res_id'))
            count = 0
            for item in stale:
                if item.id not in already_notified:
                    item.activity_schedule(
                        'project_knowledge_matrix.mail_activity_type_knowledge_stale',
                        date_deadline=today + timedelta(days=3),
                        user_id=item.assigned_user_id.id,
                        note=f"{item.decision_id} : {item.name} — aucune mise à jour depuis >30 jours",
                    )
                    count += 1
            if count:
                _logger.info("Activités stagnation créées pour %d éléments.", count)
