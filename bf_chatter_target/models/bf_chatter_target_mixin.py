"""Le champ « fiche cible » et ses gardes, à hériter dans un assistant.

Un importateur qui hérite ce mixin obtient d'un coup le champ, la liste des
modèles compatibles, la résolution des références collées et le contrôle
d'accès sur la cible. Il ne lui reste qu'à décider quoi poser dessus.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class BfChatterTargetMixin(models.AbstractModel):
    _name = "bf.chatter.target.mixin"
    _description = "Unified target record selection"

    # Obligatoire côté vue, jamais côté modèle : `required=True` poserait un
    # NOT NULL sur la colonne du transient, donc plus moyen d'instancier
    # l'assistant avant que l'utilisateur ait choisi sa cible.
    target_reference = fields.Reference(
        selection="_selection_chatter_target",
        string="Target record",
        help="Search by name, by number, by shortcut (task:22299, "
             "invoice:42, bf.email:17) or paste an Odoo URL. Any record "
             "with a chatter is a valid target.",
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
            raise UserError(_("Please select a target record."))
        if not target.exists():
            raise UserError(_("The target record has been deleted."))
        if not hasattr(target, "message_post"):
            raise UserError(_(
                "The model %s has no chatter.", target._name,
            ))
        try:
            target.check_access(operation)
        except AccessError as exc:
            raise UserError(_(
                "Access denied on %(model)s #%(id)s: %(err)s",
                model=target._name, id=target.id, err=exc,
            )) from exc
        return target
