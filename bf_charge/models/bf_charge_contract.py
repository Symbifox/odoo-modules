# -*- coding: utf-8 -*-
"""Le contrat de capacité : le dénominateur, déclaré et jamais déduit.

Le module mesure quatre candidats et n'en choisit aucun : les heures saisies
portent des journées à plus de seize heures parce que du travail assisté produit
plusieurs heures dans la même heure. Aucune division ne rattrape ça, seule une
déclaration humaine le fait.
"""
import logging
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

#: Plafond d'heures par jour employé pour ramener les heures saisies à un temps
#: d'horloge plausible. Ce n'est pas une vérité, c'est une borne explicite.
PLAFOND_JOUR = 12.0


class BfChargeContract(models.Model):
    _name = "bf.charge.contract"
    _description = "Contrat de capacité"
    _inherit = ["mail.thread"]
    _order = "date_start desc, id desc"

    name = fields.Char(string="Nom", required=True, default="Contrat de capacité")
    company_id = fields.Many2one(
        "res.company", string="Société", required=True,
        default=lambda self: self.env.company,
    )
    user_id = fields.Many2one(
        "res.users", string="Personne", required=True, tracking=True,
        default=lambda self: self.env.user,
        help="La capacité déclarée porte sur une personne, pas sur une équipe.",
    )
    date_start = fields.Date(string="En vigueur depuis", required=True, tracking=True,
                             default=fields.Date.context_today)
    state = fields.Selection(
        [("draft", "Brouillon"), ("active", "En vigueur"), ("closed", "Clos")],
        string="État", default="draft", required=True, tracking=True,
    )

    hours_per_week = fields.Float(
        string="Heures par semaine", tracking=True,
        help="Heures réellement disponibles pour livrer, par semaine. "
             "Ce chiffre se déclare, il ne se calcule pas.",
    )
    client_share_target = fields.Float(
        string="Part client visée (%)", tracking=True, default=60.0,
        help="Part des heures livrées qui doit aller à des mandats clients.",
    )
    max_concurrent_clients = fields.Integer(
        string="Plafond de clients simultanés", tracking=True, default=0,
        help="Nombre de clients distincts que l'on accepte de porter en même temps. "
             "0 veut dire : aucun plafond déclaré, donc aucun signal.",
    )
    meeting_hours_per_week = fields.Float(
        string="Heures de rencontres par semaine", tracking=True,
        help="Charge de rencontres retranchée de la capacité. À modéliser sur "
             "l'historique, pas sur le calendrier à venir : les rendez-vous se "
             "posent tard, donc le calendrier futur sous-estime toujours.",
    )
    stop_criteria = fields.Text(
        string="Critères d'arrêt",
        help="Ce qui doit arriver pour qu'on arrête d'en prendre. Numérique de "
             "préférence, et écrit avant d'en avoir besoin.",
    )
    note = fields.Text(string="Notes")

    net_hours_per_week = fields.Float(
        string="Capacité nette par semaine", compute="_compute_net_hours", store=False,
        help="Heures par semaine, moins la charge de rencontres modélisée.",
    )

    # --- Les candidats mesurés, affichés pour que la déclaration soit informée ---
    measure_median_12m = fields.Float(string="Médiane hebdomadaire (12 mois)",
                                      compute="_compute_measures", store=False)
    measure_mean_12m = fields.Float(string="Moyenne hebdomadaire (12 mois)",
                                    compute="_compute_measures", store=False)
    measure_calendar = fields.Float(string="Calendrier de travail déclaré",
                                    compute="_compute_measures", store=False)
    measure_capped_90d = fields.Float(string="Médiane 90 jours plafonnée au temps d'horloge",
                                      compute="_compute_measures", store=False)
    measure_meetings = fields.Float(string="Rencontres par semaine (médiane 90 jours)",
                                    compute="_compute_measures", store=False)
    measure_note = fields.Char(string="Ce que les candidats valent",
                               compute="_compute_measures", store=False)

    _sql_constraints = [
        ("hours_positive", "CHECK(hours_per_week >= 0)",
         "Les heures par semaine ne peuvent pas être négatives."),
    ]

    @api.depends("hours_per_week", "meeting_hours_per_week")
    def _compute_net_hours(self):
        for rec in self:
            rec.net_hours_per_week = max(
                0.0, (rec.hours_per_week or 0.0) - (rec.meeting_hours_per_week or 0.0)
            )

    @api.constrains("state", "hours_per_week")
    def _check_active_has_hours(self):
        for rec in self:
            if rec.state == "active" and not rec.hours_per_week:
                raise ValidationError(_(
                    "Un contrat en vigueur doit porter un nombre d'heures par semaine. "
                    "C'est tout son objet : sans dénominateur, aucun signal de "
                    "saturation n'a de sens."
                ))

    @api.depends("user_id")
    def _compute_measures(self):
        for rec in self:
            vals = rec._measure_candidates()
            rec.measure_median_12m = vals["median_12m"]
            rec.measure_mean_12m = vals["mean_12m"]
            rec.measure_calendar = vals["calendar"]
            rec.measure_capped_90d = vals["capped_90d"]
            rec.measure_meetings = vals["meetings"]
            rec.measure_note = _(
                "%(n)s semaines mesurées. Ces chiffres décrivent ce qui a été SAISI, "
                "pas du temps d'horloge : à lire comme des bornes, pas comme une réponse."
            ) % {"n": vals["weeks"]}

    # ------------------------------------------------------------------
    # Mesure
    # ------------------------------------------------------------------
    def _measure_candidates(self):
        """Quatre candidats de capacité, plus la charge de rencontres.

        Aucun n'est retenu par le module. Ils existent pour que la déclaration
        se fasse en connaissance de cause.
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        vide = {"median_12m": 0.0, "mean_12m": 0.0, "calendar": 0.0,
                "capped_90d": 0.0, "meetings": 0.0, "weeks": 0}
        if not self.user_id:
            return vide

        # 🔴 `sudo()` obligatoire : le groupe du module est étroit et ne porte
        # AUCUN droit sur `account.analytic.line`. Sans ça, ouvrir le formulaire
        # du contrat lève un AccessError pour tout le monde sauf un
        # administrateur système. Vu en production le 2026-09-21, en jouant le
        # parcours dans le rôle visé plutôt qu'en admin.
        # ⚠️ Un sudo contourne aussi les règles de société : la borne est donc
        # posée à la main dans le domaine, jamais laissée à la règle.
        lines = self.env["account.analytic.line"].sudo().search([
            ("user_id", "=", self.user_id.id),
            ("date", ">=", today - timedelta(days=365)),
            ("project_id", "!=", False),
            ("company_id", "in", [False, self.company_id.id]),
        ])
        par_semaine = {}
        par_jour = {}
        for line in lines:
            jour = line.date
            par_semaine.setdefault(jour.isocalendar()[:2], 0.0)
            par_semaine[jour.isocalendar()[:2]] += line.unit_amount or 0.0
            par_jour.setdefault(jour, 0.0)
            par_jour[jour] += line.unit_amount or 0.0

        hebdo = sorted(par_semaine.values())
        median_12m = _mediane(hebdo)
        mean_12m = (sum(hebdo) / len(hebdo)) if hebdo else 0.0

        # Médiane récente, chaque journée ramenée au plafond d'horloge.
        borne = today - timedelta(days=90)
        plafonne = {}
        for jour, heures in par_jour.items():
            if jour >= borne:
                cle = jour.isocalendar()[:2]
                plafonne.setdefault(cle, 0.0)
                plafonne[cle] += min(PLAFOND_JOUR, heures)
        capped_90d = _mediane(sorted(plafonne.values()))

        calendrier = 0.0
        employe = self.env["hr.employee"].sudo().search(
            [("user_id", "=", self.user_id.id)], limit=1)
        if employe and employe.resource_calendar_id:
            cal = employe.resource_calendar_id
            # 🔴 `resource.calendar` n'a PAS de `hours_per_week` en Odoo 18 : le
            # champ s'appelle `full_time_required_hours`. Y toucher levait un
            # AttributeError, et aucun essai ne lisait les champs de mesure,
            # donc la branche entière n'était gardée par personne. Trouvé en
            # jouant le parcours dans le rôle visé, le 2026-09-21.
            if "full_time_required_hours" in cal._fields:
                calendrier = cal.full_time_required_hours or 0.0
            if not calendrier:
                # Repli : les heures par jour, sur cinq jours ouvrables.
                calendrier = (cal.hours_per_day or 0.0) * 5.0

        return {
            "median_12m": median_12m,
            "mean_12m": mean_12m,
            "calendar": calendrier,
            "capped_90d": capped_90d,
            "meetings": self._measure_meeting_hours(),
            "weeks": len(hebdo),
        }

    def _measure_meeting_hours(self):
        """Médiane hebdomadaire des rendez-vous des 90 derniers jours.

        🔴 Volontairement tournée vers le PASSÉ. Le calendrier à venir est
        structurellement vide : les rendez-vous se posent tard, donc s'en servir
        pour estimer la charge à venir la sous-estime toujours.
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        debut = fields.Datetime.to_datetime(today - timedelta(days=90))
        fin = fields.Datetime.to_datetime(today)
        # 🔴 `calendar` n'est PAS une dépendance du module et `project` ne
        # l'entraîne pas. Appeler `self.env["calendar.event"]` sans regarder
        # lève un KeyError sur toute base qui ne l'a pas. Sur une telle base, la
        # charge de rencontres vaut zéro, et c'est un fait, pas une panne.
        if "calendar.event" not in self.env.registry:
            return 0.0
        partner = self.user_id.partner_id
        if not partner:
            return 0.0
        events = self.env["calendar.event"].sudo().search([
            ("start", ">=", debut), ("start", "<", fin),
            ("partner_ids", "in", partner.id),
        ])
        par_semaine = {}
        for ev in events:
            cle = ev.start.date().isocalendar()[:2]
            par_semaine.setdefault(cle, 0.0)
            par_semaine[cle] += ev.duration or 0.0
        return _mediane(sorted(par_semaine.values()))

    def action_adopt_measure_meetings(self):
        """Reporter la médiane mesurée des rencontres dans le contrat."""
        for rec in self:
            rec.meeting_hours_per_week = rec._measure_meeting_hours()
        return True

    def action_activate(self):
        for rec in self:
            autres = self.search([
                ("user_id", "=", rec.user_id.id),
                ("company_id", "=", rec.company_id.id),
                ("state", "=", "active"), ("id", "!=", rec.id),
            ])
            autres.write({"state": "closed"})
            rec.state = "active"
        return True

    def action_close(self):
        self.write({"state": "closed"})
        return True

    @api.model
    def _get_active(self, user=None, company=None):
        """Le contrat en vigueur, ou un enregistrement vide."""
        user = user or self.env.user
        company = company or self.env.company
        return self.search([
            ("user_id", "=", user.id),
            ("company_id", "=", company.id),
            ("state", "=", "active"),
        ], limit=1, order="date_start desc, id desc")


def _mediane(valeurs_triees):
    """Médiane d'une liste DÉJÀ triée. Rend 0.0 sur une liste vide."""
    n = len(valeurs_triees)
    if not n:
        return 0.0
    milieu = n // 2
    if n % 2:
        return valeurs_triees[milieu]
    return (valeurs_triees[milieu - 1] + valeurs_triees[milieu]) / 2.0
