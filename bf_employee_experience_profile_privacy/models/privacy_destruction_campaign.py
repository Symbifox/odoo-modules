import logging

from odoo import _, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class PrivacyDestructionCampaignLine(models.Model):
    _inherit = "privacy.destruction.campaign.line"

    def _execute_destruction(self):
        self.ensure_one()
        if self.res_model != "bf.ex.profile":
            # ⚠️ Plusieurs ponts se suivent sur cette méthode. Relayer.
            return super()._execute_destruction()

        record = self.env["bf.ex.profile"].sudo().browse(self.res_id)
        if not record.exists():
            raise UserError(
                _("« %(name)s » n'existe plus : rien à détruire, et rien à "
                  "inscrire au registre.", name=self.res_name or self.res_id)
            )
        if self.destruction_method not in ("delete", "secure_wipe"):
            raise UserError(
                _("Méthode « %(method)s » non prise en charge sur une "
                  "déclaration de type de profil. Anonymiser n'a pas de sens "
                  "ici : ce qui est sensible est le lien entre la personne et "
                  "le type, et il n'y a rien d'autre à retirer.",
                  method=self.destruction_method or _("(vide)"))
            )

        # 🔴 `check_access` posé sur un recordset DÉJÀ `sudo()` ne lève jamais :
        # le contrôle se lisait comme une protection et n'en était pas une
        # (même forme dans le pont des allergies). Il porte maintenant sur
        # l'usager réel, avec l'échappatoire du mandat vie privée : le
        # responsable qui exécute une campagne n'a pas forcément de droit sur
        # le dossier du personnel, et c'est la campagne qui est l'autorité.
        reel = self.env["bf.ex.profile"].browse(self.res_id)
        try:
            reel.check_access("unlink")
        except AccessError:
            if not self.env.user.has_group("privacy_consent.group_privacy_manager"):
                raise

        # 🔴 Et le registre ne doit nommer QUE la personne. `res_name` vient de
        # la classification, qui a recopié le nom d'affichage de la
        # déclaration ; les vals du registre sont construites après cet appel
        # et relisent `res_name`. Sans cette réécriture, le référentiel entrait
        # dans un registre immuable, exactement ce que le module promet de ne
        # pas faire.
        self.res_name = record.employee_id.display_name or self.res_name

        record.unlink()
        _logger.info(
            "bf_employee_experience_profile_privacy: déclaration %s supprimée "
            "par la campagne %s", self.res_id, self.campaign_id.name,
        )
        if self.classification_id:
            self.classification_id.write({"active": False})
        return None
