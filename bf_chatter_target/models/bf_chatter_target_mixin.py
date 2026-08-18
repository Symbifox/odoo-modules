"""Le champ « fiche cible » et ses gardes, à hériter dans un assistant.

Un importateur qui hérite ce mixin obtient d'un coup le champ, la liste des
modèles compatibles, la résolution des références collées et le contrôle
d'accès sur la cible. Il ne lui reste qu'à décider quoi poser dessus.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class BfChatterTargetMixin(models.AbstractModel):
    _name = "bf.chatter.target.mixin"
    _description = "Sélection unifiée d'une fiche cible"

    # Obligatoire côté vue, jamais côté modèle : `required=True` poserait un
    # NOT NULL sur la colonne du transient, donc plus moyen d'instancier
    # l'assistant avant que l'utilisateur ait choisi sa cible.
    target_reference = fields.Reference(
        selection="_selection_chatter_target",
        string="Fiche cible",
        help="Cherchez par nom, par numéro, par raccourci (task:22299, "
             "facture:42, bf.email:17) ou collez une URL Odoo. Toute fiche "
             "dotée d'un chatter est une cible valide.",
    )

    @api.model
    def _selection_chatter_target(self):
        return self.env["bf.chatter.target"]._thread_model_selection()

    @api.model
    def _resolve_chatter_target(self, text):
        """Résout une référence collée. Exposé ici pour que chaque assistant
        garde un point d'entrée local, testable sans connaître le socle."""
        return self.env["bf.chatter.target"]._resolve(text)

    def _get_chatter_target(self, operation="write"):
        """La cible choisie, une fois vérifiées son existence, sa compatibilité
        et les droits de l'utilisateur. Lève un ``UserError`` sinon."""
        self.ensure_one()
        target = self.target_reference
        if not target:
            raise UserError(_("Veuillez sélectionner une fiche cible."))
        if not target.exists():
            raise UserError(_("La fiche cible a été supprimée."))
        if not hasattr(target, "message_post"):
            raise UserError(_(
                "Le modèle %s ne porte pas de chatter.", target._name,
            ))
        try:
            target.check_access(operation)
        except AccessError as exc:
            raise UserError(_(
                "Accès refusé sur %(model)s #%(id)s : %(err)s",
                model=target._name, id=target.id, err=exc,
            )) from exc
        return target
