from odoo import fields, models


class MeetingRecord(models.Model):
    """Durcissement de la duplication, requis par l'exposition au portail.

    Le portail montre un compte rendu dès que `report_state == 'sent'` et que
    le partenaire figure dans `report_recipient_ids`. Or ces champs étaient
    copiables : dupliquer un compte rendu déjà envoyé produisait une copie
    marquée « envoyé », destinataires intacts, alors qu'aucun courriel n'était
    jamais parti pour elle. Elle serait apparue au portail du client.

    `copy=False` remet la copie à l'état brouillon, sans destinataires ni date
    d'envoi — ce qu'un nouveau document doit être. Le durcissement vit ici, et
    non dans bf_meeting, parce que c'est ce module-ci qui transforme ces champs
    en critère de visibilité client.
    """

    _inherit = 'meeting.record'

    report_state = fields.Selection(copy=False)
    report_sent_date = fields.Datetime(copy=False)
    report_recipient_ids = fields.Many2many(copy=False)
