"""Les gabarits : un jeu de liens attribué par groupe.

L'intention du napkin : « templates by user groups », et « employees can get it
by default ». Un gabarit décrit les liens que reçoit une catégorie de personnes
(l'équipe conseil, la direction), pour qu'une nouvelle page parte déjà garnie.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfLinkpageTemplate(models.Model):
    _name = "bf.linkpage.template"
    _description = "Gabarit de page de liens"
    _order = "sequence, name"

    name = fields.Char(string="Nom", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text(string="Remarques")

    group_ids = fields.Many2many(
        "res.groups",
        string="Groupes visés",
        help="Les groupes dont les membres reçoivent ce gabarit par défaut. "
             "Vide = le gabarit ne s'applique qu'à la main.",
    )
    is_default = fields.Boolean(
        string="Gabarit par défaut",
        help="Utilisé quand aucun gabarit de groupe ne correspond.",
    )

    # Le VISUEL du gabarit. Posé sur la page au premier rattachement seulement
    # (voir `_apply_template`) : le réappliquer à chaque rafraîchissement
    # écraserait toutes les nuits la couleur que quelqu'un a choisie pour sa
    # propre page, sans que rien ne le lui dise.
    layout = fields.Selection(
        [
            ("cards", "Cartes"),
            ("soft", "Cartes en relief"),
            ("minimal", "Épurée"),
            ("pills", "Boutons pleins"),
            ("mono", "Technique"),
        ],
        string="Disposition",
        default="cards",
        required=True,
    )
    theme = fields.Selection(
        [
            ("auto", "Selon l'appareil"),
            ("light", "Clair"),
            ("dark", "Sombre"),
        ],
        string="Thème",
        default="auto",
        required=True,
    )
    accent_color = fields.Char(string="Couleur d'accent", default="#29ABE1")

    line_ids = fields.One2many(
        "bf.linkpage.template.line", "template_id", string="Liens",
    )
    page_ids = fields.One2many("bf.linkpage", "template_id", string="Pages")
    page_count = fields.Integer(compute="_compute_page_count")

    @api.depends("page_ids")
    def _compute_page_count(self):
        for template in self:
            template.page_count = len(template.page_ids)

    def _visual_values(self):
        """Les valeurs d'allure à poser sur une page."""
        self.ensure_one()
        return {
            "layout": self.layout,
            "theme": self.theme,
            "accent_color": self.accent_color or "#29ABE1",
        }

    @api.constrains("is_default")
    def _check_single_default(self):
        for template in self:
            if not template.is_default:
                continue
            other = self.search_count([
                ("is_default", "=", True), ("id", "!=", template.id),
            ])
            if other:
                raise ValidationError(_(
                    "Il ne peut y avoir qu'un seul gabarit par défaut. "
                    "Retirez la case sur l'autre avant de la poser ici."
                ))

    @api.model
    def _for_user(self, user):
        """Le gabarit qui s'applique à cet utilisateur.

        Le plus spécifique gagne : un gabarit dont l'utilisateur est membre
        d'un des groupes passe avant le gabarit par défaut. À égalité, la
        séquence tranche, de sorte que le choix ne dépende jamais de l'ordre
        d'insertion en base.
        """
        by_group = self.search([("group_ids", "!=", False)], order="sequence, id")
        for template in by_group:
            if user.groups_id & template.group_ids:
                return template
        return self.search([("is_default", "=", True)], limit=1)


class BfLinkpageTemplateLine(models.Model):
    _name = "bf.linkpage.template.line"
    _description = "Lien d'un gabarit de page de liens"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "bf.linkpage.template", required=True, ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Libellé", required=True, translate=True)
    subtitle = fields.Char(string="Précision", translate=True)
    icon = fields.Char(string="Icône")
    source_code = fields.Selection(
        selection=lambda self: self.env["bf.linkpage.source"]._selection(),
        string="Source",
        default="manual",
        required=True,
    )
    url = fields.Char(string="Adresse")

    # La même référence douce que sur le lien. Sans elle, un gabarit ne peut
    # que laisser le résolveur DEVINER, et deviner se voit mal : sur une base
    # qui compte dix-sept types de rendez-vous publics, la recherche prend le
    # premier par séquence, qui n'est pas forcément celui qu'on met dans sa
    # signature.
    source_res_model = fields.Char(string="Modèle visé")
    source_res_id = fields.Integer(string="Enregistrement visé")

    def _copy_translations_to(self, link):
        """Reporter les traductions de la ligne sur le lien créé.

        Sans ceci, la traduction anglaise d'un libellé serait perdue à CHAQUE
        rafraîchissement. `_link_values` ne rend qu'une valeur par champ, celle
        de la langue courante : la passe de nuit tourne en `fr_CA`, elle
        recréerait donc des liens français uniquement, et la version anglaise
        de la page retomberait en français sans que rien ne le signale.

        On lit et on réécrit langue par langue, plutôt que de copier la colonne
        `jsonb` : passer par l'ORM laisse Odoo gérer les slots absents et le
        repli sur la langue par défaut.
        """
        self.ensure_one()
        langs = self.env["res.lang"].search([]).mapped("code")
        if len(langs) < 2:
            return
        for field in ("name", "subtitle"):
            valeurs = {
                code: self.with_context(lang=code)[field]
                for code in langs
                if self.with_context(lang=code)[field]
            }
            if valeurs:
                link.update_field_translations(field, valeurs)

    def _link_values(self, page):
        """Les valeurs du lien à créer sur une page depuis cette ligne."""
        self.ensure_one()
        return {
            "page_id": page.id,
            "sequence": self.sequence,
            "name": self.name,
            "subtitle": self.subtitle,
            # Une ligne de réseau n'a pas à répéter son icône : elle découle
            # du code, et la saisir deux fois invite à les désaccorder.
            "icon": self.icon or self.env["bf.linkpage.source"]._default_icon(
                self.source_code
            ),
            "source_code": self.source_code,
            "url": self.url,
            "source_res_model": self.source_res_model or False,
            "source_res_id": self.source_res_id or False,
            "from_template": True,
        }
