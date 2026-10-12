"""Après une fuite ou un portefeuille perdu : les deux dossiers de crédit tout de
suite, puis à un mois et à trois mois.

La cadence est un choix de l'appli, arbitré le 2026-10-03 : aucune source ne
l'impose. Le guide la présente comme le calendrier de l'appli, pas comme une
règle (section 7), et l'assistant le dit aussi.

Six rappels PONCTUELS, à la personne qui lance l'assistant : chacun s'archive
quand il est fait. Ils portent ``after_breach`` (le calendrier de départ ne les
compte pas comme ses propres dossiers) et ``breach_date``, la date de départ de
leur série. Relancer l'assistant pour une date qui a déjà sa série ne crée rien,
même si un rappel de la série est déjà fait (archivé) ou a été déplacé ; une autre
date (une deuxième fuite) crée sa propre série.

L'assistant se supprime une fois la série créée : une ligne de cet assistant dit
qu'il y a eu une fuite, même illisible pour autrui (la règle globale la protège,
mais une erreur d'accès sur un id dit qu'il existe). Limite : son compteur d'id
reste. Une personne qui crée ses propres assistants par RPC voit un trou dans la
numérotation, donc que quelqu'un s'en est servi entre-temps (le README le dit).
"""
from dateutil.relativedelta import relativedelta

from odoo import fields, models

from ..models.credit_reminder import LEAD_DAYS, page_for

# Les dossiers à consulter, et quand (en mois après le départ).
REPORTS = [("report_equifax", "equifax"), ("report_transunion", "transunion")]
MONTHS = [0, 1, 3]


class CreditBreachWizard(models.TransientModel):
    _name = "bf.credit.breach"
    _description = "Plan my credit checks after a breach"

    start_date = fields.Date(
        "First check", required=True, default=fields.Date.context_today,
        help="Both credit reports are due on this day, then one month and three months later.")

    def _step(self, months):
        """L'étape lisible, dans la langue de la personne."""
        if months == 0:
            return self.env._("right away")
        if months == 1:
            return self.env._("one month later")
        return self.env._("three months later")

    def _plan(self):
        """La liste des rappels voulus : (genre, agence, date, nombre de mois)."""
        self.ensure_one()
        return [
            (kind, bureau, self.start_date + relativedelta(months=months), months)
            for months in MONTHS
            for kind, bureau in REPORTS
        ]

    def action_create(self):
        self.ensure_one()
        Reminder = self.env["bf.credit.reminder"]
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bf_credit_identity.action_credit_reminder")
        # La règle globale ne montre que les rappels de la personne : la recherche
        # ne voit donc que les siens. Archivés compris : un rappel fait reste fait.
        if Reminder.with_context(active_test=False).search_count([
                ("after_breach", "=", True), ("breach_date", "=", self.start_date)]):
            self.unlink()
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "info",
                    "message": self.env._(
                        "This date already has its series: nothing was added. Done or "
                        "deleted reminders are not created again."),
                    "next": action,
                },
            }
        kinds = dict(Reminder._fields["kind"]._description_selection(self.env))
        nouveaux = Reminder.create([{
            "name": self.env._(
                "%(report)s (after a breach, %(step)s)",
                report=kinds[kind], step=self._step(months)),
            "kind": kind,
            "bureau": bureau,
            "next_date": date,
            "recurring": False,
            "after_breach": True,
            "breach_date": self.start_date,
            "link_url": page_for(kind, bureau, self.env.lang),
        } for kind, bureau, date, months in self._plan()])
        # Les dossiers « tout de suite » ont leur activité sur-le-champ.
        horizon = fields.Date.context_today(self) + relativedelta(days=LEAD_DAYS)
        nouveaux.filtered(lambda r: r.next_date <= horizon)._raise_activity()
        self.unlink()
        return action
