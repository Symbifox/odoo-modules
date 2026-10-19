"""L'import depuis les contacts écarte les personnes inscrites, sans tout faire échouer."""
from markupsafe import Markup

from odoo import _, models

_PRIVATE_KEYS = ("bf_outreach_optout_filter", "bf_outreach_optout_skipped")


class BfOutreachTargetImportWizard(models.TransientModel):
    _inherit = "bf.outreach.target.import.wizard"

    def action_import(self):
        skipped = []
        action = super(
            BfOutreachTargetImportWizard,
            self.with_context(bf_outreach_optout_filter=True,
                              bf_outreach_optout_skipped=skipped),
        ).action_import()
        # L'action renvoyée recopie le contexte : ces deux clés ne doivent pas
        # partir au navigateur, sinon toute création faite ensuite depuis la vue
        # écarterait en silence au lieu de prévenir.
        if isinstance(action, dict) and isinstance(action.get("context"), dict):
            action["context"] = {k: v for k, v in action["context"].items()
                                 if k not in _PRIVATE_KEYS}
        if sum(skipped):
            self.campaign_id.message_post(body=Markup("<p>%s</p>") % _(
                "%(count)s contact(s) écarté(s) en plus : inscrit(s) à la liste "
                "d'opposition au démarchage.",
                count=sum(skipped),
            ))
        return action
