from odoo import models, api

from . import brand_color_mixin as bc


class MailMail(models.Model):
    _inherit = 'mail.mail'

    def _replace_button_colors(self, html_content):
        """Replace old Odoo button colors with the active company's brand colors."""
        return bc.replace_button_colors(self.env, html_content)

    def _send(self, auto_commit=False, raise_exception=False, smtp_session=None, **kwargs):
        """Override to replace button colors in email body before sending."""
        for mail in self:
            if mail.body_html:
                mail.body_html = self._replace_button_colors(mail.body_html)
        return super()._send(
            auto_commit=auto_commit,
            raise_exception=raise_exception,
            smtp_session=smtp_session,
            **kwargs
        )

    @api.model_create_multi
    def create(self, vals_list):
        """Override to replace button colors when mail is created."""
        for vals in vals_list:
            if vals.get('body_html'):
                vals['body_html'] = self._replace_button_colors(vals['body_html'])
            if vals.get('body'):
                vals['body'] = self._replace_button_colors(vals['body'])
        return super().create(vals_list)

    def write(self, vals):
        """Override to replace button colors when mail is updated."""
        if vals.get('body_html'):
            vals['body_html'] = self._replace_button_colors(vals['body_html'])
        if vals.get('body'):
            vals['body'] = self._replace_button_colors(vals['body'])
        return super().write(vals)
