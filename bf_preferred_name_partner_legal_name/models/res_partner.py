"""Le « Legal name » de `partner_legal_name` fermé sur la fiche d'une personne.

Masquer le champ (vue du pont) ne suffisait pas : il restait inscriptible par
RPC, import ou `onchange`, lisible par tous les internes et suivi au fil. Le
nom légal d'une personne n'a qu'une place, `bf_legal_name`, réservée au groupe
« Noms légaux ».

Exception : la fiche d'une société Odoo (`res.company.partner_id`), dont
l'en-tête des documents lit le nom légal, même si elle n'est pas cochée
« société ».

« Type de société » (`company_type`,
l'import et l'API l'emploient) n'est pas stocké ; son inverse pose
`is_company` APRÈS le contrôle des champs stockés. Une société créée par
`company_type` seul, avec son nom légal, était refusée comme une personne. On
pose donc `is_company` d'après `company_type` avant l'écriture : c'est la
valeur que l'inverse lui donnerait de toute façon, et la contrainte la voit.
"""

from odoo import _, api, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._bf_is_company_from_type(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._bf_is_company_from_type(vals))

    @api.model
    def _bf_is_company_from_type(self, vals):
        """`is_company` tel que l'inverse de `company_type` le posera."""
        if "company_type" not in vals:
            return vals
        return dict(vals, is_company=vals["company_type"] == "company")

    @api.constrains("legal_name", "is_company")
    def _check_bf_legal_name_companies_only(self):
        persons = self.filtered(lambda p: p.legal_name and not p.is_company)
        if not persons:
            return
        companies = self.env["res.company"].sudo().with_context(active_test=False).search(
            [("partner_id", "in", persons.ids)])
        if persons - companies.partner_id:
            # Le message ne cite pas la valeur : il s'affiche à qui l'a saisie,
            # mais part aussi aux journaux.
            raise ValidationError(_(
                "The company legal name is for companies only. A person's legal "
                "name goes in “Personal legal name”, which only the “Legal "
                "names” group can read. To turn a company into an individual, "
                "clear its company legal name first."))
