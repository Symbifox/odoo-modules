"""L'entrée de registre dit ce qu'elle ne couvre pas, et refuse d'attester du vide.

⚠️ La garde est ICI aussi, et pas seulement dans `_execute_destruction` : un
pont chargé plus tard qui surchargerait l'exécuteur sans relayer à `super()`
ferait taire la nôtre. Le socle porte la même garde depuis
`privacy_consent` 18.0.5.0.0 ; celle-ci reste parce qu'elle nomme le module.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

from .privacy_destruction_campaign import OUTREACH_MODELS

_logger = logging.getLogger(__name__)

_REMOVAL_METHODS = ("delete", "secure_wipe")


def outreach_destruction_scope_statement(env):
    """Ce que la destruction ne couvre pas.

    ⚠️ Ce texte entre dans une entrée de registre IMMUABLE. Il énonce des faits
    vérifiables et des principes, jamais un chiffre qui vieillit.
    """
    return env._(
        "Portée : la cible quitte la base Odoo avec ses notes, ses interactions "
        "et leurs notes, son fil de discussion, ses activités, ses pièces jointes "
        "et la copie locale de son suivi fédéré ; leur bloc quitte le filestore au "
        "passage suivant du ramasse-miettes des pièces jointes. Le registre ne "
        "nomme la cible que par son numéro.\n"
        "Ce qui reste, et pourquoi : si la personne a refusé d'être démarchée, "
        "une empreinte SHA-256 de chacune de ses adresses et de ses numéros, la "
        "date du refus, son auteur et son motif restent à la liste d'opposition "
        "(`bf.outreach.optout`), sans échéance : c'est ce qui empêche de la "
        "recontacter. Une empreinte de numéro se renverse par force brute : elle "
        "n'est lisible que des administrateurs.\n"
        "Ne sont PAS atteints par cette destruction : (1) le contact Odoo lié, "
        "qui garde son nom, ses coordonnées et, le cas échéant, la mention de son "
        "refus, et "
        "l'opportunité créée depuis la cible, dont la description recopie "
        "l'historique des interactions ; tous deux relèvent de leur propre "
        "régime ; (2) les courriels envoyés, dont une copie vit chez le "
        "destinataire et dans nos boîtes d'envoi, les lettres remises, les appels "
        "et les courriels archivés par la messagerie et l'inbox unifiée, sous "
        "leurs propres règles ; (3) le miroir du suivi fédéré chez un mandant, "
        "retiré à son rafraîchissement suivant seulement si le suivi est encore "
        "partagé ; (4) les sauvegardes, qui gardent l'état antérieur aussi "
        "longtemps que leur propre rotation le conserve.\n"
        "La méthode déclarée est « suppression », pas « effacement sécurisé » : "
        "on retire des lignes et des objets, sans garantie sur les blocs "
        "sous-jacents."
    )


def outreach_touch_scope_statement(env):
    """Énoncé propre à une interaction détruite seule (par classification)."""
    return env._(
        "Portée : l'interaction de démarchage quitte la base Odoo avec ses notes "
        "et ses pièces jointes. La cible à laquelle elle appartient, ses autres "
        "interactions et le contact lié ne sont PAS atteints, ni les courriels, "
        "lettres et appels auxquels l'interaction renvoie, ni les sauvegardes. "
        "Le registre ne nomme l'interaction que par son numéro. La méthode "
        "déclarée est « suppression », pas « effacement sécurisé »."
    )


class PrivacyDestructionRegister(models.Model):
    _inherit = "privacy.destruction.register"

    @api.model_create_multi
    def create(self, vals_list):
        """Complète l'énoncé de portée AVANT `super()` : l'empreinte chaînée le scelle."""
        scopes = {"bf.outreach.target": outreach_destruction_scope_statement(self.env),
                  "bf.outreach.touch": outreach_touch_scope_statement(self.env)}
        for vals in vals_list:
            if vals.get("res_model") not in OUTREACH_MODELS:
                continue
            scope = scopes[vals["res_model"]]
            self._bf_outreach_assert_really_gone(vals)
            description = vals.get("document_description") or ""
            if scope not in description:
                vals["document_description"] = f"{description}\n{scope}".strip()
        return super().create(vals_list)

    @api.model
    def _bf_outreach_assert_really_gone(self, vals):
        res_id, res_model = vals.get("res_id"), vals.get("res_model")
        if not res_id or vals.get("destruction_method") not in _REMOVAL_METHODS:
            return
        if vals.get("destruction_scope") == "partial":
            return
        if not self.env[res_model].sudo().with_context(active_test=False).browse(res_id).exists():
            return
        _logger.error("bf_outreach_privacy: refus d'inscrire la suppression de %s #%s, "
                      "qui existe encore", res_model, res_id)
        raise UserError(_(
            "%(model)s #%(id)s existe toujours : le registre ne peut pas attester sa "
            "suppression. Cause la plus probable : un module chargé après "
            "bf_outreach_privacy surcharge « _execute_destruction » sans relayer à "
            "super().", model=res_model, id=res_id,
        ))
