"""Le modèle `bf.training.assignment` passe au socle `bf_training`.

Rien à migrer côté données : le modèle était vide sur les trois locataires au
moment du déplacement. Ce qui reste à défaire, ce sont les contraintes que
l'ancienne définition avait posées et qu'Odoo ne retire pas tout seul.

🔴 Une colonne d'un champ supprimé ne disparaît pas, et **sa contrainte NOT NULL
non plus**. `channel_id` était obligatoire ; il devient facultatif, parce qu'une
assignation du registre peut viser une activité qui n'a pas de cours en ligne.
Sans ce coup de barre, toute création sans cours échouerait, et l'erreur
parlerait d'une colonne que plus aucun champ ne déclare.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bf_training_assignment' AND column_name = 'channel_id'
    """)
    if cr.fetchone():
        cr.execute("ALTER TABLE bf_training_assignment "
                   "ALTER COLUMN channel_id DROP NOT NULL")
        _logger.info("bf_security_awareness : channel_id rendu facultatif.")

    # `assigned_by` devient `assigned_by_id` dans le socle. Le modèle était vide,
    # mais on reporte quand même : une base qui aurait servi entre-temps ne doit
    # rien perdre.
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bf_training_assignment' AND column_name = 'assigned_by'
    """)
    if cr.fetchone():
        cr.execute("""
            ALTER TABLE bf_training_assignment
            ADD COLUMN IF NOT EXISTS assigned_by_id integer
        """)
        cr.execute("""
            UPDATE bf_training_assignment
            SET assigned_by_id = assigned_by
            WHERE assigned_by_id IS NULL AND assigned_by IS NOT NULL
        """)
        reportes = cr.rowcount
        cr.execute("ALTER TABLE bf_training_assignment DROP COLUMN assigned_by")
        _logger.info("bf_security_awareness : %s assignations reportées de "
                     "assigned_by vers assigned_by_id.", reportes)

    # L'ancien état `to_invite` devient `pending`, `completed` devient `done`,
    # et `overdue` n'est plus un état : le retard se lit sur l'échéance.
    cr.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bf_training_assignment' AND column_name = 'state'
    """)
    if cr.fetchone():
        for ancien, nouveau in (("to_invite", "pending"),
                                ("completed", "done"),
                                ("overdue", "pending")):
            cr.execute("UPDATE bf_training_assignment SET state = %s WHERE state = %s",
                       (nouveau, ancien))
            if cr.rowcount:
                _logger.info("bf_security_awareness : %s assignations passées de "
                             "%s à %s.", cr.rowcount, ancien, nouveau)
