import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class ProjectDocumentVersion(models.Model):
    _inherit = "project.document.version"

    @api.model_create_multi
    def create(self, vals_list):
        versions = super().create(vals_list)
        versions.document_id._bf_check_linkable(versions.attachment_id | versions.attachment_ids)
        return versions

    def write(self, vals):
        if self.env.su or not {"attachment_id", "attachment_ids"} & set(vals):
            return super().write(vals)
        # Per record: a file already linked to one version proves nothing for the others.
        before = {v.id: v.attachment_id | v.attachment_ids for v in self.sudo()}
        result = super().write(vals)
        added = set()
        for version in self.sudo():
            added.update(set((version.attachment_id | version.attachment_ids).ids) - set(before[version.id].ids))
        self.env["project.document"]._bf_check_linkable(self.env["ir.attachment"].browse(sorted(added)))
        return result

    def action_release(self):
        result = super().action_release()
        self._bf_export_queue_on_release()
        return result

    def _bf_export_queue_on_release(self):
        """Queue one export per template set to follow publications.

        The export itself runs later, from the queue: a publication never waits
        for a ZIP to be built, and never fails because a target is down.
        """
        documents = self.mapped("document_id")
        if not documents:
            return
        Template = self.env["bf.document.export.template"].sudo()
        Run = self.env["bf.document.export.run"].sudo()
        queued = Run
        for template in Template.search([("deploy_mode", "=", "on_release")]):
            if not template.user_id:
                _logger.warning("Registry export template %s follows publications without an "
                                "« Export as » person: no export queued.", template.code)
                continue
            in_scope = documents.sudo().filtered_domain(template._document_domain(for_release=True))
            if not in_scope:
                continue
            if Run.search_count([("template_id", "=", template.id), ("state", "=", "queued")]):
                continue
            queued |= Run.create({
                "template_id": template.id,
                "trigger": "release",
                "user_id": template.user_id.id,
            })
        if queued:
            queued._queue()
