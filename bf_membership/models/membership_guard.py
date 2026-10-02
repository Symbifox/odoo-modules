from odoo import _, api, models
from odoo.exceptions import AccessError

from .res_partner import MEMBERSHIP_FIELDS


def _holds_membership(model):
    """Le contact, et tout modèle qui en hérite les champs par `_inherits`
    (l'usager, `res.users`)."""
    return model._name == "res.partner" or "res.partner" in model._inherits


def _path_touches(model, path):
    """(touché, modèle d'arrivée) pour un chemin pointé lu depuis `model`."""
    current = model
    for segment in path.split("."):
        if current is None:
            return False, None
        if segment in MEMBERSHIP_FIELDS and _holds_membership(current):
            return True, None
        field = current._fields.get(segment)
        current = current.env[field.comodel_name] if field is not None and field.relational else None
    return False, current


def touches_membership(model, domain):
    """Vrai si ce domaine, lu depuis `model`, touche à l'appartenance d'un
    contact, à n'importe quel segment d'un chemin et dans les sous-domaines
    `any`. (Le tri, lui, Odoo le refuse déjà sur un champ réservé.)

    🔴 Pourquoi sur `base` et pas sur le contact : un Many2one `auto_join`
    (le contact d'un projet, d'un compte analytique) et l'héritage `_inherits`
    de l'usager font joindre la table des contacts sans passer par
    `res.partner._search`. La garde se tient donc à la racine de chaque
    recherche, et suit le chemin modèle par modèle.
    """
    for leaf in domain or []:
        if not (isinstance(leaf, (list, tuple)) and len(leaf) == 3 and isinstance(leaf[0], str)):
            continue
        path, operator, value = leaf
        if operator not in ("any", "not any") and not MEMBERSHIP_FIELDS.intersection(path.split(".")):
            continue
        touched, arrival = _path_touches(model, path)
        if touched:
            return True
        if operator in ("any", "not any") and arrival is not None and isinstance(value, (list, tuple)):
            if touches_membership(arrival, value):
                return True
    return False


class Base(models.AbstractModel):
    _inherit = "base"

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        # Le rôle ne se lit que si la recherche touche à l'appartenance : la
        # garde ne coûte rien aux autres recherches.
        if (not self.env.su and touches_membership(self, domain)
                and not self.env.user.has_group("bf_membership.group_membership_user")):
            raise AccessError(_("La recherche par l'appartenance à l'association est réservée au rôle Membres."))
        return super()._search(domain, offset=offset, limit=limit, order=order)
