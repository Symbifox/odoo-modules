"""L'entrée de registre dit ce qu'elle ne couvre pas — et refuse d'attester du vide.

⚠️ **Pourquoi la garde est ICI et pas seulement dans `_execute_destruction`.**

La surcharge de `_execute_destruction` ne protège que tant que la chaîne de
résolution l'atteint. Plusieurs ponts la surchargent, et ils ne cohabitent que
parce que chacun relaie à `super()`. Le jour où un pont surcharge sans relayer,
il fait taire les autres en silence, et une campagne se remet à ne rien détruire en
certifiant « Suppression ». Aucun test qui ne charge qu'un pont à la fois ne le
voit.

📌 **Depuis `privacy_consent` 18.0.5.0.0, le SOCLE porte la même garde**
(`_assert_destruction_really_happened`) et elle couvre tous les
modèles, pas seulement ceux-ci. Celle qui suit reste néanmoins : elle s'exécute
AVANT `super()` et rend un message qui nomme le module et son inbox, là où celle
du socle parle en termes génériques. Elle n'est plus la seule ligne de défense,
elle est la plus explicite.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

from .privacy_regime import privacy_destruction_scope_statement

_logger = logging.getLogger(__name__)

# Méthodes qui affirment que l'enregistrement a DISPARU. « anonymize » et
# « archive » disent l'inverse : la ligne survit, neutralisée ou masquée.
_REMOVAL_METHODS = ("delete", "secure_wipe")

_SMS_MODELS = ("sms.archive.message", "call.archive.call")


class PrivacyDestructionRegister(models.Model):
    _inherit = "privacy.destruction.register"

    @api.model_create_multi
    def create(self, vals_list):
        """Complète l'énoncé de portée, et vérifie ce qui est affirmé.

        L'énoncé est ajouté AVANT `super()` : l'empreinte chaînée est calculée
        après la création, donc elle scelle le texte complet.
        """
        scope = privacy_destruction_scope_statement(self.env)
        for vals in vals_list:
            if vals.get("res_model") not in _SMS_MODELS:
                continue
            self._bf_sms_assert_really_gone(vals)
            description = vals.get("document_description") or ""
            if scope not in description:
                vals["document_description"] = f"{description}\n{scope}".strip()
        return super().create(vals_list)

    @api.model
    def _bf_sms_assert_really_gone(self, vals):
        """Lève si l'entrée dit « supprimé » d'une ligne encore en base."""
        res_id = vals.get("res_id")
        res_model = vals.get("res_model")
        method = vals.get("destruction_method")
        if not res_id or method not in _REMOVAL_METHODS:
            # Pas d'identifiant précis (entrée de lot), ou méthode qui
            # n'affirme pas la disparition : rien à contredire.
            return
        if vals.get("destruction_scope") == "partial":
            # L'entrée déclare elle-même que la ligne survit, amputée de ce que
            # nomme `res_field`. Il n'y a plus de contradiction à relever —
            # même convention que la garde du socle depuis la 18.0.5.0.0.
            return
        survivor = self.env[res_model].sudo().browse(res_id).exists()
        if not survivor:
            return
        _logger.error(
            "bf_sms_privacy: refus d'inscrire au registre la suppression de "
            "%s #%s, qui existe encore. La destruction a été contournée — "
            "vérifier qu'aucun pont ne surcharge _execute_destruction sans "
            "relayer à super().",
            res_model, res_id,
        )
        raise UserError(_(
            "%(model)s #%(id)s existe toujours : le registre ne peut pas "
            "attester sa suppression.\n\n"
            "Cause la plus probable : la destruction a été contournée. Le "
            "correctif de ce module vit dans une surcharge de "
            "« _execute_destruction » ; un module chargé après lui qui "
            "surcharge la même méthode sans relayer à super() la neutralise.",
            model=res_model, id=res_id,
        ))
