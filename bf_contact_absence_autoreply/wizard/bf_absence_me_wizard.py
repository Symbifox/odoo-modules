"""« Je m'absente » : un seul geste, toutes les conséquences.

Le préalable a été chiffré : trois gestes séparés étaient exigés avant qu'un
répondeur s'arme tout seul, et le résultat a été zéro enregistrement partout. Cet assistant est la réponse :
les dates, la relève, le ton, et c'est fini.

Ce qu'il pose, d'un coup :

- l'absence sur sa propre fiche (c'est elle qui commande, et c'est elle que les
  autres voient au moment de nous écrire) ;
- le répondeur pour la période, éteint tout seul à la fin ;
- le refus des invitations reçues pendant l'absence, si on le demande.

⚠️ Il n'y a pas de rappel « prendre des nouvelles » ici : cette activité est
faite pour le retour de quelqu'un d'AUTRE. Se la poser à soi-même le lendemain
de son propre retour n'aide personne.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.bf_absence_house_message import TONS


class BfAbsenceMeWizard(models.TransientModel):
    _name = "bf.absence.me.wizard"
    _description = "Je m'absente"

    date_from = fields.Date(
        string="Du",
        required=True,
        default=fields.Date.context_today,
        help="Premier jour d'absence, inclus.",
    )
    date_to = fields.Date(
        string="Au",
        required=True,
        default=lambda self: fields.Date.context_today(self) + timedelta(days=4),
        help="DERNIER jour d'absence, inclus. La fin est obligatoire : sans "
             "elle, un répondeur reste allumé et personne ne s'en aperçoit.",
    )
    date_return = fields.Date(
        string="De retour le",
        compute="_compute_date_return",
        readonly=True,
        help="Premier jour de présence, déduit de la date de fin.",
    )
    nature = fields.Selection(
        selection=lambda self: self.env["bf.partner.absence"]._fields[
            "nature"].selection,
        string="Nature",
        required=True,
        default="vacation",
    )
    backup_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Relève",
        help="À qui écrire pendant l'absence. Son nom est écrit en clair dans "
             "le message, pas laissé à un marqueur.",
    )
    backup_info = fields.Char(
        string="Relève (texte)",
        help="Quand la relève n'a pas de fiche : une adresse, un numéro, "
             "« la réception ».",
    )
    autoreply = fields.Boolean(
        string="Répondre automatiquement à mon courrier",
        default=True,
    )
    autoreply_tone = fields.Selection(
        selection=TONS,
        string="Ton du message",
        default=lambda self: self.env["bf.absence.house.message"]._default_tone(),
    )
    decline_meetings = fields.Boolean(
        string="Refuser les invitations reçues pour cette période",
        default=True,
    )
    preview_html = fields.Html(
        string="Ce qui partira",
        compute="_compute_preview",
        sanitize=False,
        readonly=True,
    )

    @api.depends("date_to")
    def _compute_date_return(self):
        for wizard in self:
            wizard.date_return = (
                wizard.date_to + timedelta(days=1) if wizard.date_to else False)

    @api.depends("autoreply", "autoreply_tone", "backup_partner_id",
                 "backup_info")
    def _compute_preview(self):
        """Montrer le texte AVANT d'armer.

        Un répondeur est la seule chose qu'on écrit sans jamais la relire : on
        ne reçoit pas son propre courrier sortant. L'aperçu est donc la seule
        occasion de voir la phrase.
        """
        Maison = self.env["bf.absence.house.message"]
        for wizard in self:
            if not wizard.autoreply:
                wizard.preview_html = False
                continue
            gabarit = self.env["bf.email.absence"].sudo().search([
                ("user_id", "=", self.env.user.id), ("is_template", "=", True),
            ], limit=1)
            if gabarit and gabarit.reply_ids:
                wizard.preview_html = gabarit.reply_ids[0].body_html
                continue
            maison = Maison._for_tone(wizard.autoreply_tone)
            if not maison:
                wizard.preview_html = False
                continue
            nom, contact = wizard._backup_pair()
            wizard.preview_html = maison._rendered_body(nom, contact)

    def _backup_pair(self):
        self.ensure_one()
        if self.backup_partner_id:
            return (self.backup_partner_id.name or "",
                    self.backup_partner_id.email or self.backup_info or "")
        if self.backup_info:
            return self.backup_info, ""
        return "", ""

    def action_confirm(self):
        self.ensure_one()
        partner = self.env.user.partner_id
        if not partner:
            raise UserError(_(
                "Votre compte n'a pas de fiche contact : il n'y a nulle part "
                "où poser l'absence."))
        if self.date_to < self.date_from:
            raise UserError(_("La fin de l'absence précède son début."))
        absence = self.env["bf.partner.absence"].create({
            "partner_id": partner.id,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "nature": self.nature,
            "backup_partner_id": self.backup_partner_id.id or False,
            "backup_info": self.backup_info or False,
            # Le rappel de reprise vise le retour de quelqu'un d'autre.
            "reminder": False,
            "autoreply": self.autoreply,
            "autoreply_tone": self.autoreply_tone,
            "autoreply_decline_meetings": self.decline_meetings,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Mon absence"),
            "res_model": "bf.partner.absence",
            "res_id": absence.id,
            "view_mode": "form",
            "target": "current",
        }
