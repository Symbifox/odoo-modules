# -*- coding: utf-8 -*-
"""Un message signé sur le tableau.

Le contributeur n'a pas de compte : `author_name` est du texte libre, saisi
sur la page publique. Le corps est du HTML assaini par Odoo, l'image est une
pièce jointe. Rien n'est jamais rapatrié d'un tiers, donc aucune requête ne
sort du serveur quand quelqu'un signe.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Un message de carte de fête n'est pas un roman, et un champ libre exposé
# sans compte est une porte. Les deux raisons vont dans le même sens.
LONGUEUR_MAX = 1200
NOM_MAX = 80


class CelebrationPost(models.Model):
    _name = "bf.celebration.post"
    _description = "Message sur un tableau de vœux"
    _order = "sequence, id"

    board_id = fields.Many2one(
        "bf.celebration.board", string="Tableau", required=True,
        ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)

    author_name = fields.Char(string="Signé", required=True)
    author_partner_id = fields.Many2one(
        "res.partner", string="Contact", ondelete="set null")
    author_user_id = fields.Many2one(
        "res.users", string="Usager", ondelete="set null",
        help="Rempli seulement quand la personne était connectée.")

    body = fields.Html(string="Message", sanitize=True, sanitize_style=True)
    image = fields.Image(string="Image", max_width=1600, max_height=1600)

    state = fields.Selection(
        [
            ("pending", "En attente d'approbation"),
            ("published", "Visible"),
            ("rejected", "Retiré"),
        ],
        default="published", required=True, index=True)

    create_ip = fields.Char(
        string="Adresse d'origine", readonly=True, groups="base.group_system",
        help="Conservée pour retracer un abus sur un lien public. Effacée "
             "avec le tableau.")

    # ------------------------------------------------------------------
    # Contrôles
    # ------------------------------------------------------------------

    @api.constrains("author_name")
    def _check_nom(self):
        for post in self:
            if len(post.author_name or "") > NOM_MAX:
                raise ValidationError(_(
                    "La signature dépasse %(n)s caractères.", n=NOM_MAX))

    @api.constrains("body")
    def _check_corps(self):
        for post in self:
            texte = post._texte_brut()
            if len(texte) > LONGUEUR_MAX:
                raise ValidationError(_(
                    "Le message dépasse %(n)s caractères.", n=LONGUEUR_MAX))

    def _texte_brut(self):
        self.ensure_one()
        from odoo.tools import html2plaintext
        return html2plaintext(self.body or "").strip()

    @api.constrains("body", "image")
    def _check_non_vide(self):
        for post in self:
            if not post._texte_brut() and not post.image:
                raise ValidationError(_(
                    "Un message vide ne signe rien : écrivez un mot ou "
                    "ajoutez une image."))

    # ------------------------------------------------------------------
    # Modération
    # ------------------------------------------------------------------

    def action_publier(self):
        self.write({"state": "published"})
        return True

    def action_retirer(self):
        self.write({"state": "rejected"})
        return True
