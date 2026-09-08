# -*- coding: utf-8 -*-
"""L'occasion : ce qu'on souligne, quand, et pour qui.

Le type est un champ, pas un modèle par cas. Un module qui code
« anniversaire » en dur se refait au premier départ à souligner ; celui-ci
porte déjà la bienvenue, le départ, la retraite et les condoléances.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

TYPES = [
    ("birthday", "Anniversaire"),
    ("work_anniversary", "Anniversaire d'embauche"),
    ("welcome", "Bienvenue"),
    ("farewell", "Départ"),
    ("retirement", "Retraite"),
    ("congratulations", "Félicitations"),
    ("recovery", "Rétablissement"),
    ("condolence", "Condoléances"),
    ("other", "Autre"),
]

# Les types que le cron fabrique tout seul à partir d'un consentement. Les
# autres se créent à la main, parce qu'aucune date de la base ne dit qu'un
# collègue vient d'avoir un enfant.
TYPES_RECURRENTS = ("birthday", "work_anniversary")

EMOJI = {
    "birthday": "🎂",
    "work_anniversary": "🎉",
    "welcome": "👋",
    "farewell": "🧳",
    "retirement": "🌅",
    "congratulations": "🎊",
    "recovery": "🌿",
    "condolence": "🕊️",
    "other": "✨",
}

# Les jalons d'ancienneté qui méritent une carte. Souligner la 3e année de
# tout le monde tous les ans use l'attention ; ces cinq-là ne l'usent pas.
JALONS_ANCIENNETE = (1, 5, 10, 15, 20, 25, 30, 35, 40)


class CelebrationOccasion(models.Model):
    _name = "bf.celebration.occasion"
    _description = "Occasion à souligner"
    _inherit = ["mail.thread"]
    _order = "date, id"

    name = fields.Char(string="Intitulé", compute="_compute_name", store=True)
    occasion_type = fields.Selection(
        TYPES, string="Type", required=True, default="birthday", index=True)
    date = fields.Date(string="Date", required=True, index=True, tracking=True)

    employee_id = fields.Many2one(
        "hr.employee", string="Personne soulignée", ondelete="cascade",
        index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Contact souligné", ondelete="cascade",
        help="Pour souligner un jalon chez un client plutôt qu'un collègue.")
    person_name = fields.Char(
        string="Nom", compute="_compute_person_name", store=True)

    years = fields.Integer(
        string="Nombre d'années",
        help="Pour l'ancienneté seulement. L'âge n'est jamais calculé ni "
             "affiché nulle part dans ce module.")

    organizer_id = fields.Many2one(
        "res.users", string="Personne qui organise", tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company)

    board_ids = fields.One2many(
        "bf.celebration.board", "occasion_id", string="Tableaux de vœux")
    board_count = fields.Integer(compute="_compute_board_count")

    state = fields.Selection(
        [
            ("upcoming", "À venir"),
            ("done", "Passée"),
            ("skipped", "Sans suite"),
        ],
        default="upcoming", required=True, index=True, tracking=True)

    allow_board = fields.Boolean(
        string="Tableau permis", default=True,
        help="Faux quand la personne a dit « oui, mais sans tableau ».")

    reminder_sent = fields.Boolean(string="Rappel envoyé", copy=False)
    calendar_event_id = fields.Many2one(
        "calendar.event", string="Entrée d'agenda", copy=False,
        ondelete="set null")

    _sql_constraints = [
        (
            "occurrence_uniq",
            "unique(employee_id, occasion_type, date)",
            "Cette occasion est déjà au calendrier pour cette date.",
        ),
    ]

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------

    @api.depends("employee_id", "partner_id")
    def _compute_person_name(self):
        for occ in self:
            occ.person_name = (
                occ.employee_id.name or occ.partner_id.name or "")

    @api.depends("occasion_type", "person_name", "years")
    def _compute_name(self):
        for occ in self:
            libelle = dict(TYPES).get(occ.occasion_type, "")
            if occ.occasion_type == "work_anniversary" and occ.years:
                libelle = _("%(n)s ans chez nous", n=occ.years)
            occ.name = "%s %s — %s" % (
                EMOJI.get(occ.occasion_type, "✨"),
                occ.person_name or _("Sans nom"),
                libelle,
            )

    def _compute_board_count(self):
        groupes = self.env["bf.celebration.board"]._read_group(
            [("occasion_id", "in", self.ids)],
            groupby=["occasion_id"], aggregates=["__count"])
        compte = {occ.id: n for occ, n in groupes}
        for occ in self:
            occ.board_count = compte.get(occ.id, 0)

    # ------------------------------------------------------------------
    # Agenda
    # ------------------------------------------------------------------

    def _miroir_agenda_actif(self):
        """Le miroir d'agenda est ÉTEINT tant que personne ne l'allume.

        ⚠️ Un `Boolean` avec `config_parameter` ne stocke pas False : décocher
        SUPPRIME la clé. L'état sûr doit donc être celui de l'absence, et ici
        l'état sûr est « ne rien écrire dans un agenda » : une entrée d'agenda
        se synchronise vers le téléphone et le CalDAV, hors de portée d'un
        retrait de consentement fait plus tard.
        """
        param = self.env["ir.config_parameter"].sudo()
        return param.get_param("bf_celebrations.calendar_mirror") == "True"

    def _poser_evenement_agenda(self):
        """Une entrée journée entière, sans convive, donc sans invitation.

        `no_mail_to_attendees` et `dont_notify` sont les deux clés que le
        cœur d'Odoo lui-même emploie dans `calendar_recurrence` : sans elles,
        créer l'événement enverrait un courriel d'invitation à la personne
        qu'on essaie justement de surprendre.
        """
        if not self._miroir_agenda_actif():
            return
        Event = self.env["calendar.event"].sudo().with_context(
            no_mail_to_attendees=True,
            dont_notify=True,
            mail_create_nolog=True,
            mail_notrack=True,
        )
        for occ in self:
            if occ.calendar_event_id or not occ.allow_board:
                # « Sans tableau » veut dire discret : discret ne va pas
                # dans un agenda qui se synchronise ailleurs.
                continue
            proprietaire = occ.organizer_id or occ._organisateur_par_defaut()
            if not proprietaire:
                continue
            occ.calendar_event_id = Event.create({
                "name": occ.name,
                "start_date": occ.date,
                "stop_date": occ.date,
                "allday": True,
                "show_as": "free",
                "privacy": "confidential",
                "user_id": proprietaire.id,
                "partner_ids": [(6, 0, [proprietaire.partner_id.id])],
                "description": _(
                    "Posé par le module Célébrations. Retiré tout seul si la "
                    "personne change d'avis."),
            })

    def _supprimer_evenement_agenda(self):
        evenements = self.mapped("calendar_event_id")
        if evenements:
            evenements.sudo().with_context(
                dont_notify=True, no_mail_to_attendees=True).unlink()

    def unlink(self):
        self._supprimer_evenement_agenda()
        return super().unlink()

    # ------------------------------------------------------------------
    # Qui organise
    # ------------------------------------------------------------------

    def _organisateur_par_defaut(self):
        """Le gestionnaire de la personne, sinon celui réglé, sinon rien.

        ⚠️ `get_param` rend False quand la clé est absente, et `int(False)`
        vaut 0 : un `browse(0)` rendrait un enregistrement vide qui se lit
        comme un usager valide jusqu'à la première écriture.
        """
        self.ensure_one()
        gestionnaire = self.employee_id.parent_id.user_id
        if gestionnaire:
            return gestionnaire
        brut = self.env["ir.config_parameter"].sudo().get_param(
            "bf_celebrations.default_organizer_id")
        try:
            uid = int(brut or 0)
        except (TypeError, ValueError):
            uid = 0
        if uid:
            usager = self.env["res.users"].sudo().browse(uid).exists()
            if usager:
                return usager
        return self.env["res.users"]

    # ------------------------------------------------------------------
    # Génération, à partir des consentements seulement
    # ------------------------------------------------------------------

    @api.model
    def _cron_generer(self):
        horizon = self._horizon_jours()
        aujourdhui = fields.Date.context_today(self)
        limite = aujourdhui + relativedelta(days=horizon)
        crees = 0
        crees += self._generer_anniversaires(aujourdhui, limite)
        crees += self._generer_anciennetes(aujourdhui, limite)
        self._cloturer_passees(aujourdhui)
        _logger.info("Célébrations : %s occasion(s) posée(s).", crees)
        return crees

    @api.model
    def _horizon_jours(self):
        brut = self.env["ir.config_parameter"].sudo().get_param(
            "bf_celebrations.horizon_days")
        try:
            return max(1, int(brut or 60))
        except (TypeError, ValueError):
            return 60

    @api.model
    def _generer_anniversaires(self, aujourdhui, limite):
        profils = self.env["bf.celebration.profile"].sudo().search([
            ("consent", "in", ("full", "quiet")),
            ("celebration_month", "!=", False),
            ("employee_id.active", "=", True),
        ])
        valeurs = []
        for profil in profils:
            date = profil._prochaine_occurrence(aujourdhui)
            if not date or date > limite:
                continue
            if self.sudo().search_count([
                    ("employee_id", "=", profil.employee_id.id),
                    ("occasion_type", "=", "birthday"),
                    ("date", "=", date)]):
                continue
            valeurs.append({
                "occasion_type": "birthday",
                "date": date,
                "employee_id": profil.employee_id.id,
                "company_id": (
                    profil.employee_id.company_id.id or self.env.company.id),
                "allow_board": profil.consent == "full",
            })
        return self._creer_et_poser(valeurs)

    @api.model
    def _generer_anciennetes(self, aujourdhui, limite):
        profils = self.env["bf.celebration.profile"].sudo().search([
            ("consent", "in", ("full", "quiet")),
            ("share_work_anniversary", "=", True),
            ("employee_id.active", "=", True),
        ])
        valeurs = []
        for profil in profils:
            embauche = self._date_embauche(profil.employee_id)
            if not embauche:
                continue
            for annee in (aujourdhui.year, aujourdhui.year + 1):
                try:
                    date = embauche.replace(year=annee)
                except ValueError:  # 29 février
                    date = embauche.replace(year=annee, day=28)
                if date < aujourdhui or date > limite:
                    continue
                anciennete = annee - embauche.year
                if anciennete not in JALONS_ANCIENNETE:
                    continue
                if self.sudo().search_count([
                        ("employee_id", "=", profil.employee_id.id),
                        ("occasion_type", "=", "work_anniversary"),
                        ("date", "=", date)]):
                    continue
                valeurs.append({
                    "occasion_type": "work_anniversary",
                    "date": date,
                    "years": anciennete,
                    "employee_id": profil.employee_id.id,
                    "company_id": (
                        profil.employee_id.company_id.id
                        or self.env.company.id),
                    "allow_board": profil.consent == "full",
                })
        return self._creer_et_poser(valeurs)

    @api.model
    def _date_embauche(self, employe):
        """`first_contract_date` si `hr_contract` est là, sinon `create_date`.

        Le cœur d'Odoo emploie exactement ce repli : `_get_new_hire_field`
        de `hr.employee.base` rend `create_date` en édition communautaire
        sans contrats. On ne suppose donc pas la présence du module.
        """
        if "first_contract_date" in employe._fields:
            date = employe.sudo().first_contract_date
            if date:
                return date
        cree = employe.sudo().create_date
        return fields.Date.to_date(cree) if cree else False

    @api.model
    def _creer_et_poser(self, valeurs):
        if not valeurs:
            return 0
        occasions = self.sudo().create(valeurs)
        for occ in occasions:
            occ.organizer_id = occ._organisateur_par_defaut()
        occasions._poser_evenement_agenda()
        return len(occasions)

    @api.model
    def _cloturer_passees(self, aujourdhui):
        passees = self.sudo().search([
            ("date", "<", aujourdhui), ("state", "=", "upcoming")])
        if passees:
            passees.write({"state": "done"})

    # ------------------------------------------------------------------
    # Le rappel, qui est le vrai produit
    # ------------------------------------------------------------------

    @api.model
    def _cron_rappeler(self):
        """Dix jours avant, un message à qui organise, avec un bouton.

        Un calendrier que personne ne regarde ne fabrique pas de cartes.
        C'est ce rappel, pas la liste, qui fait exister la fonction.
        """
        param = self.env["ir.config_parameter"].sudo()
        try:
            jours = max(0, int(param.get_param(
                "bf_celebrations.reminder_days") or 10))
        except (TypeError, ValueError):
            jours = 10
        aujourdhui = fields.Date.context_today(self)
        echeance = aujourdhui + relativedelta(days=jours)
        gabarit = self.env.ref(
            "bf_celebrations.mail_template_rappel_organisateur",
            raise_if_not_found=False)
        a_rappeler = self.sudo().search([
            ("state", "=", "upcoming"),
            ("allow_board", "=", True),
            ("reminder_sent", "=", False),
            ("date", "<=", echeance),
            ("date", ">=", aujourdhui),
            ("board_ids", "=", False),
        ])
        for occ in a_rappeler:
            organisateur = occ.organizer_id or occ._organisateur_par_defaut()
            if not organisateur:
                continue
            if gabarit:
                gabarit.with_context(
                    organisateur=organisateur,
                ).send_mail(
                    occ.id,
                    email_values={
                        "email_to": organisateur.email_formatted,
                        # ⚠️ Jamais de destinataire calculé depuis les abonnés :
                        # la personne fêtée ne doit pas recevoir ce message.
                        "recipient_ids": [],
                    },
                    email_layout_xmlid="mail.mail_notification_light",
                )
            occ.reminder_sent = True
        return len(a_rappeler)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_creer_tableau(self):
        """Un clic depuis le rappel, sinon rien ne se fait."""
        self.ensure_one()
        if not self.allow_board:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "warning",
                    "message": _(
                        "Cette personne a demandé qu'on souligne la date "
                        "sans monter de tableau."),
                },
            }
        tableau = self.board_ids[:1]
        if not tableau:
            tableau = self.env["bf.celebration.board"].create(
                self._valeurs_tableau())
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.celebration.board",
            "res_id": tableau.id,
            "view_mode": "form",
            "target": "current",
        }

    def _valeurs_tableau(self):
        self.ensure_one()
        return {
            "name": self.name,
            "occasion_id": self.id,
            "recipient_employee_id": self.employee_id.id,
            "recipient_partner_id": self.partner_id.id,
            "organizer_id": (
                self.organizer_id.id or self.env.user.id),
            "delivery_date": fields.Datetime.to_datetime(
                "%s 13:00:00" % self.date),
            "company_id": self.company_id.id,
        }

    def action_voir_tableaux(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.celebration.board",
            "domain": [("occasion_id", "=", self.id)],
            "view_mode": "list,form",
            "name": _("Tableaux de vœux"),
        }
