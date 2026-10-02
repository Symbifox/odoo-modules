from odoo.tools import SQL, split_every

CATEGORY_COLUMNS = {"work_category_id": "int4", "work_category_origin": "varchar"}
LINK_COLUMNS = {"work_category_task_id": "int4", "work_category_project_id": "int4"}
FOLLOWED_MODELS = ("project.task", "project.project")


def bf_work_category_prepare_columns(env, table, linked=False):
    """Create the stored computed columns before the ORM does.

    On a new stored computed field, Odoo computes it on every existing record in
    one transaction, which is slow on a large table. It leaves an existing column
    alone: the values are then filled by bf_work_category_initialise, only where
    there is something to store."""
    columns = dict(CATEGORY_COLUMNS, **(LINK_COLUMNS if linked else {}))
    for name, kind in columns.items():
        env.cr.execute(SQL(
            "ALTER TABLE %s ADD COLUMN IF NOT EXISTS %s %s", SQL.identifier(table), SQL.identifier(name), SQL(kind)))


def bf_work_category_initialise(env, model_name):
    """Fill a newly bridged model, in batches, as bookkeeping.

    Records attached to a task or a project get their links (a link is kept
    only if the record's owner may read its target). Every record gets its
    category, but only when some label already is a work category: otherwise
    they are all empty. Then the category origins of this model get their
    translations: they belong to bf_work_category, whose .po was loaded before
    they existed."""
    Model = env[model_name].sudo().with_context(active_test=False, prefetch_fields=False)
    if env["project.tags"].sudo().search_count([("is_work_category", "=", True)]):
        domain = []
    elif "work_category_task_id" in Model._fields:
        domain = [("res_model", "in", list(FOLLOWED_MODELS))]
    else:
        domain = None
    if domain is not None:
        for ids in split_every(1000, Model.search(domain, order="id").ids):
            Model.browse(ids)._bf_work_category_refresh()
    bf_work_category_load_translations(env)


def bf_work_category_load_translations(env):
    env["ir.module.module"].sudo().search([("name", "=", "bf_work_category")])._update_translations(overwrite=False)


def pre_init_hook(env):
    for table in ("project_project", "project_task"):
        bf_work_category_prepare_columns(env, table)
