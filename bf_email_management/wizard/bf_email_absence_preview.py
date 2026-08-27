"""bf.email.absence.preview — voir la réponse avant de partir.

Un répondeur se juge à ce qu'il envoie, et la seule façon de le savoir était
de partir en vacances. L'aperçu prend une adresse d'expéditeur, choisit le
message que le moteur choisirait pour elle, et le rend — placeholders
remplacés, liens joints, dans la langue du contact s'il en a une.

Rien n'est envoyé et rien n'est écrit : l'aperçu construit une fiche
`bf.email` **en mémoire** (`new()`), jamais en base.
"""

from odoo import _, api, fields, models


class BfEmailAbsencePreview(models.TransientModel):
    _name = "bf.email.absence.preview"
    _description = "Aperçu de la réponse d'absence"

    absence_id = fields.Many2one(
        comodel_name="bf.email.absence",
        string="Absence",
        required=True,
        readonly=True,
    )
    sender = fields.Char(
        string="Si ce contact écrivait",
        required=True,
        default="quelquun@exemple.com",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="…ou ce contact connu",
        help="Renseigné, sa catégorie et sa langue servent au choix du "
             "message. C'est ce qui distingue « Clients » de « Tout le monde ».",
    )
    subject = fields.Char(string="Objet du message reçu", default="Une question")
    reply_name = fields.Char(string="Message retenu", readonly=True)
    lang = fields.Char(string="Langue de la réponse", readonly=True)
    body = fields.Html(string="Réponse", readonly=True, sanitize=False)
    blocked_reason = fields.Char(string="Ne partirait pas", readonly=True)

    @api.onchange("sender", "partner_id", "subject")
    def _onchange_render(self):
        for wiz in self:
            wiz._render()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._render()
        return records

    def _render(self):
        for wiz in self:
            absence = wiz.absence_id
            if not absence:
                continue
            probe = wiz._probe_email()
            reply = absence._pick_reply(probe)
            wiz.reply_name = reply.name if reply else False
            wiz.lang = absence._target_lang(probe)
            wiz.body = (absence._render_body(probe, reply) if reply else
                        "<p class='text-muted'>%s</p>" % _(
                            "Aucun message ne vise cet expéditeur — "
                            "ajoutez un message sans condition pour couvrir "
                            "tout le monde."))
            wiz.blocked_reason = absence._blocked_reason(probe) or False

    def _probe_email(self):
        """A bf.email that exists only in memory, never in the table."""
        self.ensure_one()
        partner = self.partner_id
        sender = self.sender or "quelquun@exemple.com"
        if partner and partner.email:
            sender = partner.email
        owner = self.absence_id.user_id
        own = list(self.env["bf.email"]._get_self_addresses(user=owner))
        return self.env["bf.email"].new({
            "date": fields.Datetime.now(),
            "email_from": sender,
            "email_to": own[0] if own else (owner.email or ""),
            "subject": self.subject or "",
            "direction": "in",
            "source": "imap",
            "user_id": owner.id,
            "partner_id": partner.id if partner else False,
            "body_preview": "",
            "raw_headers": "From: %s" % sender,
        })
