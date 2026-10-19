# -*- coding: utf-8 -*-
"""Les dossiers de signets, et la portée qui décide qui les voit.

Une portée est portée par la RACINE d'un arbre : entreprise, département, projet ou
personne. Chaque sous-dossier et chaque signet recopie la cible de sa racine, pour
que les règles d'accès se lisent sur la fiche elle-même, sans remonter l'arbre.

🔴 Odoo ne vérifie les règles d'une écriture QUE sur l'état d'avant. Sans le
contrôle d'après (`_check_target_after_write`), une personne pourrait changer la
portée de son dossier perso en « entreprise » : la règle du propriétaire passe sur
l'état d'avant, et le dossier apparaît dans le navigateur de tout le personnel.
"""
import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

SCOPES = [
    ("company", "Entreprise"),
    ("department", "Équipe (département)"),
    ("project", "Équipe (projet)"),
    ("personal", "Perso"),
]

# La cible de chaque portée : le seul champ qui doit être rempli.
TARGET_FIELD = {
    "company": "company_id",
    "department": "department_id",
    "project": "project_id",
    "personal": "owner_id",
}
TARGET_FIELDS = ("scope", "company_id", "department_id", "project_id", "owner_id")

# Au-delà, `extension_tree` et `complete_name` récursent trop profond.
MAX_DEPTH = 20
# Ce que l'extension peut envoyer en une passe.
MAX_OPS = 500

# L'ordre des sections dans le navigateur.
SCOPE_ORDER = {"company": 0, "department": 1, "project": 2, "personal": 3}


class BookmarkFolder(models.Model):
    _name = "bf.bookmark.folder"
    _description = "Dossier de signets"
    _parent_store = True
    _order = "sequence, name, id"
    _rec_names_search = ["name", "complete_name"]

    name = fields.Char(string="Nom", required=True)
    complete_name = fields.Char(
        string="Chemin", compute="_compute_complete_name", store=True, recursive=True)
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(default=True)
    parent_id = fields.Many2one(
        "bf.bookmark.folder", string="Dossier parent", ondelete="cascade", index=True)
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("bf.bookmark.folder", "parent_id", string="Sous-dossiers")
    bookmark_ids = fields.One2many("bf.bookmark", "folder_id", string="Signets")
    bookmark_count = fields.Integer(string="Nombre de signets", compute="_compute_bookmark_count")

    scope = fields.Selection(SCOPES, string="Portée", required=True, index=True)
    company_id = fields.Many2one("res.company", string="Société", index=True)
    department_id = fields.Many2one("hr.department", string="Département", index=True)
    project_id = fields.Many2one("project.project", string="Projet", index=True)
    owner_id = fields.Many2one("res.users", string="Propriétaire", index=True)

    can_edit = fields.Boolean(string="Modifiable", compute="_compute_can_edit",
                              search="_search_can_edit")
    archived_from_browser = fields.Boolean(
        string="Supprimé depuis le navigateur", readonly=True, copy=False,
        help="Archivé par une suppression faite dans le navigateur. Restaurable.")
    archived_date = fields.Datetime(string="Supprimé le", readonly=True, copy=False)

    @api.depends("name", "parent_id.complete_name")
    def _compute_complete_name(self):
        for folder in self:
            if folder.parent_id:
                folder.complete_name = f"{folder.parent_id.complete_name} / {folder.name}"
            else:
                folder.complete_name = folder.name

    @api.depends_context("uid")
    def _compute_bookmark_count(self):
        counts = dict(self.env["bf.bookmark"]._read_group(
            [("folder_id", "in", self.ids)], ["folder_id"], ["__count"]))
        for folder in self:
            folder.bookmark_count = counts.get(folder, 0)

    @api.depends_context("uid")
    def _compute_can_edit(self):
        for folder in self:
            folder.can_edit = bool(folder.id) and folder.has_access("write")

    def _search_can_edit(self, operator, value):
        """Les dossiers où l'usager peut écrire : pour ne proposer que ceux-là.

        Le droit se lit par racine (toute une arborescence partage la cible de sa
        racine, donc les mêmes règles), puis s'étend à la descendance.
        """
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Recherche non prise en charge sur « Modifiable »."))
        roots = self.search([("parent_id", "=", False)])
        writable_roots = roots.filtered(lambda r: r.has_access("write"))
        ids = self.search([("id", "child_of", writable_roots.ids)]).ids if writable_roots else []
        positive = (operator == "=") == value
        return [("id", "in" if positive else "not in", ids)]

    # ------------------------------------------------------------------
    # La cible
    # ------------------------------------------------------------------

    def _target_vals(self):
        """La cible de ce dossier, à recopier telle quelle sur ses descendants."""
        self.ensure_one()
        return {
            "scope": self.scope,
            "company_id": self.company_id.id,
            "department_id": self.department_id.id,
            "project_id": self.project_id.id,
            "owner_id": self.owner_id.id,
        }

    @api.model
    def _normalize_target(self, vals):
        """Garde le seul champ de cible que la portée demande, et déduit la société.

        La société d'un dossier de département ou de projet est celle du département
        ou du projet, à défaut la société courante : c'est elle qui borne ce qu'un
        responsable des signets voit. Un dossier perso n'a pas de société.

        ⚠️ Lu avec les droits de l'appelant, jamais en sudo : qui ne peut pas lire le
        département ou le projet ne peut pas non plus y écrire, et un sudo ici
        rendait le nom d'un projet privé à qui ne le voit pas.
        """
        scope = vals.get("scope")
        if scope not in TARGET_FIELD:
            return vals
        keep = TARGET_FIELD[scope]
        for field in ("company_id", "department_id", "project_id", "owner_id"):
            if field != keep:
                vals[field] = False
        if scope == "company" and not vals.get("company_id"):
            vals["company_id"] = self.env.company.id
        elif scope == "personal" and not vals.get("owner_id"):
            vals["owner_id"] = self.env.uid
        elif scope == "department" and vals.get("department_id"):
            dept = self.env["hr.department"].browse(vals["department_id"])
            vals["company_id"] = dept.company_id.id or self.env.company.id
        elif scope == "project" and vals.get("project_id"):
            project = self.env["project.project"].browse(vals["project_id"])
            vals["company_id"] = project.company_id.id or self.env.company.id
        return vals

    @api.model
    def _default_root_name(self, vals):
        scope = vals.get("scope")
        if scope == "company":
            return self.env["res.company"].browse(vals["company_id"]).name
        if scope == "department" and vals.get("department_id"):
            return self.env["hr.department"].browse(vals["department_id"]).name
        if scope == "project" and vals.get("project_id"):
            return self.env["project.project"].browse(vals["project_id"]).name
        return _("Perso")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("parent_id"):
                parent = self.browse(vals["parent_id"])
                vals.update(parent._target_vals())
            else:
                vals.setdefault("scope", "personal")
                self._normalize_target(vals)
                if not vals.get("name"):
                    vals["name"] = self._default_root_name(vals)
        records = super().create(vals_list)
        # Après le contrôle de création d'Odoo : sinon la contrainte répondrait,
        # à qui n'a aucun droit, qu'une racine existe déjà pour telle cible.
        records._check_single_root()
        return records

    def write(self, vals):
        """🔴 Aucune clé de contexte ne dispense des contrôles.

        Une première version sautait le contrôle d'après quand le contexte portait
        un drapeau de propagation. Le contexte d'un appel RPC est fourni par
        l'appelant : n'importe quel employé pouvait le poser et publier un dossier
        dans la section Entreprise. La propagation passe maintenant par
        `_propagate_target`, qui écrit sans repasser par ici.
        """
        moves_target = bool(set(vals) & (set(TARGET_FIELDS) | {"parent_id"}))
        if not moves_target:
            return super().write(vals)
        # Chaque dossier peut changer de cible différemment (un nouveau parent la
        # dicte, une racine la reçoit), d'où l'écriture un par un.
        for folder in self:
            folder_vals = dict(vals)
            parent_id = folder_vals.get("parent_id", folder.parent_id.id)
            if parent_id:
                folder_vals.update(self.browse(parent_id)._target_vals())
            else:
                folder_vals.setdefault("scope", folder.scope)
                for field in ("company_id", "department_id", "project_id", "owner_id"):
                    folder_vals.setdefault(field, folder[field].id)
                self._normalize_target(folder_vals)
            super(BookmarkFolder, folder).write(folder_vals)
            folder._propagate_target()
        self._check_target_after_write()
        self._check_single_root()
        return True

    def _propagate_target(self):
        """Recopie la cible de ce dossier sur toute sa descendance, puis la contrôle."""
        self.ensure_one()
        descendants = self.search([("id", "child_of", self.id), ("id", "!=", self.id)])
        if descendants:
            super(BookmarkFolder, descendants).write(self._target_vals())
            descendants._check_target_after_write()

    def _check_target_after_write(self):
        """Refuse une écriture qui mène un dossier là où on n'a pas le droit d'écrire."""
        self.flush_recordset()
        self.invalidate_recordset()
        self.check_access("write")
        self.bookmark_ids._check_target_after_write()

    @api.constrains("parent_id")
    def _check_parent_cycle(self):
        if self._has_cycle():
            raise ValidationError(_("Un dossier ne peut pas se contenir lui-même."))

    @api.constrains("parent_id")
    def _check_depth(self):
        # Une profondeur sans borne fait tomber `extension_tree` (récursion) pour
        # tous ceux qui voient la section.
        for folder in self:
            # « 12/40/41/ » : trois dossiers, donc deux niveaux sous la racine.
            if folder.parent_path and folder.parent_path.count("/") - 1 > MAX_DEPTH:
                raise ValidationError(_(
                    "Trop de niveaux de dossiers (%s au plus).", MAX_DEPTH))

    @api.constrains(*TARGET_FIELDS, "parent_id")
    def _check_target(self):
        for folder in self:
            keep = TARGET_FIELD[folder.scope]
            if not folder[keep]:
                raise ValidationError(_("Un dossier de cette portée doit avoir une cible."))
            if folder.parent_id and folder._target_vals() != folder.parent_id._target_vals():
                raise ValidationError(_(
                    "Le dossier « %s » n'a pas la portée de son dossier parent.",
                    folder.name))

    def _check_single_root(self):
        """Une seule racine par cible. Appelé APRÈS les contrôles d'accès."""
        for folder in self.filtered(lambda f: not f.parent_id):
            field = TARGET_FIELD[folder.scope]
            twins = self.sudo().with_context(active_test=False).search_count([
                ("id", "!=", folder.id),
                ("parent_id", "=", False),
                ("scope", "=", folder.scope),
                (field, "=", folder[field].id),
            ])
            if twins:
                raise ValidationError(_(
                    "Il existe déjà un dossier racine pour cette cible. Ajoutez plutôt "
                    "un sous-dossier."))

    # ------------------------------------------------------------------
    # La racine d'entreprise
    # ------------------------------------------------------------------

    @api.model
    def _ensure_company_roots(self, companies=None):
        """Une racine d'entreprise par société, avec un premier signet vers Odoo.

        Sans elle, personne n'a rien d'imposé tant qu'un responsable n'a pas pensé
        à la créer, et l'extension n'a l'air de rien faire. Appelé à l'installation,
        à la montée qui l'introduit, et à la volée quand quelqu'un ouvre ses signets
        (une société neuve l'obtient donc au premier usage). Ne touche jamais une
        racine existante (active ou archivée).
        """
        Folder = self.sudo().with_context(active_test=False)
        if companies is None:
            # Seulement les sociétés qui sont la société principale d'au moins un
            # employé actif : une société d'archive (« ancienne », « avant
            # incorporation ») ferait sinon une section vide dans chaque barre.
            users = self.env["res.users"].sudo().search([("share", "=", False)])
            companies = users.company_id
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        created = Folder.browse()
        for company in companies:
            if Folder.search_count([("parent_id", "=", False), ("scope", "=", "company"),
                                    ("company_id", "=", company.id)]):
                continue
            root = Folder.create({"scope": "company", "company_id": company.id,
                                  "name": company.name})
            if base_url.startswith(("http://", "https://")):
                self.env["bf.bookmark"].sudo().create({
                    "name": _("Odoo de %s", company.name),
                    "url": base_url.rstrip("/") + "/odoo",
                    "folder_id": root.id,
                })
            created |= root
        return created

    # ------------------------------------------------------------------
    # La racine perso
    # ------------------------------------------------------------------

    @api.model
    def _personal_root(self):
        """La racine perso de l'usager courant, créée au besoin."""
        root = self.with_context(active_test=False).search([
            ("parent_id", "=", False),
            ("scope", "=", "personal"),
            ("owner_id", "=", self.env.uid),
        ], limit=1)
        if not root:
            root = self.create({"scope": "personal", "owner_id": self.env.uid})
        elif not root.active:
            root.active = True
        return root

    # ------------------------------------------------------------------
    # L'extension
    # ------------------------------------------------------------------

    @api.model
    def extension_tree(self):
        """L'arbre que l'extension recopie dans le navigateur.

        Une section par racine visible, dans l'ordre entreprise, départements,
        projets, perso. Le droit d'écrire se lit UNE fois par section : tous les
        dossiers d'une racine ont la même cible, donc les mêmes règles.
        """
        self._personal_root()
        self._ensure_company_roots(self.env.user.company_id)
        folders = self.search([])
        bookmarks = self.env["bf.bookmark"].search([("folder_id", "in", folders.ids)])
        by_parent = {}
        for folder in folders:
            by_parent.setdefault(folder.parent_id.id, []).append(folder)
        marks_by_folder = {}
        for mark in bookmarks:
            marks_by_folder.setdefault(mark.folder_id.id, []).append(mark)

        def node(folder, writable):
            items = [(f.sequence, 0, f.id, f) for f in by_parent.get(folder.id, [])]
            items += [(b.sequence, 1, b.id, b) for b in marks_by_folder.get(folder.id, [])]
            children = []
            for _seq, kind, _id, rec in sorted(items, key=lambda i: i[:3]):
                if kind == 0:
                    children.append(node(rec, writable))
                else:
                    children.append({"id": f"b{rec.id}", "kind": "bookmark",
                                     "title": rec.name, "url": rec.url})
            return {"id": f"f{folder.id}", "kind": "folder", "title": folder.name,
                    "children": children}

        roots = sorted(by_parent.get(False, []),
                       key=lambda f: (SCOPE_ORDER[f.scope], f.sequence, f.name, f.id))
        sections = []
        for root in roots:
            writable = root.has_access("write")
            section = node(root, writable)
            section.update({"scope": root.scope, "writable": writable})
            sections.append(section)
        body = json.dumps(sections, sort_keys=True, ensure_ascii=False)
        return {
            "schema": 1,
            "personal_root": f"f{self._personal_root().id}",
            "sections": sections,
            "hash": hashlib.sha256(body.encode()).hexdigest(),
        }

    @api.model
    def widget_data(self):
        """Ce que l'icône de la barre et la page Signets affichent.

        L'arbre de l'extension (mêmes droits), plus mes favoris et mes récents, et
        l'icône de chaque signet. Les icônes ne sont rendues que pour des signets
        que la personne vient de lire avec ses droits.
        """
        tree = self.extension_tree()
        Bookmark = self.env["bf.bookmark"]
        favorites, recents = Bookmark._favorites_and_recents()
        ids = set(favorites.ids) | set(recents.ids)

        def collect(node):
            if node["kind"] == "bookmark":
                ids.add(int(node["id"][1:]))
            for child in node.get("children", []):
                collect(child)

        for section in tree["sections"]:
            collect(section)
        marks = Bookmark.browse(list(ids))
        icons = marks._icons()
        fav_ids = set(favorites.ids)

        def decorate(node):
            if node["kind"] == "bookmark":
                rid = int(node["id"][1:])
                node["icon"] = icons.get(rid)
                node["favorite"] = rid in fav_ids
            for child in node.get("children", []):
                decorate(child)

        for section in tree["sections"]:
            decorate(section)

        def flat(bookmark):
            return {"id": f"b{bookmark.id}", "kind": "bookmark", "title": bookmark.name,
                    "url": bookmark.url, "icon": icons.get(bookmark.id),
                    "favorite": bookmark.id in fav_ids}

        tree["favorites"] = [flat(b) for b in favorites]
        tree["recents"] = [flat(b) for b in recents]
        return tree

    @api.model
    def _parse_node_id(self, node_id, kind=None):
        """« f12 » → dossier 12, « b7 » → signet 7. Refuse tout le reste.

        « Introuvable » et « pas à vous » rendent le MÊME message : sinon l'appel
        dirait quels identifiants existent chez les autres.
        """
        if not isinstance(node_id, str) or len(node_id) < 2 or not node_id[1:].isdigit():
            raise UserError(_("Identifiant de nœud invalide."))
        prefix, rid = node_id[0], int(node_id[1:])
        if prefix == "f" and kind in (None, "folder"):
            record = self.browse(rid)
        elif prefix == "b" and kind in (None, "bookmark"):
            record = self.env["bf.bookmark"].browse(rid)
        else:
            raise UserError(_("Identifiant de nœud invalide."))
        if not record.exists() or not record.has_access("read"):
            raise UserError(_("Nœud introuvable."))
        return record

    @api.model
    def _place(self, record, parent, index):
        """Met `record` à la position `index` parmi les enfants de `parent`."""
        siblings = [(f.sequence, 0, f.id, f) for f in parent.child_ids if f != record]
        siblings += [(b.sequence, 1, b.id, b) for b in parent.bookmark_ids if b != record]
        ordered = [s[3] for s in sorted(siblings, key=lambda s: s[:3])]
        if not isinstance(index, int) or index < 0 or index > len(ordered):
            index = len(ordered)
        ordered.insert(index, record)
        for position, sibling in enumerate(ordered):
            if sibling.sequence != (position + 1) * 10:
                sibling.sequence = (position + 1) * 10

    def _archive_from_browser(self):
        """Archive un dossier, toute sa descendance et leurs signets, en le marquant."""
        self.ensure_one()
        folders = self.search([("id", "child_of", self.id)])
        mark = {"active": False, "archived_from_browser": True,
                "archived_date": fields.Datetime.now()}
        folders.bookmark_ids.write(mark)
        folders.write(mark)

    def action_restore_from_browser(self):
        """Restaure ce que le navigateur a supprimé : le dossier, ses ancêtres
        archivés (sinon il resterait invisible sous un parent archivé), et ce qui a
        été archivé avec lui."""
        restore = {"active": True, "archived_from_browser": False, "archived_date": False}
        Folder = self.with_context(active_test=False)
        for folder in Folder.browse(self.ids):
            ancestors = Folder.search([("id", "parent_of", folder.id), ("active", "=", False)])
            ancestors.write(restore)
            batch = Folder.search([("id", "child_of", folder.id), ("active", "=", False),
                                   ("archived_from_browser", "=", True)])
            batch.write(restore)
            self.env["bf.bookmark"].with_context(active_test=False).search([
                ("folder_id", "in", batch.ids), ("active", "=", False),
                ("archived_from_browser", "=", True)]).write(restore)
        return True

    @api.model
    def extension_apply(self, ops):
        """Applique les changements faits dans le navigateur, un par un.

        Chaque opération a son point de reprise : une opération refusée (droit,
        adresse invalide, nœud disparu) n'emporte pas les autres. Le navigateur
        reçoit la liste des refus et resynchronise, ce qui remet en place ce qui
        n'est pas passé.
        """
        if not isinstance(ops, list):
            raise UserError(_("Les opérations doivent être une liste."))
        if len(ops) > MAX_OPS:
            raise UserError(_(
                "Trop de changements en une fois (%s au plus). Synchronisez plus souvent.",
                MAX_OPS))
        created, errors = {}, []
        Bookmark = self.env["bf.bookmark"]
        for position, op in enumerate(ops):
            try:
                with self.env.cr.savepoint():
                    if not isinstance(op, dict):
                        raise UserError(_("Opération invalide."))
                    action = op.get("op")
                    if action == "create":
                        parent_ref = created.get(op.get("parent"), op.get("parent"))
                        parent = self._parse_node_id(parent_ref, "folder")
                        title = (op.get("title") or "").strip() or _("Sans titre")
                        if op.get("kind") == "folder":
                            record = self.create({"name": title, "parent_id": parent.id})
                            node_id = f"f{record.id}"
                        elif op.get("kind") == "bookmark":
                            record = Bookmark.create({
                                "name": title, "url": op.get("url"),
                                "folder_id": parent.id})
                            node_id = f"b{record.id}"
                        else:
                            raise UserError(_("Genre de nœud inconnu."))
                        self._place(record, parent, op.get("index"))
                        if op.get("tmp"):
                            created[op["tmp"]] = node_id
                    elif action == "update":
                        record = self._parse_node_id(created.get(op.get("id"), op.get("id")))
                        vals = {}
                        if "title" in op:
                            vals["name"] = (op["title"] or "").strip() or _("Sans titre")
                        if "url" in op and record._name == "bf.bookmark":
                            vals["url"] = op["url"]
                        if vals:
                            record.write(vals)
                    elif action == "move":
                        record = self._parse_node_id(created.get(op.get("id"), op.get("id")))
                        parent = self._parse_node_id(
                            created.get(op.get("parent"), op.get("parent")), "folder")
                        field = "parent_id" if record._name == self._name else "folder_id"
                        if record[field] != parent:
                            record.write({field: parent.id})
                        self._place(record, parent, op.get("index"))
                    elif action == "delete":
                        record = self._parse_node_id(created.get(op.get("id"), op.get("id")))
                        if record._name == self._name and not record.parent_id:
                            raise UserError(_("Une section ne se supprime pas du navigateur."))
                        # Archivé, pas effacé : une passe fautive (conflit entre
                        # deux postes, défaut du moteur) se rattrape par « Archivés ».
                        record._archive_from_browser()
                    else:
                        raise UserError(_("Opération inconnue."))
                    self.env.flush_all()
            except AccessError:
                # Le message d'Odoo nomme les fiches refusées : pas ici.
                errors.append({"index": position, "message": _("Refusé : droit insuffisant.")})
            except (UserError, ValidationError, ValueError) as error:
                errors.append({"index": position, "message": str(error)[:200]})
        return {"created": created, "errors": errors}
