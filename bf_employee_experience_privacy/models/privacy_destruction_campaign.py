import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Les modèles que ce pont ouvre à la classification, donc ceux dont il doit
# répondre au moment de la destruction.
_EX_MODELS = ("bf.ex.entitlement", "bf.ex.usage", "bf.ex.claim")


class PrivacyDestructionCampaignLine(models.Model):
    """Fait dire vrai à la campagne sur les avantages, et impose l'ordre.

    🔴 **Ce que la méthode générique ferait.** Elle archive au lieu de détruire
    dès que le modèle porte un champ `active`. Aucun des trois modèles d'ici
    n'en porte, donc le `unlink` aurait bien lieu — mais il faut le vérifier à
    chaque ajout de champ, pas l'espérer. Le test `test_no_active_field_on_our_models`
    est là pour ça : le jour où quelqu'un ajoute `active` sur `bf.ex.usage`,
    il tombe.

    🔴 **L'échec n'arrête pas la certification.** Dans `action_execute`, l'entrée
    de registre est créée APRÈS l'appel, sans regarder l'état que celui-ci vient
    d'écrire : une ligne passée à « échec » est réécrite à « fait » avec son
    entrée, et le registre refuse `write` et `unlink`. Le seul moyen d'empêcher
    une certification fausse est donc de LEVER.

    🔴 **L'agrégat d'abord.** Détruire un usage non agrégé fait perdre la mesure
    en même temps que la donnée personnelle, définitivement. La campagne lève
    plutôt que de laisser faire.
    """

    _inherit = "privacy.destruction.campaign.line"

    def _execute_destruction(self):
        self.ensure_one()
        if self.res_model not in _EX_MODELS:
            # ⚠️ La chaîne compte plusieurs ponts sur cette méthode. Relayer,
            # ou casser en silence les autres modules.
            return super()._execute_destruction()

        record = self.env[self.res_model].sudo().browse(self.res_id)
        if not record.exists():
            raise UserError(
                _("« %(name)s » n'existe plus : rien à détruire, et rien à "
                  "inscrire au registre.", name=self.res_name or self.res_id)
            )

        method = self.destruction_method
        if method not in ("delete", "secure_wipe"):
            raise UserError(
                _("Méthode « %(method)s » non prise en charge sur %(model)s. "
                  "Une ligne de registre ne s'anonymise pas : elle ne contient "
                  "qu'un lien vers une personne, un lien vers un avantage, une "
                  "date et un montant. Retirer la personne ne laisse rien "
                  "d'utile, et la mesure est déjà gardée par l'agrégat.",
                  method=method or _("(vide)"), model=self.res_model)
            )

        if self.res_model == "bf.ex.usage":
            self._ex_check_aggregate(record)

        # Les pièces jointes rattachées DIRECTEMENT à l'enregistrement.
        # ⚠️ `mail.thread.unlink` supprime les messages et les abonnés, pas
        # celles-là : un reçu déposé sur la ligne survivrait à sa destruction.
        attachments = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", self.res_model), ("res_id", "=", self.res_id),
        ])
        attachment_count = len(attachments)
        if attachments:
            attachments.unlink()

        record.check_access("unlink")
        record.unlink()
        _logger.info(
            "bf_employee_experience_privacy: %s,%s supprimé pour de bon "
            "(%s pièce(s) jointe(s)) par la campagne %s",
            self.res_model, self.res_id, attachment_count, self.campaign_id.name,
        )
        if self.classification_id:
            self.classification_id.write({"active": False})
        return None

    def _ex_check_aggregate(self, record):
        """Refuser de détruire un usage dont l'année n'est pas agrégée.

        C'est l'ordre qui compte : agréger, puis détruire. L'inverse fait
        perdre le taux d'adhésion historique sans retour possible.
        """
        Aggregate = self.env["bf.ex.usage.aggregate"].sudo()
        year = record.date.year
        if Aggregate._has_coverage(record.benefit_id, year, record.company_id):
            return
        raise UserError(
            _("L'usage de %(year)s pour « %(benefit)s » n'a pas encore été "
              "agrégé. Le détruire maintenant ferait perdre le taux d'adhésion "
              "de cette année-là, et il ne se reconstitue pas.\n\n"
              "Passez d'abord par « Agréger tous les usages » dans "
              "Expérience employé > Analyse, ou attendez le cron nocturne. "
              "L'agrégat ne garde aucun nom.",
              year=year, benefit=record.benefit_id.display_name)
        )
