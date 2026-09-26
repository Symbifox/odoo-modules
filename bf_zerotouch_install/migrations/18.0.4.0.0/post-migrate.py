"""Remove the legacy bf.zerotouch.tenant model after its data moved to Policy.

The per-tenant config was folded into bf.policy.org. bf_policy is a dependency,
so its 18.0.2.0.0 post-migrate has already copied every legacy row onto the
matching org by the time this runs. Here we tear the model down.

This is SQL-only on purpose: the model class no longer exists in the registry,
so any ORM access to ``bf.zerotouch.tenant`` (env.ref on the seed record,
ir.model.unlink, or Odoo's own end-of-load stale-xmlid cleanup) would raise
KeyError. We therefore:

  1. Delete the ir_model_data rows that point at records OF the dead model (the
     ``tenant_bf`` seed) so Odoo's _process_end does not try to ORM-unlink them.
  2. Drop the physical table.
  3. Remove the model metadata (access, constraints, fields, ir.model) so the
     server does not log "Model ... has no table" on every boot.

The view / action / menu records (ir.ui.view, ir.actions.act_window, ir.ui.menu,
ir.model.access) live on still-valid models, so Odoo's normal stale-xmlid
cleanup unlinks them safely — we leave those to it. Everything here is IF EXISTS
/ guarded, so the migration is idempotent.
"""


def migrate(cr, version):
    # 1) Records OF the dead model: drop their xmlid pointers before Odoo's
    #    end-of-load cleanup tries (and fails) to ORM-unlink them.
    cr.execute("DELETE FROM ir_model_data WHERE model = 'bf.zerotouch.tenant'")

    # 2) Drop the table — data already lives on bf.policy.org.
    cr.execute("DROP TABLE IF EXISTS bf_zerotouch_tenant CASCADE")

    # 3) Tear down the model metadata, children first (FKs to ir_model).
    cr.execute("SELECT id FROM ir_model WHERE model = 'bf.zerotouch.tenant'")
    row = cr.fetchone()
    if row:
        mid = row[0]
        cr.execute("DELETE FROM ir_model_access WHERE model_id = %s", (mid,))
        cr.execute("DELETE FROM ir_model_constraint WHERE model = %s", (mid,))
        # xmlids of this model's fields, then the fields themselves.
        cr.execute(
            "DELETE FROM ir_model_data WHERE model = 'ir.model.fields' "
            "AND res_id IN (SELECT id FROM ir_model_fields WHERE model_id = %s)",
            (mid,),
        )
        cr.execute("DELETE FROM ir_model_fields WHERE model_id = %s", (mid,))
        # leftover xmlids of this module pointing at constraints we just dropped.
        cr.execute(
            "DELETE FROM ir_model_data WHERE module = 'bf_zerotouch_install' "
            "AND model = 'ir.model.constraint'"
        )
        cr.execute("DELETE FROM ir_model_data WHERE model = 'ir.model' AND res_id = %s", (mid,))
        cr.execute("DELETE FROM ir_model WHERE id = %s", (mid,))
