"""L'entrée de registre dit ce qu'elle ne couvre pas, et refuse d'attester du vide.

⚠️ La garde est ICI aussi, et pas seulement dans `_execute_destruction` : un
pont chargé plus tard qui surchargerait l'exécuteur sans relayer à `super()`
ferait taire la nôtre. Le socle porte la même garde depuis
`privacy_consent` 18.0.5.0.0 ; celle-ci reste parce qu'elle nomme le module.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

from .privacy_destruction_campaign import PERSONA_MODELS

_logger = logging.getLogger(__name__)

_REMOVAL_METHODS = ("delete", "secure_wipe")

_PART_LABELS = {
    "contact.persona": "Persona #%s",
    "contact.persona.kpi": "Indicateur de persona #%s",
    "contact.cc.rule": "Règle de copie de persona #%s",
}
# Codes de `privacy.document.classification.pi_category`, comme le socle les inscrit.
_PI_CATEGORIES = {
    "contact.persona": "financial, identification",
    "contact.persona.kpi": "financial, other",
    "contact.cc.rule": "identification",
}


def persona_destruction_scope_statement(env):
    """Ce que la destruction d'un persona couvre, et ce qu'elle ne couvre pas.

    ⚠️ Ce texte entre dans une entrée de registre IMMUABLE. Il énonce des faits
    vérifiables et des principes, jamais un chiffre qui vieillit.
    """
    return env._(
        "Portée : le persona quitte la base Odoo avec son profil (style "
        "d'adresse, salutation, consignes avant d'écrire, détails personnels, "
        "ton, faits de la relation), sa cote de payeur (qualité, notes, délai "
        "moyen), ses indicateurs, ses règles de copie, son fil de discussion "
        "(dont l'historique suivi de la qualité de payeur), ses activités, ses "
        "abonnés et ses pièces jointes ; leur bloc quitte le filestore au passage "
        "suivant du ramasse-miettes des pièces jointes. Le pont inscrit le "
        "persona sous son seul numéro ; ses classifications archivées ne nomment "
        "plus la personne.\n"
        "Ne sont PAS atteints par cette destruction : (1) le contact Odoo "
        "lui-même, qui relève de ses propres règles ; (2) les courriels, "
        "factures et paiements dont le persona est tiré, sous leurs propres "
        "règles (correspondance, documents fiscaux) : le délai de paiement peut "
        "s'en recalculer ; (3) les éléments de connaissance du projet que le "
        "persona citait, qui appartiennent au projet ; (4) les règles de copie "
        "d'AUTRES personas qui nomment ce contact en copie ; (5) ce qui a été "
        "écrit avec l'aide du persona : courriels et lettres envoyés, "
        "conversations de l'assistant qui ont reçu son résumé ; (6) les "
        "sauvegardes, qui gardent l'état antérieur aussi longtemps que leur "
        "propre rotation le conserve.\n"
        "Si la personne reprend contact, un persona neuf peut être recréé à "
        "partir de ses nouveaux échanges : c'est une nouvelle relation, pas la "
        "résurrection de l'ancienne fiche.\n"
        "La méthode déclarée est « suppression », pas « effacement sécurisé » : "
        "on retire des lignes et des objets, sans garantie sur les blocs "
        "sous-jacents."
    )


def persona_part_scope_statement(env):
    """Énoncé propre à un indicateur ou une règle de copie détruit seul (par classification)."""
    return env._(
        "Portée : l'élément du persona (indicateur ou règle de copie) quitte la "
        "base Odoo avec ses notes. Le persona auquel il appartient, ses autres "
        "éléments et le contact lié ne sont PAS atteints, ni les sauvegardes. "
        "Le pont inscrit l'élément sous son seul numéro. La méthode déclarée "
        "est « suppression », pas « effacement sécurisé »."
    )


class PrivacyDestructionRegister(models.Model):
    _inherit = "privacy.destruction.register"

    @api.model_create_multi
    def create(self, vals_list):
        """Complète l'énoncé de portée AVANT `super()` : l'empreinte chaînée le scelle."""
        scopes = {"contact.persona": persona_destruction_scope_statement(self.env),
                  "contact.persona.kpi": persona_part_scope_statement(self.env),
                  "contact.cc.rule": persona_part_scope_statement(self.env)}
        for vals in vals_list:
            if vals.get("res_model") not in PERSONA_MODELS:
                continue
            scope = scopes[vals["res_model"]]
            self._bf_persona_assert_really_gone(vals)
            # L'énoncé promet que le registre ne nomme que le numéro : on le
            # fait tenir ici, quel que soit le chemin qui a rempli `res_name`.
            if vals.get("res_id"):
                vals["res_name"] = _PART_LABELS[vals["res_model"]] % vals["res_id"]
            if not vals.get("pi_categories"):
                vals["pi_categories"] = _PI_CATEGORIES[vals["res_model"]]
            description = vals.get("document_description") or ""
            if scope not in description:
                vals["document_description"] = f"{description}\n{scope}".strip()
        return super().create(vals_list)

    @api.model
    def _bf_persona_assert_really_gone(self, vals):
        res_id, res_model = vals.get("res_id"), vals.get("res_model")
        if not res_id or vals.get("destruction_method") not in _REMOVAL_METHODS:
            return
        if vals.get("destruction_scope") == "partial":
            return
        if not self.env[res_model].sudo().with_context(active_test=False).browse(res_id).exists():
            return
        _logger.error("bf_persona_privacy: refus d'inscrire la suppression de %s #%s, "
                      "qui existe encore", res_model, res_id)
        raise UserError(_(
            "%(model)s #%(id)s existe toujours : le registre ne peut pas attester sa "
            "suppression. Cause la plus probable : un module chargé après "
            "bf_persona_privacy surcharge « _execute_destruction » sans relayer à "
            "super().", model=res_model, id=res_id,
        ))
