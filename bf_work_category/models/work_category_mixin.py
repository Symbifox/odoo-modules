from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import SQL, split_every

ORIGINS = [
    ("manual", "Overridden"),
    ("label", "Label"),
    ("task", "Task"),
    ("project", "Project"),
    ("linked", "Linked record"),
]
ORIGIN_BY_MODEL = {"project.task": "task", "project.project": "project"}
FOLLOWED_MODELS = {"project.task": "work_category_task_id", "project.project": "work_category_project_id"}

# Sources of a record attached to another one by res_model / res_id.
LINKED_SOURCES = (
    ("record", "work_category_task_id"),
    ("record", "work_category_project_id"),
    ("linked", "res_model", "res_id"),
)

# The stored bookkeeping of the category. Only the ORM's own computations write
# it: a user write or an import cannot set a category that does not resolve.
BOOKKEEPING = frozenset({"work_category_id", "work_category_origin", "work_category_task_id", "work_category_project_id"})

# Projects before tasks, tasks before the records that follow them.
RECOMPUTE_FIRST = ("project.project", "project.task")
BATCH_SIZE = 1000


class BfWorkCategoryMixin(models.AbstractModel):
    """Give a model a work category that can be filtered, grouped and exported.

    A work category is a project label flagged ``is_work_category``. The
    category of a record is resolved and stored: first the category forced on
    the record itself, then each source the model declares in
    ``_bf_work_category_sources``, strongest first:

    - ``("tags", "tag_ids")``: the record's own category labels, lowest order first;
    - ``("record", "project_id")``: the resolved category of a many2one target
      that carries this mixin;
    - ``("linked", "res_model", "res_id")``: the category of a generic linked
      record that carries this mixin, read when the link changes.

    Storing the category is bookkeeping, not an edit: it never changes the
    record's ``write_date`` or ``write_uid``.
    """

    _name = "bf.work.category.mixin"
    _description = "Work category mixin"

    _bf_work_category_sources = ()

    work_category_manual_id = fields.Many2one(
        "project.tags", string="Override category", ondelete="set null", index="btree_not_null",
        domain=[("is_work_category", "=", True)],
        help="Wins over the category found on the labels, the task, the project or the linked record.",
    )
    work_category_id = fields.Many2one(
        "project.tags", string="Work category", compute="_compute_work_category",
        store=True, index="btree_not_null", ondelete="set null",
    )
    work_category_origin = fields.Selection(
        ORIGINS, string="Category origin", compute="_compute_work_category", store=True,
    )

    def _bf_work_category_depends(self):
        depends = ["work_category_manual_id", "work_category_manual_id.is_work_category"]
        for source in self._bf_work_category_sources:
            kind = source[0]
            if kind == "tags":
                depends += [source[1], f"{source[1]}.is_work_category", f"{source[1]}.work_category_sequence"]
            elif kind == "record":
                depends.append(f"{source[1]}.work_category_id")
            elif kind == "linked":
                depends += [source[1], source[2]]
                if "user_id" in self._fields:
                    depends.append("user_id")
        return depends

    @api.depends(lambda self: self._bf_work_category_depends())
    def _compute_work_category(self):
        # Only the fields the resolution reads: prefetching every column of a
        # large email table at install time exhausts the memory.
        for record in self.with_context(prefetch_fields=False):
            category, origin = record._bf_work_category_resolve()
            record.work_category_id = category
            record.work_category_origin = origin

    @api.constrains("work_category_manual_id")
    def _check_work_category_manual(self):
        for record in self:
            if record.work_category_manual_id and not record.work_category_manual_id.is_work_category:
                raise ValidationError(_("The override category must be a label marked as a work category."))

    def write(self, vals):
        # On create the ORM recomputes these readonly fields anyway; a write
        # would store them as given.
        if not self.env.su and BOOKKEEPING.intersection(vals):
            vals = {key: value for key, value in vals.items() if key not in BOOKKEEPING}
        return super().write(vals)

    def _write_multi(self, vals_list):
        quiet = [bool(vals) and vals.keys() <= BOOKKEEPING for vals in vals_list]
        if not (self._log_access and any(quiet)):
            return super()._write_multi(vals_list)
        # The ORM stamps write_uid / write_date on every computed field it
        # flushes. For the category that would make one label change look like
        # an edit of every task, timesheet, note and email under it: mobile note
        # edits refused as conflicts, "last modified" orders and waiting times
        # reset. Records whose only change is their category get the same UPDATE
        # as the ORM, without the two log columns; real edits go the usual way.
        loud = [(record, vals) for record, vals, q in zip(self, vals_list, quiet) if not q]
        if loud:
            records = self.browse([record.id for record, _vals in loud])
            super(BfWorkCategoryMixin, records)._write_multi([vals for _record, vals in loud])
        table = SQL.identifier(self._table)
        updates = defaultdict(list)
        for record, vals, q in zip(self, vals_list, quiet):
            if q:
                fnames, row = zip(*sorted(vals.items()))
                updates[fnames].append(record._ids + row)
        for fnames, rows in updates.items():
            columns = [SQL.identifier(fname) for fname in fnames]
            assignments = [
                SQL('%s = "__tmp".%s::%s', column, column, SQL(self._fields[fname].column_type[1]))
                for fname, column in zip(fnames, columns)
            ]
            for sub_rows in split_every(BATCH_SIZE, rows):
                self.env.execute_query(SQL(
                    'UPDATE %s SET %s FROM (VALUES %s) AS "__tmp"("id", %s) WHERE %s."id" = "__tmp"."id"',
                    table, SQL(", ").join(assignments), SQL(", ").join(sub_rows), SQL(", ").join(columns), table,
                ))

    def _bf_work_category_resolve(self):
        self.ensure_one()
        if self.work_category_manual_id.is_work_category:
            return self.work_category_manual_id, "manual"
        for source in self._bf_work_category_sources:
            kind = source[0]
            if kind == "tags":
                labels = self[source[1]].filtered("is_work_category")
                if labels:
                    return labels.sorted(lambda tag: (tag.work_category_sequence, tag.id))[0], "label"
            elif kind == "record":
                target = self[source[1]]
                if target.work_category_id:
                    return target.work_category_id, ORIGIN_BY_MODEL.get(target._name, "linked")
            elif kind == "linked":
                target = self._bf_work_category_linked_record(source[1], source[2])
                if target and target.work_category_id:
                    return target.work_category_id, "linked"
        return self.env["project.tags"], False

    def _bf_work_category_owner(self):
        """The user whose rights decide which linked record this one may follow:
        its user, else its creator. Stored categories are computed as superuser,
        so following a link the owner cannot read would tell them the category
        of a record they cannot open, and that it exists."""
        self.ensure_one()
        if "user_id" in self._fields and self._fields["user_id"].comodel_name == "res.users" and self.user_id:
            return self.user_id
        # A record being typed in a form has no creator yet: the person typing.
        return self.create_uid or self.env.user

    def _bf_work_category_readable(self, records, owner):
        """The records the owner may read, in the current environment."""
        if not records or owner._is_superuser():
            return records
        # Under the owner's own companies: the caller's active companies (all of
        # them, with several ticked) are not the owner's, and Odoo would refuse
        # the whole operation instead of answering.
        as_owner = records.with_user(owner).with_context(allowed_company_ids=owner.company_ids.ids)
        return records.browse(as_owner._filtered_access("read").ids)

    def _bf_work_category_linked_record(self, model_field, id_field):
        """Linked record that carries a category, other than a task or a project
        (those are followed through their own stored link), if the owner may read it."""
        model_name, res_id = self[model_field], self[id_field]
        if not model_name or not res_id or model_name in FOLLOWED_MODELS or model_name not in self.env:
            return None
        Model = self.env[model_name]
        if Model._abstract or not Model._auto or "work_category_id" not in Model._fields:
            return None
        target = Model.with_context(active_test=False).browse(int(res_id)).exists()
        return self._bf_work_category_readable(target, self._bf_work_category_owner()) or None

    def _bf_work_category_store(self, values):
        """Write {record id: {field: value}} where it differs from what is stored,
        as bookkeeping (no write_date). Returns the ids of the records changed."""
        fnames = list(BOOKKEEPING.intersection(self._fields))
        records = self.browse(list(values))
        records.invalidate_recordset(fnames)
        changed, vals_list = [], []
        for record in records:
            vals = {}
            for fname, value in values[record.id].items():
                stored = record[fname]
                if self._fields[fname].type == "many2one":
                    stored = stored.id
                if (stored or False) != (value or False):
                    vals[fname] = value or None
            if vals:
                changed.append(record.id)
                vals_list.append(vals)
        if changed:
            self.browse(changed)._write_multi(vals_list)
            self.browse(changed).invalidate_recordset(fnames)
        return set(changed)

    def _bf_work_category_refresh(self):
        """Store the category these records should have, where it differs.
        Returns the ids of the records changed."""
        changed = self._bf_work_category_refresh_links()
        values = {}
        for record in self.with_context(prefetch_fields=False):
            category, origin = record._bf_work_category_resolve()
            values[record.id] = {"work_category_id": category.id, "work_category_origin": origin}
        return changed | self._bf_work_category_store(values)

    def _bf_work_category_refresh_links(self):
        return set()

    @api.model
    def _bf_work_category_recompute_all(self, commit=False):
        """Bring every stored category up to date, on every model that carries
        the mixin, in batches, writing only what differs.

        Safety net for what dependencies cannot follow: a record linked by
        res_model / res_id to something other than a task or a project whose
        category changed. Run weekly by a scheduled action, and on demand from a
        category label. Returns {model: records changed}."""
        if not self.env.su and not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(_("Only project administrators can recompute work categories."))
        self.env.flush_all()
        names = [
            name for name in self.pool.descendants(["bf.work.category.mixin"], "_inherit")
            if not self.env[name]._abstract and self.env[name]._auto
        ]
        rank = {name: index for index, name in enumerate(RECOMPUTE_FIRST)}
        names.sort(key=lambda name: (
            rank.get(name, len(rank)), "work_category_task_id" in self.env[name]._fields, name))
        counts = {}
        for name in names:
            Model = self.env[name].sudo().with_context(active_test=False, prefetch_fields=False)
            counts[name] = 0
            for ids in split_every(BATCH_SIZE, Model.search([], order="id").ids):
                counts[name] += len(Model.browse(ids)._bf_work_category_refresh())
                if commit:
                    self.env.cr.commit()
        return counts


class BfWorkCategoryLinkedMixin(models.AbstractModel):
    """Work category of a record attached to another one by res_model / res_id
    (Gen conversations, notes, emails).

    Tasks and projects are followed through two stored links, so a change of
    their category reaches the attached records. Other linked records that
    carry a category are read when the link itself changes, and by the weekly
    recompute. A link is followed only if the record's owner may read its
    target. The links are bookkeeping for administrators only: they would
    otherwise show the name of a task or a project the reader cannot open."""

    _name = "bf.work.category.linked.mixin"
    _inherit = "bf.work.category.mixin"
    _description = "Work category of a linked record"

    _bf_work_category_sources = LINKED_SOURCES

    work_category_task_id = fields.Many2one(
        "project.task", string="Category task", compute="_compute_work_category_links",
        store=True, index="btree_not_null", ondelete="set null", groups="base.group_system",
    )
    work_category_project_id = fields.Many2one(
        "project.project", string="Category project", compute="_compute_work_category_links",
        store=True, index="btree_not_null", ondelete="set null", groups="base.group_system",
    )

    def _bf_work_category_link_values(self):
        """{record id: {link field: id or False}} from res_model / res_id, for
        the tasks and projects the record's owner may read."""
        records = self.with_context(prefetch_fields=False)
        wanted = defaultdict(set)
        for record in records:
            if record.res_model in FOLLOWED_MODELS and record.res_id:
                wanted[record.res_model, record._bf_work_category_owner()].add(int(record.res_id))
        readable = {}
        for (name, owner), ids in wanted.items():
            found = self.env[name].with_context(active_test=False).browse(list(ids)).exists()
            readable[name, owner] = set(self._bf_work_category_readable(found, owner).ids)
        values = {}
        for record in records:
            owner = record._bf_work_category_owner()
            values[record.id] = {
                fname: int(record.res_id) if (
                    record.res_model == name and record.res_id
                    and int(record.res_id) in readable.get((name, owner), ())) else False
                for name, fname in FOLLOWED_MODELS.items()
            }
        return values

    @api.depends(lambda self: ["res_model", "res_id"] + (["user_id"] if "user_id" in self._fields else []))
    def _compute_work_category_links(self):
        values = self._bf_work_category_link_values()
        for record in self:
            for fname, value in values[record.id].items():
                record[fname] = value

    def _bf_work_category_refresh_links(self):
        return self._bf_work_category_store(self._bf_work_category_link_values())
