# -*- coding: utf-8 -*-
"""Favoris et récents : ce que CHAQUE personne fait de ses signets, et d'elle seule.

Une ligne par personne et par signet. La règle d'accès est globale et ne laisse
voir à chacun que ses propres lignes : ni un responsable des signets ni un
gestionnaire ne voient ce que les autres ouvrent ou marquent. Rien n'est agrégé.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# Ce que le widget montre au plus.
RECENT_LIMIT = 8


class BookmarkUsage(models.Model):
    _name = "bf.bookmark.usage"
    _description = "Favori ou usage récent d'un signet (par personne)"
    _order = "favorite_sequence, id"
    _rec_name = "bookmark_id"

    user_id = fields.Many2one("res.users", required=True, index=True, ondelete="cascade",
                              default=lambda self: self.env.uid, readonly=True)
    bookmark_id = fields.Many2one("bf.bookmark", string="Signet", required=True, index=True,
                                  ondelete="cascade")
    favorite = fields.Boolean(string="Favori", index=True)
    favorite_sequence = fields.Integer(string="Ordre", default=10)
    last_used = fields.Datetime(string="Dernière ouverture", index=True)
    use_count = fields.Integer(string="Ouvertures")

    # 🔴 compute_sudo=False : un champ relié se lit en superutilisateur par défaut. Sans
    # ça, une ligne pointant le signet PERSO d'un autre rendait son titre et son adresse
    # (trouvé par une relecture adverse ; présent de 18.0.1.3.0 à 18.0.1.4.0).
    name = fields.Char(related="bookmark_id.name", string="Titre", compute_sudo=False)
    url = fields.Char(related="bookmark_id.url", string="Adresse", compute_sudo=False)
    folder_id = fields.Many2one(related="bookmark_id.folder_id", string="Dossier",
                                compute_sudo=False)

    _sql_constraints = [
        ("user_bookmark_unique", "unique(user_id, bookmark_id)",
         "Une seule ligne par personne et par signet."),
    ]

    @api.model
    def _check_bookmark_readable(self, bookmark_id):
        """La personne ne pointe que des signets qu'elle peut LIRE elle-même."""
        bookmark = self.env["bf.bookmark"].browse(bookmark_id)
        if not bookmark.exists() or not bookmark.has_access("read"):
            raise AccessError(_("Signet introuvable."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                # 🔴 Toujours pour soi : `user_id` n'est pas choisi par l'appelant.
                vals["user_id"] = self.env.uid
                self._check_bookmark_readable(vals.get("bookmark_id"))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if "user_id" in vals and vals["user_id"] != self.env.uid:
                raise AccessError(_("Une ligne de favori reste à celui qui l'a créée."))
            if "bookmark_id" in vals:
                self._check_bookmark_readable(vals["bookmark_id"])
        result = super().write(vals)
        if not self.env.su:
            # Odoo ne vérifie la règle que sur l'état d'AVANT : on la revérifie après.
            self.check_access("write")
        return result

    @api.model
    def _for(self, bookmarks):
        """Les lignes de l'usager courant pour ces signets, créées au besoin."""
        existing = self.search([("user_id", "=", self.env.uid),
                                ("bookmark_id", "in", bookmarks.ids)])
        missing = bookmarks - existing.bookmark_id
        if missing:
            existing |= self.create([{"bookmark_id": b.id} for b in missing])
        return existing

    def action_remove_favorite(self):
        self.write({"favorite": False})
        return True


class Bookmark(models.Model):
    _inherit = "bf.bookmark"

    usage_ids = fields.One2many("bf.bookmark.usage", "bookmark_id")
    is_favorite = fields.Boolean(string="Mon favori", compute="_compute_is_favorite")

    @api.depends_context("uid")
    def _compute_is_favorite(self):
        favs = self.env["bf.bookmark.usage"].search([
            ("user_id", "=", self.env.uid), ("bookmark_id", "in", self.ids),
            ("favorite", "=", True)]).bookmark_id
        for bookmark in self:
            bookmark.is_favorite = bookmark in favs

    def action_mark_used(self):
        """Le widget et la page l'appellent à chaque ouverture d'un signet.

        Seulement sur ce que la personne peut lire : `_for` passe par ses droits.
        """
        readable = self.exists().filtered(lambda b: b.has_access("read"))
        for usage in self.env["bf.bookmark.usage"]._for(readable):
            usage.write({"last_used": fields.Datetime.now(),
                         "use_count": usage.use_count + 1})
        return True

    def action_toggle_favorite(self):
        readable = self.exists().filtered(lambda b: b.has_access("read"))
        for usage in self.env["bf.bookmark.usage"]._for(readable):
            usage.favorite = not usage.favorite
        return True

    @api.model
    def set_favorite_order(self, bookmark_ids):
        """Glisser-déposer des favoris : l'ordre voulu, de haut en bas.

        Ne touche que MES lignes favorites ; un identifiant étranger est ignoré.
        """
        if not isinstance(bookmark_ids, list) or not all(isinstance(i, int) for i in bookmark_ids):
            return False
        usages = self.env["bf.bookmark.usage"].search([
            ("user_id", "=", self.env.uid), ("favorite", "=", True),
            ("bookmark_id", "in", bookmark_ids)])
        by_bookmark = {u.bookmark_id.id: u for u in usages}
        for position, bid in enumerate(bookmark_ids):
            if bid in by_bookmark:
                by_bookmark[bid].favorite_sequence = (position + 1) * 10
        return True

    @api.model
    def _favorites_and_recents(self):
        """Pour le widget : mes favoris (dans mon ordre), mes récents (sans doublon)."""
        Usage = self.env["bf.bookmark.usage"]
        mine = [("user_id", "=", self.env.uid), ("bookmark_id.active", "=", True)]
        favorites = Usage.search(mine + [("favorite", "=", True)]).bookmark_id
        recents = Usage.search(mine + [("last_used", "!=", False)],
                               order="last_used desc, id desc", limit=RECENT_LIMIT).bookmark_id
        # Un signet devenu illisible (déplacé dans une section qu'on ne voit plus)
        # garde sa ligne d'usage : on ne le rend pas.
        favorites = favorites.filtered(lambda b: b.has_access("read"))
        recents = recents.filtered(lambda b: b.has_access("read"))
        return favorites, recents
