# Part of Healthy Fox. See LICENSE file for full copyright and licensing details.
"""Les abonnés d'une fiche santé ne se lisent qu'à son propre nom.

``mail.followers`` est lisible par tout interne et n'a aucune règle : un membre
listait les abonnés des modèles santé et apprenait qui a des fiches, et combien
(mesuré au banc). Seule la personne suit ses fiches (``bf.health.note.only``) :
chacun ne lit donc que ses propres lignes. Deux portes :

* ``_search`` : la recherche, ET la lecture par id d'un champ stocké (``fetch``
  relit les ids par ``_search`` et refuse ceux qui n'en reviennent pas) ;
* ``_check_access`` : ``check_access`` / ``has_access``, qu'appellent les contrôleurs.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError
from odoo.osv import expression

from .nom_prive import MODELES_PRIVES

MODELES = tuple(modele for modele, _sorte in MODELES_PRIVES)


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        if not self.env.su:
            domain = expression.AND([domain, [
                "|", ("res_model", "not in", MODELES), ("partner_id", "=", self.env.user.partner_id.id)]])
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)

    def _check_access(self, operation):
        resultat = super()._check_access(operation)
        if self.env.su:
            return resultat
        moi = self.env.user.partner_id
        interdits = self.browse([
            ligne.id for ligne in self.sudo() if ligne.res_model in MODELES and ligne.partner_id != moi])
        if not interdits:
            return resultat
        if resultat:
            interdits |= resultat[0]
        return interdits, lambda: AccessError(_("Les abonnés d'une fiche santé ne se lisent pas."))
