"""18.0.11.54.1 : l'arriéré des copies « Sent » ne déferle pas dans la boîte.

Jusqu'à la 18.0.11.54.0, une copie du dossier « Sent » naissait non traitée et
n'entrait jamais en boîte. Depuis, elle y entre comme un envoi fait depuis
Odoo. Une montée depuis une version antérieure versait donc d'un coup dans la
boîte, le badge et le téléphone toutes les copies jamais traitées depuis le
branchement du compte (sur une base réelle, des dizaines de copies, dont la
plus vieille avait dix mois).

Elles sont marquées traitées : la boîte reste celle d'avant la montée. Rien ne
part au serveur IMAP, et « Relance à faire » ne change pas (c'est le geste qui
compte, pas l'état : voir `bf_email_awaiting`).

Une base déjà en 18.0.11.54.0 n'est pas touchée : ses copies en boîte y sont
entrées par la règle, après la pose.

SQL : l'ORM passerait par `write`, qui notifie la boîte de chaque titulaire.
"""
import logging

from odoo.tools import parse_version

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version or parse_version(version) >= parse_version("18.0.11.54.0"):
        return
    # `=ilike` sans joker, comme `_inbox_domain` : le nom exact, à la casse près.
    cr.execute(
        """
        UPDATE bf_email
           SET is_handled = TRUE,
               handled_at = (now() AT TIME ZONE 'UTC')
         WHERE source = 'imap'
           AND imap_folder ILIKE 'Sent'
           AND is_handled IS NOT TRUE
        """)
    _logger.info("bf_email 11.54.1 : %s copie(s) « Sent » d'avant la montée "
                 "marquée(s) traitée(s)", cr.rowcount)
