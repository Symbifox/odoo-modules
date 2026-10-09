"""Les données intimes restent cachées pendant une incarnation.

Le refus à l'entrée RPC (``ir_http``) ne voit que ``call_kw`` ; une fiche santé
se lit aussi par le fil de discussion, une pièce jointe, une image, une
activité ou une route maison. Toutes ces lectures passent par les règles
d'accès : c'est ici qu'on ajoute, pendant une incarnation seulement, un domaine
qui cache les modèles intimes et les conversations Gen privées.

Deux précautions :

* le domaine ajouté dépend de la requête : il reste HORS du cache des règles.
  Le corps mis en cache rappelle ``_compute_domain`` pour les modèles parents
  (``_inherits``, par exemple ``mail.mail`` sur ``mail.message``) ; ces appels
  imbriqués rendent le domaine d'Odoo seul, et le nôtre est ajouté pour les
  parents ici, de la même façon qu'Odoo (``any``) ;
* l'erreur d'accès d'Odoo nomme jusqu'à six fiches refusées à tout interne
  (le groupe « mode debug » est donné à tous). Sur un modèle que ce module
  cache, elle devient générique.

Ce qui passe en ``sudo()`` n'est pas couvert (un compteur, par exemple) : le
README le dit.
"""
import threading

from odoo import _, api, models
from odoo.exceptions import AccessError
from odoo.osv import expression

from .. import impersonation as imp

_NESTED = threading.local()


class IrRule(models.Model):
    _inherit = "ir.rule"

    @api.model
    def _compute_domain(self, model_name, mode="read"):
        if getattr(_NESTED, "depth", 0):
            return super()._compute_domain(model_name, mode)
        _NESTED.depth = 1
        try:
            domain = super()._compute_domain(model_name, mode)
        finally:
            _NESTED.depth = 0
        if not imp.current():
            return domain
        extras = []
        own = imp.private_domain(self.env, model_name)
        if own is not None:
            extras.append(own)
        if model_name in self.env.registry:
            for parent, field in self.env[model_name]._inherits.items():
                inherited = imp.private_domain(self.env, parent)
                if inherited is not None:
                    extras.append([(field, "any", inherited)])
        if not extras:
            return domain
        return expression.AND(([domain] if domain else []) + extras)

    def _make_access_error(self, operation, records):
        names = [records._name, *records._inherits]
        if imp.current() and any(imp.private_domain(self.env, name) is not None for name in names):
            return AccessError(_(
                "This record is not available while you see Symbifox as someone else."))
        return super()._make_access_error(operation, records)
