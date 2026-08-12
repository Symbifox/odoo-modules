from odoo import models, api

from . import brand_color_mixin as bc


class MailRenderMixin(models.AbstractModel):
    _inherit = 'mail.render.mixin'

    @api.model
    def _replace_button_colors(self, html_content):
        """Replace old Odoo button colors with the active company's brand colors."""
        return bc.replace_button_colors(self.env, html_content)

    @api.model
    def _render_template(self, template_src, model, res_ids, engine='inline_template',
                         add_context=None, options=None):
        """Override to replace button colors in rendered email templates."""
        result = super()._render_template(
            template_src, model, res_ids, engine=engine,
            add_context=add_context, options=options
        )

        # Replace colors in all rendered results
        if isinstance(result, dict):
            for res_id, content in result.items():
                if isinstance(content, str):
                    result[res_id] = self._replace_button_colors(content)
        elif isinstance(result, str):
            result = self._replace_button_colors(result)

        return result
