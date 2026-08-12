from odoo import fields, models


class KnowledgeItem(models.Model):
    """Extension de project.knowledge.item pour le lien bidirectionnel avec les meetings."""
    _inherit = 'project.knowledge.item'

    meeting_ids = fields.Many2many(
        'meeting.record',
        'meeting_record_knowledge_item_rel',
        'knowledge_item_id',
        'meeting_id',
        string='Comptes rendus',
    )
