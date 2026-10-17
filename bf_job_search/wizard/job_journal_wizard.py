# -*- coding: utf-8 -*-
"""Le journal des démarches, et la purge des détails sensibles.

Assurance-emploi (fédéral, donc aussi au Québec) : garder un relevé détaillé des
démarches de recherche d'emploi, six ans, à fournir sur demande (canada.ca, droits
et responsabilités des prestataires). Le journal ne porte que ce que ce relevé
demande : date, employeur, poste, canal, résultat. La purge efface le reste
(annonce, salaire, notes, fichiers, entrevues) des candidatures fermées depuis
longtemps, et garde l'entrée du journal.
"""
import base64
import csv
import io
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields, models
from odoo.exceptions import UserError

#: Ce que la purge efface d'une candidature fermée.
PURGED_FIELDS = {
    "posting": False, "fit_note": False, "close_note": False, "salary_min": 0.0,
    "salary_max": 0.0, "offer_salary": 0.0, "offer_note": False, "source_url": False,
    "recruiter": False, "referred_by": False, "cv_attachment_id": False,
    "letter_attachment_id": False,
}


class JobJournalWizard(models.TransientModel):
    _name = "bf.job.journal.wizard"
    _description = "Job search log"

    date_from = fields.Date(
        "From", required=True,
        default=lambda self: fields.Date.context_today(self) - relativedelta(months=3))
    date_to = fields.Date("To", required=True, default=fields.Date.context_today)
    purge_before = fields.Date(
        "Purge details of applications closed before",
        default=lambda self: fields.Date.context_today(self) - relativedelta(years=1))
    file_data = fields.Binary("File", readonly=True, attachment=False)
    file_name = fields.Char(readonly=True)
    line_count = fields.Integer(readonly=True)

    def _applications(self):
        return self.env["bf.job.application"].with_context(active_test=False).search(
            [("user_id", "=", self.env.uid), ("date_applied", ">=", self.date_from),
             ("date_applied", "<=", self.date_to)], order="date_applied, id")

    def action_export(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(self.env._("The start date is after the end date."))
        _ = self.env._
        channels = dict(self.env["bf.job.application"]._fields["channel"]._description_selection(self.env))
        buf = io.StringIO()
        out = csv.writer(buf)
        out.writerow([_("Date applied"), _("Employer"), _("Position"), _("Channel"),
                      _("Location"), _("Result"), _("Result date")])
        apps = self._applications()
        for app in apps:
            out.writerow([fields.Date.to_string(app.date_applied), app.employer, app.name,
                          channels.get(app.channel, ""), app.location or "", app._log_result(),
                          fields.Date.to_string(app.date_closed) if app.date_closed else ""])
        # BOM : Excel ouvre alors le fichier en UTF-8 (accents des employeurs).
        data = ("﻿" + buf.getvalue()).encode("utf-8")
        self.write({
            "file_data": base64.b64encode(data),
            "file_name": "job-search-log-%s-%s.csv" % (self.date_from, self.date_to),
            "line_count": len(apps),
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_purge(self):
        """Efface les détails des candidatures fermées avant la date, dans le rôle de
        la personne. Les fichiers joints et les entrevues partent avec."""
        self.ensure_one()
        if not self.purge_before:
            raise UserError(self.env._("Choose a date first."))
        if self.purge_before > fields.Date.context_today(self) - timedelta(days=30):
            raise UserError(self.env._(
                "Only applications closed more than 30 days ago can be purged."))
        apps = self.env["bf.job.application"].with_context(active_test=False).search(
            [("user_id", "=", self.env.uid), ("is_closed", "=", True),
             ("date_closed", "<", self.purge_before), ("details_purged", "=", False)])
        if not apps:
            raise UserError(self.env._("No closed application to purge before this date."))
        apps.write(dict(PURGED_FIELDS, details_purged=True))
        self.env["ir.attachment"].search(
            [("res_model", "=", "bf.job.application"), ("res_id", "in", apps.ids)]).unlink()
        apps.interview_ids.unlink()
        apps.reference_ids.write({"note": False})
        # Le fil garde les notes : on vide leur texte, quel que soit leur type (en
        # Odoo 18, ``message_post`` écrit par défaut des « notification »). Les
        # suivis de champs (étape, employeur, poste) restent : c'est le journal.
        messages = self.env["mail.message"].sudo().search(
            [("model", "=", "bf.job.application"), ("res_id", "in", apps.ids)])
        # Une activité faite avec un fichier le range sur le MESSAGE (res_model
        # mail.message) : la recherche par candidature plus haut ne le voit pas.
        messages.attachment_ids.unlink()
        messages.write({"body": ""})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "message": self.env._(
                "%s application(s) purged. Their log entries remain.", len(apps)),
                "next": {"type": "ir.actions.act_window_close"}},
        }
