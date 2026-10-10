"""Mise en route : le calendrier de départ, pour la personne qui lance l'assistant.

Le calendrier de départ :

* dossier Equifax tous les 12 mois, à partir du jour choisi ;
* dossier TransUnion tous les 12 mois, six mois plus tard (l'alternance donne un
  coup d'oeil tous les six mois ; les sources disent « au moins une fois par an ») ;
* relevés bancaires et de cartes chaque mois (ACFC : examiner le relevé mensuel) ;
* revue des mesures de protection une fois par an ;
* si une alerte est posée : son renouvellement, une fois, 30 jours avant ses six ans
  (Equifax et TransUnion la gardent six ans).

Relancer l'assistant ne crée pas de doublon : un rappel actif du même genre (et de
la même agence) est gardé tel quel.
"""
from dateutil.relativedelta import relativedelta

from odoo import fields, models

from ..models.credit_reminder import LEAD_DAYS, page_for

ALERT_YEARS = 6
ALERT_NOTICE_DAYS = 30


class CreditSetupWizard(models.TransientModel):
    _name = "bf.credit.setup"
    _description = "Set up my credit and identity reminders"

    start_date = fields.Date(
        "First check", required=True, default=fields.Date.context_today,
        help="The Equifax credit report is due on this day; TransUnion six months later.")
    include_statements = fields.Boolean("Monthly statements", default=True)
    include_review = fields.Boolean("Yearly review of the protective measures", default=True)
    alert_date = fields.Date(
        "Security alert placed on",
        help="Leave empty if no security alert is placed. Equifax and TransUnion keep "
             "an alert six years.")
    alert_bureau = fields.Selection(
        [("equifax", "Equifax"), ("transunion", "TransUnion"), ("both", "Both bureaus")],
        string="Alert placed at", default="both")

    def _plan(self):
        """La liste des rappels voulus : (genre, agence, prochaine date, récurrent, nombre, unité)."""
        self.ensure_one()
        d0 = self.start_date
        plan = [
            ("report_equifax", "equifax", d0, True, 12, "month"),
            ("report_transunion", "transunion", d0 + relativedelta(months=6), True, 12, "month"),
        ]
        if self.include_statements:
            plan.append(("statements", False, d0 + relativedelta(months=1), True, 1, "month"))
        if self.include_review:
            plan.append(("measures_review", "both", d0 + relativedelta(months=12), True, 12, "month"))
        if self.alert_date:
            echeance = (self.alert_date + relativedelta(years=ALERT_YEARS)
                        - relativedelta(days=ALERT_NOTICE_DAYS))
            bureaus = ["equifax", "transunion"] if self.alert_bureau == "both" else [self.alert_bureau]
            for bureau in bureaus:
                plan.append(("alert_renewal", bureau, echeance, False, 1, "year"))
        return plan

    def action_create(self):
        self.ensure_one()
        Reminder = self.env["bf.credit.reminder"]
        kinds = dict(Reminder._fields["kind"]._description_selection(self.env))
        bureaus = dict(Reminder._fields["bureau"]._description_selection(self.env))
        # La règle globale ne montre que les rappels de la personne : la recherche
        # de doublons ne voit donc que les siens.
        existants = Reminder.search([])
        a_creer = []
        for kind, bureau, date, recurring, number, unit in self._plan():
            if existants.filtered(lambda r: r.kind == kind and (r.bureau or False) == bureau):
                continue
            name = kinds[kind]
            if kind == "alert_renewal":
                name = "%s (%s)" % (name, bureaus[bureau])
            a_creer.append({
                "name": name,
                "kind": kind,
                "bureau": bureau,
                "next_date": date,
                "recurring": recurring,
                "interval_number": number,
                "interval_unit": unit,
                "link_url": page_for(kind, bureau, self.env.lang),
            })
        nouveaux = Reminder.create(a_creer)
        # Ce qui tombe déjà dans la fenêtre de préavis a son activité tout de suite.
        horizon = fields.Date.context_today(self) + relativedelta(days=LEAD_DAYS)
        nouveaux.filtered(lambda r: r.next_date <= horizon)._raise_activity()
        return self.env["ir.actions.act_window"]._for_xml_id(
            "bf_credit_identity.action_credit_reminder")
