"""L'entrée de registre d'un courriel dit ce qu'elle ne couvre pas — et refuse
d'attester une destruction qui n'a pas eu lieu.

⚠️ Pourquoi la garde est ICI et pas seulement dans `_execute_destruction`.

`bf_email_privacy` surcharge `privacy.destruction.campaign.line._execute_destruction()`
pour que `bf.email` soit vraiment supprimé plutôt qu'archivé. Cette surcharge ne
protège que tant que la chaîne de résolution l'atteint. Plusieurs ponts la
surchargent, et ça ne se compose que parce que chacun relaie à `super()` pour
les modèles qu'il ne possède pas.

**Le jour où un pont surcharge cette méthode sans relayer, il fait taire les
gardes de tous les autres en silence**, et une campagne se remet à archiver en
certifiant « Suppression ». Aucun test qui ne charge qu'un pont à la fois ne le
voit.

D'où cette seconde garde, posée sur une méthode DIFFÉRENTE : quel que soit le
chemin qui a mené jusqu'ici, le registre refuse d'attester la suppression d'un
`bf.email` qui existe encore. Un pont qui ne relaie pas casse la destruction ;
il ne pourra pas en plus la faire certifier.

📌 **Depuis `privacy_consent` 18.0.5.0.0, le SOCLE porte la même garde**
(`_assert_destruction_really_happened`), et le défaut d'origine
— « Suppression » qui archive — est corrigé à la source. Celle-ci reste parce
qu'elle s'exécute avant `super()` et nomme l'inbox unifiée dans son message.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Méthodes qui affirment que l'enregistrement a DISPARU. « anonymize » et
# « archive » disent l'inverse : la ligne survit, neutralisée ou masquée.
_REMOVAL_METHODS = ("delete", "secure_wipe")


class PrivacyDestructionRegister(models.Model):
    _inherit = "privacy.destruction.register"

    @api.model_create_multi
    def create(self, vals_list):
        """Complète l'énoncé de portée, et vérifie ce qui est affirmé.

        L'énoncé est ajouté AVANT `super()` : l'empreinte SHA-256 chaînée est
        calculée après la création, donc elle scelle le texte complet.
        """
        scope = self.env["bf.email"]._privacy_destruction_scope_statement()
        for vals in vals_list:
            if vals.get("res_model") != "bf.email":
                continue
            self._bf_email_assert_really_gone(vals)
            description = vals.get("document_description") or ""
            if scope not in description:
                vals["document_description"] = f"{description}\n{scope}".strip()
        return super().create(vals_list)

    @api.model
    def _bf_email_assert_really_gone(self, vals):
        """Lève si l'entrée dit « supprimé » d'un courriel encore en base."""
        res_id = vals.get("res_id")
        method = vals.get("destruction_method")
        if not res_id or method not in _REMOVAL_METHODS:
            # Pas d'identifiant précis (entrée de lot), ou méthode qui n'affirme
            # pas la disparition : rien à contredire.
            return
        if vals.get("destruction_scope") == "partial":
            # L'entrée déclare elle-même que le courriel survit, amputé de ce
            # que nomme `res_field` — même convention que la garde du socle.
            return
        survivor = self.env["bf.email"].with_context(
            active_test=False
        ).sudo().browse(res_id).exists()
        if not survivor:
            return
        _logger.error(
            "bf_email_privacy: refus d'inscrire au registre la suppression du "
            "courriel #%s, qui existe encore (actif=%s). La destruction a été "
            "contournée — vérifier qu'aucun pont ne surcharge "
            "_execute_destruction sans relayer à super().",
            res_id, survivor.active,
        )
        raise UserError(_(
            "Le courriel #%(id)s existe toujours : le registre ne peut pas "
            "attester sa suppression.\n\n"
            "Cause la plus probable : la destruction a été contournée. La "
            "méthode « supprimer » du socle se contente d'archiver dès que le "
            "modèle a un champ « actif », et l'inbox unifiée en a un. Le "
            "correctif de ce module vit dans une surcharge de "
            "« _execute_destruction » ; un module chargé après lui qui "
            "surcharge la même méthode sans relayer à super() la neutralise.",
            id=res_id,
        ))
