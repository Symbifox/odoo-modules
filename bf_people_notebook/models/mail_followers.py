"""Garde 6 : les abonnés d'une fiche de personne ne se lisent pas.

``mail.followers`` est lisible par tout interne et n'a aucune règle : sans cette
garde, un membre du foyer listait les abonnés de ``bf.people.person`` et apprenait
l'id et la propriétaire de chaque fiche. Deux portes, à prouver chacune :

* ``_search`` : la recherche, ET la lecture par id d'un champ stocké (``fetch`` relit
  les ids par ``_search`` et refuse ceux qui n'en reviennent pas) ;
* ``_check_access`` : ``check_access`` / ``has_access``, qu'appellent les contrôleurs.
"""
from odoo import api, models
from odoo.exceptions import AccessError
from odoo.osv import expression

MODELE = "bf.people.person"


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    def _fiches_lisibles(self):
        """Les fiches que la personne lit, cherchées en sudo. Refus par défaut : un
        abonné dont la fiche n'existe plus (supprimée hors ORM) reste caché aussi,
        alors qu'une liste des fiches « cachées » l'aurait laissé passer."""
        return self.env[MODELE].sudo().with_context(active_test=False)._search(
            ["|", ("user_id", "=", self.env.uid), ("shared_user_ids", "in", [self.env.uid])])

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        if not self.env.su:
            domain = expression.AND([domain, [
                "|", ("res_model", "!=", MODELE), ("res_id", "in", self._fiches_lisibles())]])
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)

    def _check_access(self, operation):
        resultat = super()._check_access(operation)
        if self.env.su:
            return resultat
        lignes = self.sudo().filtered(lambda f: f.res_model == MODELE)
        if not lignes:
            return resultat
        fiches = self.env[MODELE].sudo().with_context(active_test=False).browse(
            lignes.mapped("res_id")).exists()
        lisibles = {f.id for f in fiches if f._readable_by(self.env.uid)}
        interdits = self.browse([f.id for f in lignes if f.res_id not in lisibles])
        if not interdits:
            return resultat
        if resultat:
            interdits |= resultat[0]
        return interdits, lambda: AccessError(self.env._(
            "You cannot see the followers of a private card."))
