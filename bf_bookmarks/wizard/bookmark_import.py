# -*- coding: utf-8 -*-
"""Importer un fichier de signets au format Netscape.

C'est le format que produisent Chrome, Edge, Brave et Firefox à l'export, et celui
de la sauvegarde nocturne de l'app Bookmarks de Nextcloud (`/Bookmarks Backups/`).

L'import se rejoue sans doubler : un dossier du même nom sous le même parent est
repris, un signet de la même adresse dans le même dossier est sauté.
"""
import base64
from html.parser import HTMLParser

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..models.onchange_guard import refuse_unreadable_targets

# Un export réel en compte quelques centaines ; au-delà, c'est une erreur ou un abus.
MAX_BOOKMARKS = 5000


class NetscapeParser(HTMLParser):
    """Lit un fichier Netscape en une liste d'événements, sans rien écrire.

    Événements : ("folder", titre), ("open",), ("close",), ("bookmark", titre, url).
    Un <H3> annonce un dossier, le <DL> qui suit l'ouvre ; un <A HREF> est un signet.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []
        self._text = None
        self._href = None
        self._pending_folder = False

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self._text = []
        elif tag == "a":
            self._text = []
            self._href = dict(attrs).get("href") or ""
        elif tag == "dl":
            self.events.append(("open", self._pending_folder))
            self._pending_folder = False

    def handle_endtag(self, tag):
        if tag == "h3" and self._text is not None:
            self.events.append(("folder", "".join(self._text).strip()))
            self._pending_folder = True
            self._text = None
        elif tag == "a" and self._text is not None:
            self.events.append(("bookmark", "".join(self._text).strip(), self._href))
            self._text = None
            self._href = None
        elif tag == "dl":
            self.events.append(("close",))

    def handle_data(self, data):
        if self._text is not None:
            self._text.append(data)


class BookmarkImport(models.TransientModel):
    _name = "bf.bookmark.import"
    _description = "Importer des signets"

    file = fields.Binary(string="Fichier de signets", required=True)
    filename = fields.Char()
    folder_id = fields.Many2one(
        "bf.bookmark.folder", string="Dans le dossier", required=True,
        default=lambda self: self.env["bf.bookmark.folder"]._personal_root())

    def onchange(self, values, field_names, fields_spec):
        # Le nom du dossier se lirait aussi par le formulaire, sans rien créer.
        refuse_unreadable_targets(self, values)
        return super().onchange(values, field_names, fields_spec)

    @api.model
    def default_get(self, fields_list):
        # Une valeur par défaut posée par l'usager (`ir.default`) aussi.
        defaults = super().default_get(fields_list)
        refuse_unreadable_targets(self.browse(), defaults)
        return defaults

    @api.constrains("folder_id")
    def _check_folder_writable(self):
        """🔴 Seulement un dossier où l'on peut écrire. Sans ça, l'assistant prenait
        n'importe quel dossier, et son nom se lisait par le champ relié, perso
        d'autrui compris. « Introuvable » et « interdit » disent la même chose."""
        for wizard in self:
            if wizard.folder_id and not wizard.folder_id.has_access("write"):
                raise AccessError(_("Dossier introuvable."))

    @api.model
    def _import_html(self, html, folder):
        """Importe `html` sous `folder`. Rend (dossiers créés, signets créés, sautés, refusés)."""
        parser = NetscapeParser()
        parser.feed(html)
        parser.close()
        return self._import_events(parser.events, folder)

    @api.model
    def _events_from_tree(self, nodes, depth=0):
        """Un arbre venu de l'extension ([{title, url} | {title, children}]) en événements.

        Au-delà de la profondeur permise, les dossiers sont aplatis dans leur parent
        (le contenu est gardé, pas la hiérarchie).
        """
        from ..models.bookmark_folder import MAX_DEPTH
        events = []
        if not isinstance(nodes, list):
            return events
        for node in nodes:
            if not isinstance(node, dict):
                continue
            title = str(node.get("title") or "")[:300]
            if node.get("url"):
                events.append(("bookmark", title, str(node["url"])[:2000]))
            elif isinstance(node.get("children"), list):
                if depth + 1 < MAX_DEPTH - 1:
                    events += [("folder", title), ("open", True)]
                    events += self._events_from_tree(node["children"], depth + 1)
                    events.append(("close",))
                else:
                    events += self._events_from_tree(node["children"], depth + 1)
        return events

    @api.model
    def import_from_browser(self, nodes):
        """L'extension envoie les favoris du navigateur : ils vont dans le Perso, sous
        « Importés du navigateur ». Rejouable sans doublons, comme l'import de fichier."""
        root = self.env["bf.bookmark.folder"]._personal_root()
        title = _("Importés du navigateur")
        target = root.child_ids.filtered(lambda f: f.name == title)[:1] or \
            self.env["bf.bookmark.folder"].create({"name": title, "parent_id": root.id})
        return self._import_events(self._events_from_tree(nodes), target)

    @api.model
    def _import_events(self, events, folder):
        Folder = self.env["bf.bookmark.folder"]
        Bookmark = self.env["bf.bookmark"]
        if sum(1 for e in events if e[0] == "bookmark") > MAX_BOOKMARKS:
            raise UserError(_(
                "Ce fichier contient plus de %s signets. Découpez-le avant de l'importer.",
                MAX_BOOKMARKS))
        stack = [folder]
        last_folder = None
        stats = {"folders": 0, "bookmarks": 0, "skipped": 0, "refused": 0}
        # Adresses déjà présentes, par dossier : lues une fois, pas à chaque signet.
        known = {}

        def urls_of(target):
            if target.id not in known:
                known[target.id] = set(target.bookmark_ids.mapped("url"))
            return known[target.id]

        for event in events:
            kind = event[0]
            if kind == "folder":
                parent = stack[-1]
                title = event[1] or _("Sans titre")
                existing = parent.child_ids.filtered(lambda f, t=title: f.name == t)[:1]
                if existing:
                    last_folder = existing
                else:
                    last_folder = Folder.create({"name": title, "parent_id": parent.id})
                    stats["folders"] += 1
            elif kind == "open":
                # Le <DL> racine n'a pas de <H3> devant lui : il reste dans `folder`.
                stack.append(last_folder if event[1] and last_folder else stack[-1])
                last_folder = None
            elif kind == "close":
                if len(stack) > 1:
                    stack.pop()
            elif kind == "bookmark":
                parent = stack[-1]
                title, url = event[1], (event[2] or "").strip()
                if url in urls_of(parent):
                    stats["skipped"] += 1
                    continue
                try:
                    with self.env.cr.savepoint():
                        Bookmark.create({"name": title or url, "url": url,
                                         "folder_id": parent.id,
                                         "sequence": 10 + stats["bookmarks"]})
                    stats["bookmarks"] += 1
                    urls_of(parent).add(url)
                except ValidationError:
                    stats["refused"] += 1
        return stats

    def action_import(self):
        self.ensure_one()
        raw = base64.b64decode(self.file or b"")
        try:
            html = raw.decode("utf-8")
        except UnicodeDecodeError:
            html = raw.decode("latin-1")
        if "<dl" not in html.lower() and "<a " not in html.lower():
            raise UserError(_("Ce fichier n'est pas un export de signets (format Netscape)."))
        stats = self._import_html(html, self.folder_id)
        message = _(
            "%(folders)s dossier(s) et %(bookmarks)s signet(s) importés, %(skipped)s déjà "
            "présent(s), %(refused)s refusé(s) (adresse non acceptée).", **stats)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Import terminé"),
                "message": message,
                "type": "success" if not stats["refused"] else "warning",
                "sticky": bool(stats["refused"]),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
