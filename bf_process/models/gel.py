# -*- coding: utf-8 -*-
"""Gel d'une version validée.

Une cartographie validée est une pièce datée : on la cite, on la transmet, on
s'appuie dessus. La modifier après coup, c'est réécrire l'histoire sans que
personne ne le voie. Une version validée devient donc non modifiable, et le
travail se poursuit sur la suivante.

Le gel n'est pas une porte fermée à clé : `action_rouvrir` existe, et le
chatter garde la trace de qui a rouvert quoi.
"""
from odoo import _, api, models
from odoo.exceptions import UserError

DEGEL = "bf_process_degel"


class BfProcessGel(models.AbstractModel):
    """Refuse l'écriture sur ce qui appartient à une version validée."""

    _name = "bf.process.gel"
    _description = "Gel d'une version validée"

    def _garde_gel(self, processus=None):
        if self.env.context.get(DEGEL):
            return
        proc = processus if processus is not None else self.mapped("process_id")
        geles = proc.filtered(lambda p: p.state == "valide")
        if geles:
            raise UserError(_(
                "« %s » est une version validée : elle ne se modifie plus.\n\n"
                "Créez la version suivante, ou rouvrez celle-ci depuis sa fiche "
                "si la validation était prématurée."
            ) % ", ".join(f"{p.name} v{p.version}" for p in geles))

    @api.model_create_multi
    def create(self, vals_list):
        # le parent est un niveau pour les nœuds et les flux, le processus
        # lui-même pour les niveaux
        niveaux = self.env["bf.process.diagram"].browse([
            v["diagram_id"] for v in vals_list if v.get("diagram_id")])
        directs = self.env["bf.process"].browse([
            v["process_id"] for v in vals_list
            if v.get("process_id") and not v.get("diagram_id")])
        self._garde_gel(niveaux.mapped("process_id") | directs)
        return super().create(vals_list)

    def write(self, vals):
        self._garde_gel()
        return super().write(vals)

    def unlink(self):
        self._garde_gel()
        return super().unlink()
