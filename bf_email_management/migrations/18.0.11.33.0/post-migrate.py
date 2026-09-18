"""18.0.11.33.0 — recalcul des signaux après le changement de fuseau.

``is_late_night`` se lisait dans le fuseau du PROPRIÉTAIRE et se lit
désormais dans celui de l'EXPÉDITEUR. ``is_invitation`` naît avec
cette version. ``is_bulk`` et la catégorie profitent du motif de robots élargi.

⚠️ On ne force PAS le recalcul de ``category`` : c'est un calculé stocké
``readonly=False``, donc une valeur posée à la main ou par une règle y tient,
et un recalcul l'écraserait. Seuls les signaux en lecture seule sont rejoués.
"""
import logging

_logger = logging.getLogger(__name__)

LOT = 500


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    BfEmail = env["bf.email"]
    champ = BfEmail._fields["is_late_night"]

    cr.execute("SELECT id FROM bf_email ORDER BY id")
    ids = [row[0] for row in cr.fetchall()]
    _logger.info("bf_email 11.33.0 : recalcul des signaux sur %s lignes", len(ids))

    avant = 0
    cr.execute("SELECT COUNT(*) FROM bf_email WHERE is_late_night IS TRUE")
    avant = cr.fetchone()[0]

    for debut in range(0, len(ids), LOT):
        lot = BfEmail.browse(ids[debut:debut + LOT])
        env.add_to_compute(champ, lot)
        lot.flush_recordset()
        env.invalidate_all()

    cr.execute("SELECT COUNT(*) FROM bf_email WHERE is_late_night IS TRUE")
    apres = cr.fetchone()[0]
    cr.execute("SELECT COUNT(*) FROM bf_email WHERE is_invitation IS TRUE")
    invitations = cr.fetchone()[0]
    _logger.info(
        "bf_email 11.33.0 : « hors heures » passe de %s à %s lignes ; "
        "%s invitations reconnues",
        avant, apres, invitations,
    )
