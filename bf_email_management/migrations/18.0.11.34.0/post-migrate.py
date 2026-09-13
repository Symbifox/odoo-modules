"""18.0.11.34.0 — préparer le repliage en fils et le désabonnement.

Deux rattrapages, tous deux en SQL parce qu'ils portent sur des colonnes
simples et que l'ORM n'y ajouterait rien.

⚠️ `thread_root_id` vide fait tomber toutes les lignes concernées dans le MÊME
groupe au moment de replier : elles s'afficheraient comme un seul fil de
trente-sept messages sans rapport entre eux. Le Message-ID d'une ligne est sa
propre racine quand elle n'en a pas d'autre, ce que l'ingestion fait déjà
depuis longtemps ; seules les lignes anciennes en manquent.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE bf_email
           SET thread_root_id = message_id_header
         WHERE thread_root_id IS NULL
           AND message_id_header IS NOT NULL
        """)
    _logger.info("bf_email 11.34.0 : %s racines de fil reconstituées",
                 cr.rowcount)

    # Les champs de désabonnement sont calculés STOCKÉS : Odoo les calcule à
    # l'ajout de la colonne, donc rien à faire ici. On se contente de dire ce
    # que la base contient, parce que c'est le chiffre qui décidera si le
    # panneau des abonnements vaut la peine sur cette instance.
    cr.execute("SELECT COUNT(*) FROM bf_email WHERE unsubscribe_url IS NOT NULL")
    avec_lien = cr.fetchone()[0]
    cr.execute(
        "SELECT COUNT(*) FROM bf_email WHERE unsubscribe_one_click IS TRUE")
    un_clic = cr.fetchone()[0]
    _logger.info(
        "bf_email 11.34.0 : %s lignes avec un lien de désabonnement, "
        "%s en un clic", avec_lien, un_clic)
