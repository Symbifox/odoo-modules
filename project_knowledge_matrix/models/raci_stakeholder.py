from odoo import api, fields, models, tools


class RaciStakeholder(models.Model):
    """Vue SQL agrégée des rôles RACI par partie prenante et projet.

    Construit à partir de 4 UNION ALL sur les champs de knowledge.item :
    - R (Responsable) : assigned_user_id
    - A (Décideur) : decision_maker_id
    - C (Consulté) : stakeholder_consulted_ids
    - I (Informé) : stakeholder_informed_ids
    """
    _name = 'knowledge.raci.stakeholder'
    _description = 'Résumé RACI des parties prenantes'
    _auto = False
    _order = 'partner_name, project_name, role'

    partner_id = fields.Many2one('res.partner', string='Partie prenante', readonly=True)
    partner_name = fields.Char(string='Nom', readonly=True)
    project_id = fields.Many2one('project.project', string='Projet', readonly=True)
    project_name = fields.Char(string='Projet', readonly=True)
    role = fields.Selection([
        ('R', 'Responsable'),
        ('A', 'Décideur'),
        ('C', 'Consulté'),
        ('I', 'Informé'),
    ], string='Rôle', readonly=True)
    item_count = fields.Integer(string="Nombre d'éléments", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                -- R: Responsable (assigned_user_id → partner)
                SELECT
                    ROW_NUMBER() OVER () AS id,
                    sub.partner_id,
                    sub.partner_name,
                    sub.project_id,
                    sub.project_name,
                    sub.role,
                    sub.item_count
                FROM (
                    SELECT
                        rp.id AS partner_id,
                        rp.name AS partner_name,
                        pp.id AS project_id,
                        pp.name AS project_name,
                        'R' AS role,
                        COUNT(*) AS item_count
                    FROM project_knowledge_item ki
                    JOIN project_knowledge_matrix km ON km.id = ki.matrix_id
                    JOIN res_users ru ON ru.id = ki.assigned_user_id
                    JOIN res_partner rp ON rp.id = ru.partner_id
                    JOIN project_project pp ON pp.id = km.project_id
                    WHERE km.is_template = false
                    GROUP BY rp.id, rp.name, pp.id, pp.name

                    UNION ALL

                    -- A: Décideur (decision_maker_id → partner direct)
                    SELECT
                        rp.id AS partner_id,
                        rp.name AS partner_name,
                        pp.id AS project_id,
                        pp.name AS project_name,
                        'A' AS role,
                        COUNT(*) AS item_count
                    FROM project_knowledge_item ki
                    JOIN project_knowledge_matrix km ON km.id = ki.matrix_id
                    JOIN res_partner rp ON rp.id = ki.decision_maker_id
                    JOIN project_project pp ON pp.id = km.project_id
                    WHERE km.is_template = false
                    GROUP BY rp.id, rp.name, pp.id, pp.name

                    UNION ALL

                    -- C: Consulté (M2M stakeholder_consulted_ids)
                    SELECT
                        rp.id AS partner_id,
                        rp.name AS partner_name,
                        pp.id AS project_id,
                        pp.name AS project_name,
                        'C' AS role,
                        COUNT(*) AS item_count
                    FROM knowledge_item_consulted_rel rel
                    JOIN project_knowledge_item ki ON ki.id = rel.item_id
                    JOIN project_knowledge_matrix km ON km.id = ki.matrix_id
                    JOIN res_partner rp ON rp.id = rel.partner_id
                    JOIN project_project pp ON pp.id = km.project_id
                    WHERE km.is_template = false
                    GROUP BY rp.id, rp.name, pp.id, pp.name

                    UNION ALL

                    -- I: Informé (M2M stakeholder_informed_ids)
                    SELECT
                        rp.id AS partner_id,
                        rp.name AS partner_name,
                        pp.id AS project_id,
                        pp.name AS project_name,
                        'I' AS role,
                        COUNT(*) AS item_count
                    FROM knowledge_item_informed_rel rel
                    JOIN project_knowledge_item ki ON ki.id = rel.item_id
                    JOIN project_knowledge_matrix km ON km.id = ki.matrix_id
                    JOIN res_partner rp ON rp.id = rel.partner_id
                    JOIN project_project pp ON pp.id = km.project_id
                    WHERE km.is_template = false
                    GROUP BY rp.id, rp.name, pp.id, pp.name
                ) sub
            )
        """ % self._table)
