"""Le statut devient l'interrupteur du répondeur.

Deux modèles disaient déjà la même chose sans se parler :
`bf.partner.absence` sait qu'une personne est absente du 16 au 18, et
`bf.email.absence` sait répondre à sa place. Le premier n'écrivait à personne,
le second attendait qu'on remplisse un deuxième formulaire. Ce fichier relie
les deux, dans un seul sens : **le statut commande, le répondeur suit**.

🔴 **Le fuseau tranche, et il n'est pas celui du lecteur.** Le statut porte des
DATES (jours pleins, inclusifs), le répondeur porte des INSTANTS. Entre deux
fuseaux éloignés il y a plus d'une demi-journée : « absent du 16 au 18 » lu dans
le mauvais fuseau allume ou éteint le répondeur presque un jour à côté. C'est le fuseau de la **personne absente** qui fait foi, pas celui de
celle qui saisit, ni celui du serveur.

⚠️ Rien ne s'arme pour un contact. L'absence d'un client reste une
connaissance ; seule l'absence de quelqu'un qui a une boîte chez nous produit
une réponse automatique.
"""

import logging
from datetime import datetime, time

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .bf_absence_house_message import TONS

_logger = logging.getLogger(__name__)


class BfPartnerAbsence(models.Model):
    _inherit = "bf.partner.absence"

    absent_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Person in the organisation",
        compute="_compute_absent_user",
        help="The internal user attached to the record. It decides "
             "whether a responder is possible: a contact's absence never "
             "writes to anyone.",
    )
    autoreply = fields.Boolean(
        string="Reply automatically while away",
        help="Arms this person's responder for the period, and switches "
             "it off at the end. The message is theirs if they wrote one, "
             "the house one otherwise.",
    )
    autoreply_tone = fields.Selection(
        selection=TONS,
        string="Message tone",
        default=lambda self: self.env["bf.absence.house.message"]._default_tone(),
        help="Only used when the message comes from the house. A personal "
             "\"message type\", if there is one, wins.",
    )
    autoreply_decline_meetings = fields.Boolean(
        string="Decline invitations received while away",
        default=True,
        help="Marks attendance as declined for invitations falling inside "
             "the period. The event is never modified or deleted, only "
             "the answer is.",
    )
    email_absence_id = fields.Many2one(
        comodel_name="bf.email.absence",
        string="Responder",
        readonly=True,
        ondelete="set null",
        copy=False,
        help="The period created among the absence replies. It holds the "
             "send log.",
    )
    autoreply_state = fields.Selection(
        related="email_absence_id.state",
        string="Responder state",
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Qui est de la maison
    # ------------------------------------------------------------------
    @api.depends("partner_id")
    def _compute_absent_user(self):
        """L'utilisateur interne de la fiche, s'il y en a un.

        ⚠️ En `sudo` : un employé ordinaire ne lit pas `res.users`, et sans ça
        le champ lèverait à la simple ouverture d'une fiche contact. Rien de ce
        qui est lu ne sort d'ici, seul un identifiant est retenu.
        """
        for absence in self:
            partner = absence.partner_id
            if not partner:
                absence.absent_user_id = False
                continue
            users = partner.sudo().user_ids.filtered(
                lambda u: not u.share and u.active)
            absence.absent_user_id = users[:1]

    # ------------------------------------------------------------------
    # Les bornes, dans le fuseau de la personne absente
    # ------------------------------------------------------------------
    def _autoreply_timezone(self):
        """Le fuseau qui fait foi pour cette absence.

        Celui de la personne absente d'abord. À défaut, celui de la société,
        puis UTC. Jamais celui du lecteur : deux personnes qui regardent la
        même absence depuis deux continents doivent voir la même période.
        """
        self.ensure_one()
        noms = [
            self.absent_user_id.sudo().tz,
            self.company_id.partner_id.tz if self.company_id else False,
            self.env.company.partner_id.tz,
        ]
        for nom in noms:
            if not nom:
                continue
            try:
                return pytz.timezone(nom)
            except pytz.UnknownTimeZoneError:
                _logger.warning(
                    "bf_contact_absence_autoreply : fuseau %r illisible, "
                    "absence %s", nom, self.id)
        return pytz.utc

    def _autoreply_window(self):
        """Les deux instants UTC que couvre la période."""
        self.ensure_one()
        tz = self._autoreply_timezone()
        debut = tz.localize(datetime.combine(self.date_from, time(0, 0, 0)))
        fin = tz.localize(datetime.combine(self.date_to, time(23, 59, 59)))
        return (debut.astimezone(pytz.utc).replace(tzinfo=None),
                fin.astimezone(pytz.utc).replace(tzinfo=None))

    # ------------------------------------------------------------------
    # Le texte
    # ------------------------------------------------------------------
    def _autoreply_backup(self):
        """Le nom et le moyen de joindre la relève, écrits en clair.

        Rendus ici et pas par un marqueur : `{releve}` vide laisse « écrivez à
        () » dans un courriel qui part chez un client.
        """
        self.ensure_one()
        partner = self.backup_partner_id
        if partner:
            return partner.name or "", partner.email or self.backup_info or ""
        if self.backup_info:
            return self.backup_info, ""
        return "", ""

    def _autoreply_reply_commands(self):
        """Les lignes de message à poser sur le répondeur, ou None.

        Ordre : le « message type » de la personne d'abord (c'est le sien, il
        gagne), le message de maison ensuite. **Aucun des deux, rien ne
        s'arme** : sans texte, on ne répond pas.
        """
        self.ensure_one()
        Absence = self.env["bf.email.absence"].sudo()
        gabarit = Absence.search([
            ("user_id", "=", self.absent_user_id.id),
            ("is_template", "=", True),
        ], limit=1)
        if gabarit and gabarit.reply_ids:
            return [(0, 0, ligne._copy_vals()) for ligne in gabarit.reply_ids]
        # 🔴 Dans la langue de la personne ABSENTE : c'est en son nom que le
        # texte part. Lu dans la langue de qui a cliqué (un administrateur
        # anglophone), ou sans langue (le travail planifié), il partirait dans
        # la langue source.
        maison = self.env["bf.absence.house.message"]._for_tone(
            self.autoreply_tone).with_context(
                lang=self.absent_user_id.lang or "en_US")
        if not maison:
            return None
        nom, contact = self._autoreply_backup()
        return [(0, 0, {
            "name": maison.name,
            "body_html": maison._rendered_body(nom, contact),
        })]

    # ------------------------------------------------------------------
    # L'armement
    # ------------------------------------------------------------------
    def _autoreply_vals(self):
        self.ensure_one()
        debut, fin = self._autoreply_window()
        delegue = self.backup_partner_id.sudo().user_ids.filtered(
            lambda u: not u.share and u.active)[:1]
        return {
            "name": self.with_context(
                lang=self.absent_user_id.lang or "en_US").display_name,
            "user_id": self.absent_user_id.id,
            "company_id": (self.company_id or self.absent_user_id.company_id).id,
            "date_from": debut,
            "date_to": fin,
            "active": True,
            # La relève est NOMMÉE, pas mise aux commandes : confier la boîte
            # de quelqu'un est un geste explicite, pas un effet de bord.
            "delegate_user_id": delegue.id if delegue else False,
            "route_to_delegate": False,
            "decline_meetings": self.autoreply_decline_meetings,
        }

    def _autoreply_clash(self, debut, fin):
        """Une autre période vivante du même titulaire sur les mêmes instants.

        La contrainte de `bf.email.absence` lèverait de toute façon, mais son
        message parle d'un modèle que la personne qui coche la case n'a jamais
        ouvert. Mieux vaut nommer le conflit dans le vocabulaire d'ici.
        """
        self.ensure_one()
        return self.env["bf.email.absence"].sudo().search([
            ("user_id", "=", self.absent_user_id.id),
            ("is_template", "=", False),
            ("active", "=", True),
            ("date_from", "<", fields.Datetime.to_string(fin)),
            ("date_to", ">", fields.Datetime.to_string(debut)),
            ("id", "!=", self.email_absence_id.id or 0),
        ], limit=1)

    def _check_autoreply_owner(self):
        """Seul le titulaire (ou un administrateur courriel) arme sa boîte.

        🔴 L'ACL du socle laisse n'importe quel employé noter l'absence de
        n'importe qui, et c'est voulu : savoir qu'un client part est un geste
        ordinaire de la relation client. Mais **armer un répondeur, c'est
        écrire à des clients sous le nom de quelqu'un d'autre**. Les deux
        gestes n'ont pas le même poids et ne peuvent pas avoir la même
        permission. La question ne se pose pas dans une base à une personne ;
        elle se pose dès la deuxième.
        """
        self.ensure_one()
        if self.env.su or self.env.user == self.absent_user_id:
            return
        if self.env.user.has_group("bf_email_management.group_email_admin"):
            return
        if self.env.user.has_group("base.group_system"):
            return
        raise UserError(_(
            "You can record %(nom)s's absence, but not arm their "
            "responder: an automatic message goes out under their name, "
            "from their mailbox. That box belongs to them, or to an email "
            "administrator.",
            nom=self.absent_user_id.name,
        ))

    def _sync_autoreply(self):
        """Pose, déplace ou éteint le répondeur de chaque absence."""
        Absence = self.env["bf.email.absence"].sudo()
        for absence in self:
            veut = (
                absence.autoreply
                and absence.active
                and absence.absent_user_id
                and absence.date_from
                and absence.date_to
            )
            if not veut:
                if absence.email_absence_id and absence.email_absence_id.active:
                    absence.email_absence_id.sudo().write({"active": False})
                    _logger.info(
                        "bf_contact_absence_autoreply : répondeur %s éteint "
                        "(absence %s)", absence.email_absence_id.id, absence.id)
                continue

            absence._check_autoreply_owner()
            debut, fin = absence._autoreply_window()
            clash = absence._autoreply_clash(debut, fin)
            if clash:
                raise UserError(_(
                    "%(nom)s already has an active absence reply on these "
                    "dates: \"%(autre)s\". End it before arming another, "
                    "or two messages would compete for the reply.",
                    nom=absence.absent_user_id.name, autre=clash.name,
                ))

            vals = absence._autoreply_vals()
            if absence.email_absence_id:
                absence.email_absence_id.sudo().write(vals)
                _logger.info(
                    "bf_contact_absence_autoreply : répondeur %s repris de "
                    "l'absence %s (%s à %s)",
                    absence.email_absence_id.id, absence.id, debut, fin)
                continue

            lignes = absence._autoreply_reply_commands()
            if lignes is None:
                raise UserError(_(
                    "No absence message is available: neither yours nor "
                    "the house one. No text, no reply."
                ))
            vals["reply_ids"] = lignes
            absence.email_absence_id = Absence.create(vals)
            _logger.info(
                "bf_contact_absence_autoreply : répondeur %s armé depuis "
                "l'absence %s (%s à %s)",
                absence.email_absence_id.id, absence.id, debut, fin)

    # ------------------------------------------------------------------
    # Écritures
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        absences = super().create(vals_list)
        absences._sync_autoreply()
        return absences

    def write(self, vals):
        res = super().write(vals)
        if {"autoreply", "autoreply_tone", "autoreply_decline_meetings",
            "date_from", "date_to", "date_return", "partner_id", "active",
            "backup_partner_id", "backup_info", "nature"} & set(vals):
            self._sync_autoreply()
        return res

    def unlink(self):
        # Le répondeur porte le journal des envois : on l'éteint, on ne
        # l'efface pas. Même doctrine que les absences venues de l'agenda.
        repondeurs = self.email_absence_id.sudo()
        res = super().unlink()
        repondeurs.filtered("active").write({"active": False})
        return res

    # ------------------------------------------------------------------
    # Boutons
    # ------------------------------------------------------------------
    def action_open_email_absence(self):
        self.ensure_one()
        if not self.email_absence_id:
            raise UserError(_("No responder is armed for this absence."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Absence reply"),
            "res_model": "bf.email.absence",
            "res_id": self.email_absence_id.id,
            "view_mode": "form",
            "target": "current",
        }
