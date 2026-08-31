import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrivacyDestructionCampaignLine(models.Model):
    _inherit = "privacy.destruction.campaign.line"

    def _execute_destruction(self):
        self.ensure_one()
        if self.res_model != "bf.ex.allergy":
            # ⚠️ Plusieurs ponts se suivent sur cette méthode. Relayer.
            return super()._execute_destruction()

        record = self.env["bf.ex.allergy"].sudo().browse(self.res_id)
        if not record.exists():
            raise UserError(
                _("« %(name)s » n'existe plus : rien à détruire, et rien à "
                  "inscrire au registre.", name=self.res_name or self.res_id)
            )
        if self.destruction_method not in ("delete", "secure_wipe"):
            raise UserError(
                _("Méthode « %(method)s » non prise en charge sur une "
                  "déclaration d'allergie. Anonymiser une allergie n'a pas de "
                  "sens : ce qui est sensible est le lien entre la personne et "
                  "l'allergène, et il n'y a rien d'autre à retirer.",
                  method=self.destruction_method or _("(vide)"))
            )

        record.check_access("unlink")
        record.unlink()
        _logger.info(
            "bf_employee_experience_health_privacy: déclaration %s supprimée "
            "par la campagne %s", self.res_id, self.campaign_id.name,
        )
        if self.classification_id:
            self.classification_id.write({"active": False})
        return None
