from odoo import models


class SurveySurvey(models.Model):
    _inherit = 'survey.survey'

    def action_send_survey(self):
        """Override to remove the Odoo notification layout wrapper.

        The Blue Fox survey invitation template is self-contained (full HTML
        with header, footer, accent bars) so it must NOT be wrapped in
        mail.mail_notification_light which adds a second Odoo-branded shell.
        """
        result = super().action_send_survey()
        ctx = dict(result.get('context', {}))
        ctx.pop('default_email_layout_xmlid', None)
        result['context'] = ctx
        return result
