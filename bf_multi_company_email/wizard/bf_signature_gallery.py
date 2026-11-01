"""bf.signature.gallery — choisir un modèle de signature en le voyant.

Un gabarit de signature est un bloc de QWeb : personne ne devine ce qu'il rend
en lisant son nom, et la seule façon de le savoir était de se l'assigner, de
regarder, puis de revenir en arrière. C'est comme ça qu'on se retrouve avec la
signature d'un modèle qu'on était juste allé voir.

La galerie rend chaque gabarit **avec les données réelles de la personne qui
regarde**, côte à côte, et n'écrit qu'au moment où elle en choisit un. Rien
n'est assigné pour produire l'aperçu.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class BfSignatureGallery(models.TransientModel):
    _name = "bf.signature.gallery"
    _description = "Galerie de modèles de signature"

    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Aperçu pour",
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        required=True,
        default=lambda self: self.env.company,
    )
    scope = fields.Selection(
        selection=[
            ("user", "Pour moi seulement"),
            ("company", "Pour toute la société"),
        ],
        string="Appliquer",
        required=True,
        default="user",
    )
    can_set_company = fields.Boolean(
        default=lambda self: self.env.user.has_group("base.group_system"),
        readonly=True,
    )
    is_locked = fields.Boolean(
        string="Signature imposée",
        compute="_compute_is_locked",
        help="La société impose sa signature : le choix personnel est fermé.",
    )
    line_ids = fields.One2many(
        comodel_name="bf.signature.gallery.line",
        inverse_name="gallery_id",
        string="Modèles",
    )

    @api.depends("user_id")
    def _compute_is_locked(self):
        # La signature imposée est celle de la société de la PERSONNE
        # (`res.users._bf_signature_is_enforced`), pas celle choisie dans
        # l'assistant : sinon choisir une autre société lèverait l'imposition.
        may_override = self.env.user.has_group("base.group_system")
        for wiz in self:
            wiz.is_locked = bool(
                wiz.user_id.company_id.bf_signature_enforce) and not may_override

    def _bf_check_caller(self):
        """L'assistant n'agit que sur la personne qui l'utilise.

        `user_id` et `can_set_company` sont en lecture seule à l'écran, mais un
        `create` par RPC les écrit quand même : ils ne prouvent rien. Les
        droits se relisent ici, au moment d'agir.
        """
        self.ensure_one()
        is_admin = self.env.user.has_group("base.group_system")
        if self.user_id != self.env.user and not is_admin:
            raise AccessError(_(
                "On ne choisit que sa propre signature."))
        return is_admin

    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if "line_ids" not in fields_list:
            return values
        user = self.env.user
        company = self.env.company
        current = user._bf_signature_template(company)
        templates = self.env["mail.template"].sudo().search(
            [("bf_is_signature", "=", True), ("model", "=", "res.users")],
            order="name")
        lines = []
        for template in templates:
            lines.append((0, 0, {
                "template_id": template.id,
                "name": template.name,
                "preview": user._bf_render_with(template, company)
                           or "<p class='text-muted'>Ce gabarit ne rend rien "
                              "pour votre fiche.</p>",
                "is_current": template == current,
            }))
        values["line_ids"] = lines
        return values

    # ------------------------------------------------------------------
    def action_apply(self):
        """Assign the chosen template, then re-render on the spot."""
        is_admin = self._bf_check_caller()
        chosen = self.line_ids.filtered("selected")
        if len(chosen) != 1:
            raise UserError(_(
                "Choisissez exactement un modèle (%s coché(s)).", len(chosen)))
        template = chosen.template_id

        # The wizard is the gate. Writing bf_signature_template_id through
        # sudo() rather than widening SELF_WRITEABLE_FIELDS keeps the choice
        # bounded to what this check allows: a template written against
        # res.users, which on any sane instance only an administrator can
        # author (mail.template create is group-gated). Widening the
        # self-writeable list instead would let a crafted RPC point the field
        # at any template at all.
        if not template.sudo().bf_is_signature or template.sudo().model != "res.users":
            raise UserError(_(
                "« %s » n'est pas marqué comme modèle de signature.",
                template.name))

        if self.scope == "company":
            if not is_admin or self.company_id not in self.env.user.company_ids:
                raise AccessError(_(
                    "Choisir le modèle de toute la société est réservé aux "
                    "administrateurs."))
            self.company_id.sudo().bf_signature_template_id = template.id
            targets = self.company_id.sudo()._bf_signature_users()
            targets._bf_apply_company_signature(force=True)
            message = _(
                "« %(tpl)s » est maintenant le modèle de %(company)s. "
                "%(count)s signature(s) régénérée(s).",
                tpl=template.name, company=self.company_id.name,
                count=len(targets),
            )
        else:
            if self.user_id.company_id.bf_signature_enforce and not is_admin:
                raise UserError(_(
                    "%s impose sa signature : le choix personnel est fermé. "
                    "Demandez à une personne administratrice.",
                    self.user_id.company_id.name))
            self.user_id.sudo().write(
                {"bf_signature_template_id": template.id})
            self.user_id.sudo()._bf_apply_company_signature(force=True)
            message = _(
                "« %s » est maintenant votre modèle de signature.",
                template.name)

        _logger.info(
            "bf_multi_company_email: gabarit %s appliqué (portée %s) par %s",
            template.id, self.scope, self.env.uid,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Signature mise à jour"),
                "message": message,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_reset_to_company(self):
        """Drop the personal override and fall back to the company template."""
        self._bf_check_caller()
        self.user_id.sudo().write({"bf_signature_template_id": False})
        self.user_id.sudo()._bf_apply_company_signature(force=True)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Retour au modèle de la société"),
                "message": _(
                    "Votre signature suit de nouveau « %s ».",
                    self.company_id.bf_signature_template_id.name
                    or _("aucun modèle")),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class BfSignatureGalleryLine(models.TransientModel):
    _name = "bf.signature.gallery.line"
    _description = "Modèle de signature proposé"
    _order = "is_current desc, name, id"

    gallery_id = fields.Many2one(
        comodel_name="bf.signature.gallery",
        required=True,
        ondelete="cascade",
    )
    template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Gabarit",
        required=True,
        readonly=True,
    )
    name = fields.Char(string="Modèle", readonly=True)
    preview = fields.Html(
        string="Aperçu",
        readonly=True,
        sanitize=False,
        help="Rendu avec vos propres données, pas un exemple générique.",
    )
    is_current = fields.Boolean(string="Actuel", readonly=True)
    selected = fields.Boolean(string="Choisir")

    @api.onchange("selected")
    def _onchange_selected(self):
        """One choice at a time — a gallery where two are ticked is a gallery
        that has to guess."""
        for line in self:
            if line.selected and line.gallery_id:
                others = line.gallery_id.line_ids - line
                others.selected = False
