"""bf.partner.absence.suggestion — ce que la machine propose, et qu'un humain
accepte ou refuse.

**Pourquoi une suggestion et pas une absence.** Trois raisons, toutes mesurées
sur un corpus de courrier réel :

1. neuf répondeurs sur vingt-huit ne portent **aucune date lisible** ;
2. le filet de reconnaissance attrape des robots : `mailer-daemon` y est entré
   deux fois, et une ingestion silencieuse aurait mis un MAILER-DAEMON en
   vacances ;
3. une fiche fausse est pire qu'une fiche vide, parce qu'un avertissement faux
   se fait désarmer et emporte avec lui les avertissements justes.

C'est la doctrine déjà tenue ailleurs dans la maison : n'apparier que de
l'existant, n'entrer qu'en brouillon.

⚠️ Vie privée : rien du texte du répondeur n'est recopié ici. Une période, une
nature, au plus une relève, et un lien vers le courriel d'origine, qui reste
sous les règles de conservation de la boîte unifiée.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BfPartnerAbsenceSuggestion(models.Model):
    _name = "bf.partner.absence.suggestion"
    _description = "Absence proposed by an auto-reply"
    _order = "create_date desc, id desc"

    partner_id = fields.Many2one(
        comodel_name="res.partner", string="Contact",
        required=True, index=True, ondelete="cascade")
    bf_email_id = fields.Many2one(
        comodel_name="bf.email", string="Auto-reply received",
        ondelete="set null", index=True,
        help="The original email stays where it is: none of its text is "
             "copied here.")
    email_date = fields.Datetime(related="bf_email_id.date", string="Received "
                                                                    "on",
                                 store=True)
    email_subject = fields.Char(related="bf_email_id.subject", string="Subject")

    date_from = fields.Date(string="From")
    date_to = fields.Date(string="To (last day away)")
    nature = fields.Selection(
        selection=[
            ("vacation", "Holiday"),
            ("leave", "Leave"),
            ("closure", "Shutdown"),
            ("training", "Training or conference"),
            ("other", "Other"),
        ],
        string="Nature", default="vacation", required=True)
    backup_hint = fields.Char(
        string="Proposed stand-in",
        help="The address or number named in the auto-reply.")
    backup_partner_id = fields.Many2one(
        comodel_name="res.partner", string="Stand-in",
        compute="_compute_backup_partner_id", store=True, readonly=False,
        help="Matched on the address when it belongs to an existing "
             "record. Never created.")

    kind = fields.Selection(
        selection=[
            ("absence", "Absence"),
            ("acknowledgement", "Acknowledgement"),
        ],
        string="Kind", default="absence", required=True, index=True,
        help="An application or request acknowledgement carries the "
             "subject of an auto-reply without being one. It is rejected "
             "up front and kept for the record, rather than cluttering "
             "the pile.")
    detected_by = fields.Char(string="Recognised by", readonly=True)
    read_by = fields.Char(string="Dates read by", readonly=True)
    complete = fields.Boolean(string="Readable period",
                              compute="_compute_complete", store=True)

    state = fields.Selection(
        selection=[
            ("pending", "To decide"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
        ],
        string="State", default="pending", required=True, index=True)
    absence_id = fields.Many2one(
        comodel_name="bf.partner.absence", string="Absence recorded",
        readonly=True, ondelete="set null")
    company_id = fields.Many2one(
        comodel_name="res.company", string="Company",
        default=lambda self: self.env.company)

    _sql_constraints = [
        ("bf_absence_suggestion_email_uniq", "unique(bf_email_id)",
         "This email already produced an absence proposal."),
    ]

    @api.depends("date_from", "date_to")
    def _compute_complete(self):
        for suggestion in self:
            suggestion.complete = bool(
                suggestion.date_from and suggestion.date_to
                and suggestion.date_to >= suggestion.date_from)

    @api.depends("backup_hint")
    def _compute_backup_partner_id(self):
        """N'apparie que de l'existant, et jamais sur un nom : sur l'adresse.

        Un appariement par nom se trompe de personne, et se tromper de relève
        envoie le courriel du client à quelqu'un d'autre.
        """
        for suggestion in self:
            indice = (suggestion.backup_hint or "").strip()
            if not indice or "@" not in indice:
                suggestion.backup_partner_id = False
                continue
            trouve = self.env["res.partner"].search(
                [("email", "=ilike", indice)], limit=1)
            suggestion.backup_partner_id = trouve or False

    # ------------------------------------------------------------------
    # Les deux gestes
    # ------------------------------------------------------------------
    def action_accept(self):
        """Pose l'absence. Un chevauchement refuse bruyamment plutôt que de
        poser une seconde période qui rendrait le bandeau ambigu."""
        Absence = self.env["bf.partner.absence"]
        for suggestion in self:
            if suggestion.state != "pending":
                continue
            if not suggestion.complete:
                raise UserError(_(
                    "This proposal's period is incomplete: enter \"From\" "
                    "and \"To\" before accepting it."))
            absence = Absence.create({
                "partner_id": suggestion.partner_id.id,
                "date_from": suggestion.date_from,
                "date_to": suggestion.date_to,
                "nature": suggestion.nature,
                "backup_partner_id": suggestion.backup_partner_id.id or False,
                "backup_info": (suggestion.backup_hint
                                if not suggestion.backup_partner_id else False),
                "source": "autoreply",
            })
            suggestion.write({"state": "accepted", "absence_id": absence.id})
        return True

    def action_reject(self):
        self.filtered(lambda s: s.state == "pending").write({"state": "rejected"})
        return True

    def action_open_partner(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": self.partner_id.id,
            "view_mode": "form",
        }

    def action_open_email(self):
        self.ensure_one()
        if not self.bf_email_id:
            raise UserError(_("The original email is no longer available."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.email",
            "res_id": self.bf_email_id.id,
            "view_mode": "form",
        }
