from odoo import _, models
from odoo.exceptions import UserError

ASSEMBLY_MODEL = "bf.membership.assembly"
# Ce qui fait une pièce : son contenu et son rattachement.
CONTENT_FIELDS = {"raw", "datas", "db_datas", "store_fname", "url", "type", "res_model", "res_id"}


class IrAttachment(models.Model):
    """Les pièces d'une assemblée convoquée ne se remplacent ni ne se suppriment.

    🔴 Une pièce jointe à l'assemblée accompagne l'avis : le courriel de
    convocation la porte. La boîte des pièces du fil permet de remplacer son
    contenu ou de la supprimer, ce qui changerait après coup ce que les membres
    ont reçu, et retirerait la pièce du message de convocation déjà parti, sans
    trace. Dès la convocation (toute assemblée hors brouillon), hors
    superutilisateur, son contenu et son rattachement ne changent plus, et elle
    ne se supprime pas. La retirer de la liste des documents de l'assemblée
    reste permis : la pièce demeure, et le fil le consigne.
    """

    _inherit = "ir.attachment"

    def _assembly_held(self):
        """Les pièces de `self` liées à une assemblée hors brouillon, par leur
        rattachement ou par la liste des documents de l'assemblée."""
        if self.env.su or not self:
            return self.browse()
        pieces = self.sudo()
        bound = [a.res_id for a in pieces if a.res_model == ASSEMBLY_MODEL and a.res_id]
        assemblies = self.env[ASSEMBLY_MODEL].sudo().search([
            ("state", "!=", "draft"), "|", ("id", "in", bound), ("attachment_ids", "in", pieces.ids)])
        return pieces.filtered(lambda a: a in assemblies.attachment_ids or (
            a.res_model == ASSEMBLY_MODEL and a.res_id in assemblies.ids))

    def write(self, vals):
        touched = CONTENT_FIELDS & vals.keys()
        if touched:
            for piece in self._assembly_held():
                rebinding = {"res_model", "res_id"} & touched
                same_binding = all(piece[f] == vals[f] for f in rebinding)
                if touched - {"res_model", "res_id"} or not same_binding:
                    raise UserError(_(
                        "La pièce « %s » accompagne l'avis d'une assemblée convoquée : elle "
                        "ne se remplace ni ne se détache. Retirez-la de la liste des documents "
                        "de l'assemblée, ce que le fil consigne, et joignez la nouvelle.",
                        piece.name))
        return super().write(vals)

    def unlink(self):
        held = self._assembly_held()
        if held:
            raise UserError(_(
                "La pièce « %s » accompagne l'avis d'une assemblée convoquée : elle ne "
                "se supprime pas. Retirez-la de la liste des documents de l'assemblée, ce "
                "que le fil consigne.", held[0].name))
        return super().unlink()
