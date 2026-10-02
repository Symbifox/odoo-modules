from odoo import _, api, models
from odoo.exceptions import UserError


class CorporateResolutionSignatory(models.Model):
    """Les signataires d'une résolution inscrite par le pont sont figés.

    🔴 Ce sont la présidence et le secrétariat de l'assemblée qui a adopté la
    résolution, eux-mêmes gelés à la clôture. Hors superutilisateur, une ligne
    ne s'ajoute pas à une résolution pontée, ne s'y modifie pas et ne s'en
    retire pas : sinon le registre ferait signer la résolution par d'autres
    personnes que celles qui ont tenu l'assemblée, ou en qualité
    d'« Actionnaire ».
    """

    _inherit = "corporate.resolution.signatory"

    def _refuse_bridged(self, resolutions):
        bridged = resolutions.filtered("bf_assembly_proposal_id")
        if bridged and not self.env.su:
            raise UserError(_(
                "« %s » vient d'une assemblée des membres : ses signataires sont la "
                "présidence et le secrétariat de cette assemblée, et ne changent pas "
                "au registre.", bridged[0].name))

    @api.model_create_multi
    def create(self, vals_list):
        # Avant la création, sur les valeurs reçues : les contrôles du registre
        # ne doivent pas refuser à sa place ; puis sur la ligne créée, car la
        # résolution peut aussi venir d'une valeur par défaut du contexte.
        self._refuse_bridged(self.env["corporate.resolution"].browse(
            [vals["resolution_id"] for vals in vals_list if vals.get("resolution_id")]))
        lignes = super().create(vals_list)
        self._refuse_bridged(lignes.resolution_id)
        return lignes

    def write(self, vals):
        resolutions = self.resolution_id
        if vals.get("resolution_id"):
            resolutions |= self.env["corporate.resolution"].browse(vals["resolution_id"])
        self._refuse_bridged(resolutions)
        return super().write(vals)

    def unlink(self):
        self._refuse_bridged(self.resolution_id)
        return super().unlink()
