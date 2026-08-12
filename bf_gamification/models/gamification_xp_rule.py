from odoo import fields, models


class GamificationXpRule(models.Model):
    _name = 'bf.gamification.xp.rule'
    _description = 'Règle XP'
    _order = 'source, name'

    name = fields.Char(string="Nom", required=True)
    source = fields.Selection([
        ('timesheet', 'Feuille de temps'),
        ('task', 'Tâche'),
        ('document', 'Document'),
        ('hosting', 'Hébergement'),
        ('streak', 'Streak'),
        ('message', 'Message'),
        ('helpdesk', 'Ticket'),
        ('activity', 'Activité'),
        ('meeting', 'Compte rendu'),
        ('agenda', 'Ordre du jour'),
        ('decision', 'Décision'),
        ('knowledge_item', 'Élément de matrice'),
        ('resolution', 'Résolution corporative'),
        ('email_triage', 'Triage courriel'),
    ], string="Source", required=True)
    trigger = fields.Selection([
        ('create', 'Création'),
        ('write', 'Modification'),
        ('complete', 'Complétion'),
        ('daily', 'Quotidien'),
    ], string="Déclencheur", required=True, default='create')
    xp_amount = fields.Integer(string="XP accordé", required=True, default=1)
    condition_description = fields.Char(string="Condition")
    active = fields.Boolean(string="Actif", default=True)
    model_id = fields.Many2one('ir.model', string="Modèle")
    min_value = fields.Float(string="Valeur minimum")
