"""18.0.5.27.4 : rattacher les fils restés sans contact.

Jusqu'ici, l'appariement automatique cherchait les dix chiffres du numéro dans
la fiche telle qu'on l'a tapée (« 418 555-0101 ») et ne la trouvait jamais.
Les fils ouverts depuis sont restés sans contact, leurs appels invisibles depuis
la fiche. On les reprend avec la nouvelle règle, qui ne rattache que si UN
contact porte le numéro (ou l'entreprise dont c'est le standard). Un fil déjà
rattaché n'est jamais touché, et le nom déjà connu d'un fil (venu du téléphone)
est gardé : l'écriture de partner_id le remplacerait par celui de la fiche.
"""

import logging

from odoo import SUPERUSER_ID, api
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {"active_test": False})
    Thread = env["sms.archive.thread"]
    lies = 0
    for thread in Thread.search([("partner_id", "=", False), ("phone_normalized", "!=", False)]):
        # La fiche doit être lisible par le PROPRIÉTAIRE du fil : sinon sa
        # Messagerie tomberait en lisant le nom du contact.
        juge = Thread.with_user(thread.owner_id) if thread.owner_id else Thread
        try:
            partner = juge._match_partner(thread.phone_normalized)
        except AccessError:
            partner = False
        if partner:
            nom = thread.contact_name
            thread.partner_id = partner.id  # écrit en superutilisateur (env de la montée)
            if nom and thread.contact_name != nom:
                # Par l'ORM : un UPDATE direct serait écrasé par l'écriture du nom
                # de la fiche, encore en attente dans le cache.
                thread.contact_name = nom
            lies += 1
    _logger.info("bf_sms_archive 18.0.5.27.4 : %s fil(s) rattaché(s) à leur contact", lies)
