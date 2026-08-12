from odoo import api, fields, models

# Ceiling mirrored on the Claude bridge side (its own request model). The
# instructions are pasted into the `claude -p` prompt, so an unbounded field
# would push the real skill body out of the model's attention budget.
MAX_INSTRUCTIONS = 4000


class MeetingRefineWizard(models.TransientModel):
    """Free-text instructions collected before launching /refine-meeting.

    The refine pass is always complete — these notes tell the skill what to
    look at *in addition*, they never narrow its scope. Typically used after
    the automatic pass left something behind: a mangled acronym, a first name
    the transcription got wrong, a meeting routed to the wrong client.
    """

    _name = 'meeting.refine.wizard'
    _description = 'Raffiner un compte rendu avec TentaClaude'

    meeting_id = fields.Many2one(
        'meeting.record',
        string='Compte rendu',
        required=True,
        ondelete='cascade',
        readonly=True,
    )
    instructions = fields.Text(
        string='Points à regarder',
        help="Facultatif. Éléments à vérifier, corrections connues, contexte "
             "utile. Une passe complète est faite de toute façon : ce texte "
             "s'y ajoute, il ne la remplace pas.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'meeting_id' in fields_list and not res.get('meeting_id'):
            if self.env.context.get('active_model') == 'meeting.record':
                res['meeting_id'] = self.env.context.get('active_id')
        return res

    def action_launch(self):
        self.ensure_one()
        instructions = (self.instructions or '').strip()[:MAX_INSTRUCTIONS]
        action = self.meeting_id.action_refine_meeting(instructions=instructions)
        # `display_notification` ne referme PAS le dialogue de l'assistant : le
        # client action retourne `params.next` et rien d'autre (voir
        # web/static/src/webclient/actions/client_actions.js). Sans ce chaînage,
        # le raffinage part mais la fenêtre reste ouverte et il faut cliquer
        # « Annuler » pour s'en débarrasser — ce qui laisse croire à un échec.
        if isinstance(action, dict) and action.get('tag') == 'display_notification':
            action.setdefault('params', {})['next'] = {
                'type': 'ir.actions.act_window_close',
            }
        return action
