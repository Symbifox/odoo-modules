# -*- coding: utf-8 -*-
"""Vivant, dormant ou gabarit : la classification qui retire le bruit.

Mesuré sur une base réelle : la grande majorité des heures budgétées ouvertes
vivaient dans une poignée de projets sans une seule heure saisie depuis 90 jours,
dont des gabarits qui ne sont pas du travail mais des patrons à cloner.

La dernière ligne de temps du projet suffit à trancher. Aucun message n'est lu.
"""
from datetime import timedelta

from odoo import api, fields, models

#: Nom de l'étiquette qui marque un projet comme patron à cloner. Surchargeable
#: par le paramètre système `bf_charge.template_tag`.
ETIQUETTE_GABARIT = "Gabarit"
#: Au delà de ce nombre de jours sans heure saisie, un projet est dit dormant.
JOURS_DORMANT = 90


class ProjectProject(models.Model):
    _inherit = "project.project"

    charge_last_timesheet_date = fields.Date(
        string="Dernière heure saisie", compute="_compute_bf_charge_kind", store=False,
    )
    charge_kind = fields.Selection(
        [
            ("vivant", "Vivant"),
            ("dormant", "Dormant"),
            ("gabarit", "Gabarit"),
        ],
        string="Nature", compute="_compute_bf_charge_kind", store=False,
        help="Gabarit : porte l'étiquette des patrons, ses heures n'entrent dans "
             "aucun plan. Vivant : du temps y a été saisi récemment. Dormant : "
             "aucune heure saisie depuis la fenêtre configurée.",
    )

    @api.model
    def _bf_charge_template_tag_names(self):
        param = self.env["ir.config_parameter"].sudo().get_param(
            "bf_charge.template_tag", ETIQUETTE_GABARIT)
        return [nom.strip() for nom in (param or "").split(",") if nom.strip()]

    @api.model
    def _bf_charge_dormant_days(self):
        param = self.env["ir.config_parameter"].sudo().get_param(
            "bf_charge.dormant_days", JOURS_DORMANT)
        try:
            return max(1, int(param))
        except (TypeError, ValueError):
            return JOURS_DORMANT

    def _compute_bf_charge_kind(self):
        if not self:
            return
        noms = {nom.lower() for nom in self._bf_charge_template_tag_names()}
        borne = fields.Date.context_today(self) - timedelta(days=self._bf_charge_dormant_days())

        # Une seule lecture groupée pour tout le recordset : la dernière ligne
        # de temps par projet. `_read_group` d'Odoo 18 rend des tuples, pas des
        # dictionnaires, et le projet arrive en recordset.
        # 🔴 `sudo()` : voir le commentaire du contrat de capacité. Le groupe du
        # module ne lit pas les lignes analytiques, et sans ça la simple
        # ouverture d'un projet lèverait un AccessError.
        groupes = self.env["account.analytic.line"].sudo()._read_group(
            [("project_id", "in", self.ids)],
            groupby=["project_id"], aggregates=["date:max"],
        )
        derniere = {
            projet.id: fields.Date.to_date(jour) if jour else False
            for projet, jour in groupes if projet
        }

        for projet in self:
            jour = derniere.get(projet.id) or False
            projet.charge_last_timesheet_date = jour
            etiquettes = {t.name.lower() for t in projet.tag_ids if t.name}
            if noms & etiquettes:
                projet.charge_kind = "gabarit"
            elif not projet.active:
                # Un projet ARCHIVÉ est rangé, jamais vivant, même s'il porte une
                # saisie récente. Vu en production : un projet archivé portait une
                # ligne de temps récente, et ses tâches ouvertes entraient dans le
                # plan.
                projet.charge_kind = "dormant"
            elif jour and jour >= borne:
                projet.charge_kind = "vivant"
            else:
                projet.charge_kind = "dormant"
