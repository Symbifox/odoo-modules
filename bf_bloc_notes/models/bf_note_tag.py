from odoo import _, fields, models
from odoo.exceptions import AccessError


class BfNoteTag(models.Model):
    _name = "bf.note.tag"
    _description = "Note tag"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    color = fields.Integer(default=0)
    active = fields.Boolean(default=True)
    # Une étiquette créée par une personne est à elle. Vide = commune
    # à l'instance (celles du module, et toutes celles d'avant la 4.2.0, que
    # plusieurs auteurs utilisent souvent déjà). Avant, tout
    # interne lisait, renommait ou supprimait les étiquettes de tout le monde.
    #
    # ⚠️ Pas de propriétaire en superutilisateur (montée, données du module,
    # code en sudo) : Odoo a rempli la colonne neuve des étiquettes existantes
    # avec __system__, ce qui les rendait invisibles à tout le monde.
    user_id = fields.Many2one(
        "res.users", string="Owner", index=True, ondelete="cascade",
        default=lambda self: False if self.env.su else self.env.user,
        help="Empty: shared with the whole instance.",
    )

    _sql_constraints = [
        ("name_uniq", "unique(name, user_id)", "This tag already exists."),
    ]

    def write(self, vals):
        """Le propriétaire d'une étiquette ne
        change pas hors superutilisateur et administration. Odoo ne relit pas
        la règle après un `write` : B rendait SA étiquette commune (visible de
        tous) ou la posait dans la liste de A."""
        if "user_id" in vals and not self.env.su and not self.env.user.has_group("base.group_system"):
            nouveau = vals["user_id"]
            nouveau = nouveau.id if hasattr(nouveau, "id") else nouveau
            if any(tag.user_id.id != (nouveau or False) for tag in self):
                raise AccessError(_("The owner of a tag cannot be changed."))
        return super().write(vals)
