"""Au portail, la personne saisit elle-même ses pronoms.

« Mon compte » (`/my/account`) écrit la fiche en sudo, avec suivi. Un client
qui y change son nom n'y laisse pas pour autant « ancien → nouveau » au fil :
c'est sa propre fiche, que `res.partner.write` renomme sans suivi d'où que
vienne l'écriture (ici, la boutique, les préférences, le RPC).
"""

from odoo import _
from odoo.http import request

from odoo.addons.portal.controllers import portal

from ..models.res_partner import (
    PRONOUNS_MAX_LENGTH, pronouns_are_valid, pronouns_error_message)

PORTAL_FIELDS = ["bf_pronouns", "bf_grammatical_gender"]


class CustomerPortal(portal.CustomerPortal):

    def _prepare_portal_layout_values(self):
        values = super()._prepare_portal_layout_values()
        # Libellés traduits : `.selection` rendrait ceux de la source.
        values["bf_grammatical_gender_options"] = request.env["res.partner"]._fields[
            "bf_grammatical_gender"]._description_selection(request.env)
        return values

    def _get_optional_fields(self):
        return super()._get_optional_fields() + PORTAL_FIELDS

    def details_form_validate(self, data, partner_creation=False):
        # Un nom fait d'espaces passe le contrôle d'Odoo (`not data.get`) et
        # vide la fiche de son nom : vidé ici, il est refusé par Odoo comme
        # un champ obligatoire manquant, avec son message.
        if "name" in data and not (data.get("name") or "").strip():
            data["name"] = ""
        error, error_message = super().details_form_validate(data, partner_creation)
        # Une valeur hors de la liste ne lève qu'au vidage du cache, APRÈS
        # l'écriture : on la refuse ici, avec un message.
        gender = data.get("bf_grammatical_gender")
        allowed = dict(request.env["res.partner"]._fields["bf_grammatical_gender"].selection)
        if gender and gender not in allowed:
            error["bf_grammatical_gender"] = "error"
            error_message.append(_("Please choose a grammatical gender from the list."))
        pronouns = (data.get("bf_pronouns") or "").strip()
        if len(pronouns) > PRONOUNS_MAX_LENGTH:
            error["bf_pronouns"] = "error"
            error_message.append(_(
                "Pronouns are limited to %(count)s characters.", count=PRONOUNS_MAX_LENGTH))
        # La contrainte du modèle lèverait APRÈS l'écriture, en page d'erreur :
        # on refuse ici, avec le même message, et rien n'est écrit.
        if not pronouns_are_valid(pronouns):
            error["bf_pronouns"] = "error"
            error_message.append(pronouns_error_message(request.env))
        return error, error_message

    def on_account_update(self, values, partner):
        super().on_account_update(values, partner)
        if "bf_pronouns" in values:
            values["bf_pronouns"] = (values["bf_pronouns"] or "").strip() or False
