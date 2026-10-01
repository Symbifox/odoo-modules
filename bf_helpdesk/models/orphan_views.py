"""Vues orphelines de l'assistance : des copies sans xmlid qui masquent les vraies.

Constat sur une base réelle : des vues helpdesk.* sans xmlid, laissées par
une installation antérieure de helpdesk_mgmt dont les xmlid ont été perdus. Même nom, même priorité, identifiant plus petit : Odoo les
choisit avant les vraies, et aucune extension (onglets Triage IA, Client 360,
Articles, Feuilles de temps, Incident ; réglages IA et relances de l'équipe)
n'apparaissait à l'écran.

On les archive (active = false, réversible), sans rien supprimer : une vue sans
xmlid dont un modèle helpdesk porte une jumelle AVEC xmlid (même modèle, même
type, même nom, même mode), et les extensions sans xmlid qui en héritent.
"""
import logging

_logger = logging.getLogger(__name__)


def archive_orphan_helpdesk_views(cr):
    cr.execute("""
        SELECT v.id FROM ir_ui_view v
         WHERE v.active AND v.model LIKE 'helpdesk.%%'
           AND NOT EXISTS (SELECT 1 FROM ir_model_data d
                            WHERE d.model = 'ir.ui.view' AND d.res_id = v.id)
           AND EXISTS (SELECT 1 FROM ir_ui_view w
                         JOIN ir_model_data dw ON dw.model = 'ir.ui.view' AND dw.res_id = w.id
                        WHERE w.id != v.id AND w.model = v.model AND w.type = v.type
                          AND w.name = v.name AND w.mode = v.mode)
    """)
    orphans = [r[0] for r in cr.fetchall()]
    if not orphans:
        return []
    cr.execute("""
        WITH RECURSIVE arbre(id) AS (
            SELECT unnest(%s::int[])
            UNION
            SELECT v.id FROM ir_ui_view v JOIN arbre a ON v.inherit_id = a.id
             WHERE NOT EXISTS (SELECT 1 FROM ir_model_data d
                                WHERE d.model = 'ir.ui.view' AND d.res_id = v.id)
        )
        UPDATE ir_ui_view SET active = false
         WHERE id IN (SELECT id FROM arbre) AND active
     RETURNING id
    """, (orphans,))
    archived = sorted(r[0] for r in cr.fetchall())
    _logger.warning("bf_helpdesk : %s vue(s) orpheline(s) archivée(s) : %s",
                    len(archived), archived)
    return archived
