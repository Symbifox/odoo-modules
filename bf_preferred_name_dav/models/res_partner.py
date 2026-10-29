from odoo import models

from .dav_carddav import PRONOUN_FIELDS

# Les champs que `bf_dav_server` surveille déjà : quand l'un d'eux est écrit
# avec les pronoms, il journalise lui-même.
from odoo.addons.bf_dav_server.models.dav_addressbook import CARD_FIELDS


class ResPartner(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        # `bf_dav_server` ne journalise que SES champs : changer les pronoms ne
        # signalait rien aux téléphones en synchronisation.
        mine = set(PRONOUN_FIELDS) & set(vals) and not CARD_FIELDS & set(vals)
        before = self._bf_dav_rows() if mine else []
        res = super().write(vals)
        if mine:
            self.env["bf.dav.change"]._record_many(before)
            self._bf_dav_journal()
        return res
