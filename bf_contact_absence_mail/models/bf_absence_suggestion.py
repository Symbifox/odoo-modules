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
    _description = "Absence proposée par un répondeur"
    _order = "create_date desc, id desc"

    partner_id = fields.Many2one(
        comodel_name="res.partner", string="Contact",
        required=True, index=True, ondelete="cascade")
    bf_email_id = fields.Many2one(
        comodel_name="bf.email", string="Répondeur reçu",
        ondelete="set null", index=True,
        help="Le courriel d'origine reste à sa place : rien de son texte "
             "n'est recopié ici.")
    email_date = fields.Datetime(related="bf_email_id.date", string="Reçu le",
                                 store=True)
    email_subject = fields.Char(related="bf_email_id.subject", string="Objet")

    date_from = fields.Date(string="Du")
    date_to = fields.Date(string="Au (dernier jour absent)")
    nature = fields.Selection(
        selection=[
            ("vacation", "Vacances"),
            ("leave", "Congé"),
            ("closure", "Fermeture"),
            ("training", "Formation ou congrès"),
            ("other", "Autre"),
        ],
        string="Nature", default="vacation", required=True)
    backup_hint = fields.Char(
        string="Relève proposée",
        help="L'adresse ou le numéro nommé dans le répondeur.")
    backup_partner_id = fields.Many2one(
        comodel_name="res.partner", string="Relève",
        compute="_compute_backup_partner_id", store=True, readonly=False,
        help="Apparié sur l'adresse quand elle correspond à une fiche "
             "existante. Jamais créé.")

    kind = fields.Selection(
        selection=[
            ("absence", "Absence"),
            ("acknowledgement", "Accusé de réception"),
        ],
        string="Genre", default="absence", required=True, index=True,
        help="Un accusé de réception de candidature ou de demande porte "
             "l'objet d'un répondeur sans en être un. Il est refusé d'office "
             "et gardé pour la trace, plutôt que d'encombrer la pile.")
    detected_by = fields.Char(string="Reconnu par", readonly=True)
    read_by = fields.Char(string="Dates lues par", readonly=True)
    complete = fields.Boolean(string="Période lisible",
                              compute="_compute_complete", store=True)

    state = fields.Selection(
        selection=[
            ("pending", "À décider"),
            ("accepted", "Acceptée"),
            ("rejected", "Refusée"),
        ],
        string="État", default="pending", required=True, index=True)
    absence_id = fields.Many2one(
        comodel_name="bf.partner.absence", string="Absence posée",
        readonly=True, ondelete="set null")
    company_id = fields.Many2one(
        comodel_name="res.company", string="Société",
        default=lambda self: self.env.company)

    _sql_constraints = [
        ("bf_absence_suggestion_email_uniq", "unique(bf_email_id)",
         "Ce courriel a déjà produit une proposition d'absence."),
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
                    "La période de cette proposition est incomplète : "
                    "saisir « Du » et « Au » avant de l'accepter."))
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
            raise UserError(_("Le courriel d'origine n'est plus disponible."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.email",
            "res_id": self.bf_email_id.id,
            "view_mode": "form",
        }
