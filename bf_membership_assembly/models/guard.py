from odoo.exceptions import UserError


def check_computed_not_written(model, field_names):
    """🔴 Refuse l'écriture d'un champ calculé stocké, hors superutilisateur.

    L'ORM ne refuse pas d'écrire un champ calculé stocké : la valeur écrite
    reste en base jusqu'au prochain recalcul. Un client RPC poserait ainsi un
    résultat « adoptée » sur une proposition rejetée, cinquante bulletins
    remis là où il n'y en a qu'un, une voix à une personne absente ou un
    quorum atteint sans personne dans la salle.

    Les recalculs de l'ORM ne passent pas par `write` : ils ne sont pas
    touchés. Un champ calculé qu'on a voulu modifiable (`readonly=False`)
    reste permis.
    """
    if model.env.su:
        return
    written = sorted(
        name for name in field_names
        if (field := model._fields.get(name)) and field.compute and field.store and field.readonly
    )
    if written:
        labels = ", ".join(model._fields[name]._description_string(model.env) for name in written)
        # `env._` et non `_` : hors d'une méthode de modèle, `_` ne trouve pas
        # la langue de la personne et laisserait le message sans traduction.
        raise UserError(model.env._("Ces champs se calculent ; ils ne s'écrivent pas : %s.", labels))
