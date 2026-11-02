"""Exécution réelle de la destruction, et refus de certifier autre chose.

🔴 **Le défaut du socle.** `privacy.destruction.campaign.line._execute_destruction()`
traite « delete » ainsi ::

    elif method == "delete":
        if hasattr(record, "active"):
            record.sudo().write({"active": False})
        else:
            record.sudo().unlink()

Ni `sms.archive.message` ni `call.archive.call` ne portent de champ `active` :
le socle les supprimerait donc pour de bon. Cette surcharge n'est pas là pour
corriger un archivage déguisé — elle est là pour **les pièces jointes MMS**.

🔴 **Les pièces jointes MMS, que le socle laisserait derrière.**
`sms.archive.mms.part.message_id` est `ondelete="cascade"` : supprimer le
message efface les parties MMS **au niveau SQL**, sans que l'ORM n'appelle
jamais `sms.archive.mms.part.unlink()`. Or c'est cet `unlink` qui balaie
``ir_attachment WHERE res_model='sms.archive.mms.part' AND res_id IN …``
(`odoo/models.py`, ~4595). Les photos et vidéos reçues par MMS resteraient donc
en base et au filestore, orphelines et introuvables, pendant que le registre
IMMUABLE attesterait la destruction du message. On supprime donc les parties
par l'ORM, d'abord.

⚠️ **Cette surcharge RELAIE à `super()`**, et ce n'est pas une politesse.
Plusieurs ponts surchargent cette méthode — celui-ci,
`bf_meeting_privacy` et `bf_email_privacy` parmi eux — et ça ne se compose que
parce que chacun relaie pour les modèles qu'il ne possède pas. Un pont qui
oublierait de relayer ferait taire les gardes de tous les autres en silence, et
aucun test qui ne charge qu'un pont ne le verrait.
"""
import logging

from odoo import _, models
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)

_SMS_MODELS = ("sms.archive.message", "call.archive.call")


class PrivacyDestructionCampaignLine(models.Model):
    _inherit = "privacy.destruction.campaign.line"

    def _execute_destruction(self):
        """Détruit vraiment, et le vérifie avant de rendre la main.

        ⚠️ La vérification n'est pas de la ceinture et bretelles.
        `action_execute` crée l'entrée de registre dès que cette méthode rend
        sans lever, sans relire l'état qu'elle vient d'écrire : une ligne
        passée à « échec » y serait quand même réécrite à « fait », avec son
        entrée. **Lever est le seul moyen d'empêcher une certification.**
        """
        self.ensure_one()
        if self.res_model not in _SMS_MODELS:
            return super()._execute_destruction()

        if not self.res_id:
            return

        record = self.env[self.res_model].sudo().browse(self.res_id).exists()
        if not record:
            # Déjà partie : rien à détruire, et surtout rien à certifier.
            self.write({
                "state": "skipped",
                "error_message": _("Enregistrement déjà supprimé"),
            })
            return

        if self.destruction_method != "delete":
            raise UserError(_(
                "La messagerie ne se détruit que par suppression. Méthode "
                "demandée : « %(method)s ». Anonymiser laisserait le corps du "
                "message et les pièces jointes MMS en base, et archiver n'est "
                "même pas offert — ces modèles n'ont pas de champ « actif ». "
                "Le registre attesterait à tort une destruction.",
                method=self.destruction_method or _("(vide)"),
            ))

        # ── 1. Graver AVANT de détruire ─────────────────────────────────
        # L'empreinte n'existe que sur la ligne vivante. Graver d'abord la met
        # dans la même transaction que la suppression : si celle-ci échoue,
        # la pierre tombale s'en va avec elle.
        record._privacy_engrave_tombstones(campaign=self.campaign_id)

        # ── 2. Les parties MMS par l'ORM, pas par la cascade SQL ────────
        parts = getattr(record, "mms_part_ids", None)
        if parts:
            part_count = len(parts)
            parts.sudo().unlink()
            _logger.info(
                "bf_sms_privacy: %s partie(s) MMS et leurs pièces jointes "
                "supprimées avant le message #%s", part_count, self.res_id,
            )

        # ── 3. La ligne elle-même ───────────────────────────────────────
        record.sudo().unlink()

        if self.env[self.res_model].sudo().browse(self.res_id).exists():
            raise UserError(_(
                "%(model)s #%(id)s existe toujours après la suppression. Rien "
                "n'est inscrit au registre : une destruction qui n'a pas eu "
                "lieu ne s'atteste pas.",
                model=self.res_model, id=self.res_id,
            ))

        if self.classification_id:
            self.classification_id.write({"active": False})
        return None


class PrivacyDestructionCampaign(models.Model):
    _inherit = "privacy.destruction.campaign"

    def action_scan(self):
        """Refuse le balayage natif sur une campagne alimentée par le cron.

        Le balayage natif lit `privacy.document.classification` ; nos lignes
        sont déposées directement par le cron du pont. Le lancer ne trouverait
        rien, ferait repasser la campagne par « balayage » puis « révision », et
        donnerait à croire qu'elle est vide.
        """
        cron_rules = (
            self.env["sms.archive.message"]._privacy_rules()
            | self.env["call.archive.call"]._privacy_rules()
        )
        for campaign in self:
            if campaign.retention_calendar_id and campaign.retention_calendar_id in cron_rules:
                raise UserError(_(
                    "Cette campagne est alimentée par le balayage de rétention "
                    "de la messagerie, pas par les classifications "
                    "documentaires. Le balayage natif n'y trouverait rien. "
                    "Approuvez la campagne, puis exécutez-la."
                ))
        return super().action_scan()
