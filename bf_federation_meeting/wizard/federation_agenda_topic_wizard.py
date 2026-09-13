from odoo import _, fields, models


class FederationAgendaTopicWizard(models.TransientModel):
    _name = "federation.agenda.topic.wizard"
    _description = "Proposer un sujet à l'ordre du jour d'un pair"

    agenda_id = fields.Many2one("meeting.agenda", string="Ordre du jour", required=True, readonly=True)
    peer_name = fields.Char(string="Chez", related="agenda_id.federation_peer_id.name", readonly=True)
    name = fields.Char(string="Sujet", required=True)
    description = fields.Html(string="Ce qu'il faut en dire",
                              help="Réduit en texte au départ : aucun balisage ne traverse.")

    def action_send(self):
        self.ensure_one()
        self.agenda_id._federation_send_topic(self.name, self.description)
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Sujet proposé"),
            "message": _("« %s » part chez %s, à examiner.") % (self.name, self.peer_name or ""),
            "type": "success", "next": {"type": "ir.actions.act_window_close"}}}
