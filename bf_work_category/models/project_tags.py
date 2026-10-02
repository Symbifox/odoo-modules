from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ProjectTags(models.Model):
    _inherit = "project.tags"

    is_work_category = fields.Boolean(
        string="Work category",
        help="Use this label as a work category: projects, tasks and the records of every bridged module "
             "(timesheets, Gen conversations, notes, meetings, emails) can then be filtered, grouped and "
             "exported by it. On a label carried by many projects, ticking or unticking it updates all their "
             "records at once and can take a while.",
    )
    work_category_sequence = fields.Integer(
        string="Category order", default=10,
        help="When a record carries several category labels, the lowest order wins.",
    )

    def _bf_work_category_may_set(self):
        return self.env.su or self.env.user.has_group("project.group_project_manager")

    def _bf_work_category_check_settings(self, vals_list):
        # Turning a label into a category (or back), reordering or deleting a
        # category reclassifies every record under it: project administrators
        # only, although employees may create, rename and delete plain labels.
        if self._bf_work_category_may_set():
            return
        is_category = bool(self.filtered("is_work_category"))
        for vals in vals_list:
            if vals.get("is_work_category") or ("is_work_category" in vals and is_category):
                raise AccessError(_("Only project administrators can make a label a work category, or stop it being one."))
            if "work_category_sequence" in vals and is_category:
                raise AccessError(_("Only project administrators can change the order of work categories."))

    @api.model_create_multi
    def create(self, vals_list):
        self._bf_work_category_check_settings(vals_list)
        records = super().create(vals_list)
        # Defaults (a default_is_work_category in the context) are only known now.
        if not self._bf_work_category_may_set() and records.filtered("is_work_category"):
            raise AccessError(_("Only project administrators can make a label a work category, or stop it being one."))
        return records

    def write(self, vals):
        self._bf_work_category_check_settings([vals])
        return super().write(vals)

    def unlink(self):
        if not self._bf_work_category_may_set() and self.filtered("is_work_category"):
            raise AccessError(_("Only project administrators can delete a work category."))
        return super().unlink()

    def action_bf_work_category_recompute(self):
        if not self._bf_work_category_may_set():
            raise AccessError(_("Only project administrators can recompute work categories."))
        cron = self.env.ref("bf_work_category.ir_cron_work_category_recompute", raise_if_not_found=False)
        if cron and cron.sudo().active:
            cron.sudo()._trigger()
            message = _("Work categories are being recomputed in the background. "
                        "On a large database this takes a few minutes.")
        else:
            # The scheduled action is switched off: a trigger would never run.
            counts = self.env["bf.work.category.mixin"]._bf_work_category_recompute_all()
            message = _("Work categories recomputed: %s records updated.", sum(counts.values()))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "info", "message": message},
        }

    def _bf_work_category_cron(self):
        self.env["bf.work.category.mixin"].sudo()._bf_work_category_recompute_all(commit=True)
