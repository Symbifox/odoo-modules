"""Un rappel de crédit n'a aucun abonné.

``mail.followers`` est lisible par tout interne, sans règle : la liste des
abonnés d'un modèle dit à qui appartient chaque fiche, et combien chacun en a.
Un administrateur pouvait aussi y ajouter une ligne à la main, et recevait alors
par courriel ce qui s'écrivait dans le fil. Le rappel n'en crée plus aucun
(credit_reminder.py) ; ici, aucune ligne ne peut viser ce modèle, et les lignes
qui existeraient encore ne se lisent pas. Deux portes de lecture :

* ``_search`` : la recherche, et la lecture d'un champ stocké par id (``fetch``
  relit les ids par ``_search``) ;
* ``_check_access`` : ``check_access`` / ``has_access``, qu'appellent les contrôleurs.
"""
from odoo import api, models
from odoo.exceptions import AccessError
from odoo.osv import expression

MODELE = "bf.credit.reminder"


class MailFollowers(models.Model):
    _inherit = "mail.followers"

    def _bf_credit_refus(self):
        return AccessError(self.env._("A credit reminder has no followers."))

    @api.model_create_multi
    def create(self, vals_list):
        gardees = [vals for vals in vals_list if vals.get("res_model") != MODELE]
        if len(gardees) != len(vals_list) and not self.env.su:
            raise self._bf_credit_refus()
        # Les chemins internes du cœur (``_insert_followers``) passent en
        # superutilisateur : leurs lignes vers un rappel sont écartées en silence.
        return super().create(gardees)

    def write(self, vals):
        if not self.env.su and ("res_model" in vals or "res_id" in vals):
            for ligne in self.sudo():
                if vals.get("res_model", ligne.res_model) == MODELE:
                    raise self._bf_credit_refus()
        return super().write(vals)

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        if not self.env.su:
            domain = expression.AND([domain, [("res_model", "!=", MODELE)]])
        return super()._search(domain, offset=offset, limit=limit, order=order, **kwargs)

    def _check_access(self, operation):
        resultat = super()._check_access(operation)
        if self.env.su:
            return resultat
        interdits = self.browse([f.id for f in self.sudo() if f.res_model == MODELE])
        if not interdits:
            return resultat
        if resultat:
            interdits |= resultat[0]
        return interdits, self._bf_credit_refus
