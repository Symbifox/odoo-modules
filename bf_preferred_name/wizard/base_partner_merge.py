"""La fusion de contacts garde le nom légal de la fiche qui disparaît.

La fusion d'Odoo recopie vers la fiche gardée les champs que la fusionneuse
peut LIRE (`fields_get`) : hors du groupe « Noms légaux », `bf_legal_name` n'en
est pas, et le nom légal de la fiche fusionnée partait avec elle, sans avis.

Ici, en sudo et sans suivi (le champ ne l'est jamais) : le nom légal d'une
fiche fusionnée passe à la fiche gardée quand elle n'en a pas. Rien n'est
écrasé : quand les noms légaux diffèrent, la fiche gardée reste telle quelle
et elle est signalée au journal par son seul numéro, jamais par les noms.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class BasePartnerMergeAutomaticWizard(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _update_values(self, src_partners, dst_partner):
        legal = {p.id: p.bf_legal_name for p in (src_partners | dst_partner).sudo()}
        super()._update_values(src_partners, dst_partner)
        kept = legal[dst_partner.id]
        merged = {legal[p.id] for p in src_partners if legal[p.id]}
        if not kept and len(merged) == 1:
            dst_partner.sudo().bf_legal_name = merged.pop()
        elif merged - {kept}:
            _logger.warning(
                "Partner %s: the merged contacts carried different personal legal names; "
                "none was overwritten, to be settled by hand.", dst_partner.id)
