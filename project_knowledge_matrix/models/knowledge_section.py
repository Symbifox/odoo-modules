from odoo import fields, models


class KnowledgeSection(models.Model):
    """Reference data for knowledge matrix sections.

    Sections categorize knowledge items (e.g., A - Objectives, B - Roles, etc.)
    Pre-populated with standard sections but can be extended.
    """
    _name = 'project.knowledge.section'
    _description = 'Knowledge Matrix Section'
    _order = 'sequence, id'

    name = fields.Char(
        string='Section Name',
        required=True,
        translate=True,
        help='Display name for this section'
    )
    code = fields.Char(
        string='Code',
        required=True,
        index=True,
        help='Short code (e.g., A, B, IN, V)'
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Order in which sections are displayed'
    )
    description = fields.Text(
        string='Description',
        translate=True,
        help='Detailed description of what this section covers'
    )
    color = fields.Integer(
        string='Color Index',
        help='Color used in kanban views'
    )
    active = fields.Boolean(
        default=True,
        help='Inactive sections are hidden from selection'
    )

    _sql_constraints = [
        ('code_uniq', 'UNIQUE(code)', 'Section code must be unique!')
    ]

    def name_get(self):
        """Display code + name in dropdowns."""
        return [(rec.id, f"[{rec.code}] {rec.name}") for rec in self]
