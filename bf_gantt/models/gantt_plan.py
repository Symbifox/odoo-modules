# -*- coding: utf-8 -*-
"""L'échéancier qui ne crée aucune tâche.

Le besoin est celui du devis et du plan d'implantation : dessiner un calendrier
de douze lignes pour le montrer à quelqu'un, sans ouvrir douze `project.task`
qui pollueraient le projet, la banque d'heures et les rapports.

Un plan porte ses propres lignes, ses propres couloirs et ses propres liens. Il
peut citer un projet, sans jamais en dépendre.
"""
import uuid
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

GROUPE_GESTION = "bf_gantt.group_bf_gantt_manager"

ETATS = [
    ("draft", "Draft"),
    ("active", "In progress"),
    ("done", "Done"),
    ("cancel", "Cancelled"),
]


class BfGanttPlan(models.Model):
    _name = "bf.gantt.plan"
    _description = "Standalone timeline"
    _inherit = ["portal.mixin", "mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, name"

    name = fields.Char(string="Name", required=True, tracking=True)
    active = fields.Boolean(string="Active", default=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True,
        default=lambda self: self.env.company, index=True,
    )
    partner_id = fields.Many2one("res.partner", string="Client", tracking=True)
    project_id = fields.Many2one(
        "project.project", string="Referenced project", tracking=True,
        help="Courtesy link to a project. The plan stays standalone: no "
             "task is created, read or modified.",
    )
    user_id = fields.Many2one(
        "res.users", string="Responsible", default=lambda self: self.env.user,
        tracking=True,
    )
    state = fields.Selection(ETATS, string="Status", default="draft",
                             required=True, tracking=True)
    date_start = fields.Date(string="Start", tracking=True)
    date_end = fields.Date(string="End", tracking=True)
    note = fields.Html(string="Notes")
    item_ids = fields.One2many("bf.gantt.item", "plan_id", string="Lines")
    item_count = fields.Integer(string="Lines", compute="_compute_item_count")
    portal_published = fields.Boolean(
        string="Published on the portal", tracking=True,
        help="As long as the box is unchecked, the token address answers "
             "\"access denied\" even with the right token.",
    )

    @api.depends("item_ids")
    def _compute_item_count(self):
        groupes = dict(self.env["bf.gantt.item"]._read_group(
            [("plan_id", "in", self.ids)], ["plan_id"], ["__count"],
        ))
        for plan in self:
            plan.item_count = groupes.get(plan, 0)

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for plan in self:
            if plan.date_start and plan.date_end and plan.date_end < plan.date_start:
                raise ValidationError(_("The plan's end falls before its "
                                        "start."))

    def _compute_access_url(self):
        super()._compute_access_url()
        for plan in self:
            plan.access_url = "/mon/echeancier/plan/%s" % plan.id

    def action_bf_gantt(self):
        """Ouvre le composant sur ce plan."""
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("bf_gantt.action_bf_gantt")
        action["context"] = {
            "default_bf_gantt_kind": "plan",
            "default_bf_gantt_id": self.id,
        }
        action["params"] = {"kind": "plan", "res_id": self.id}
        return action

    def write(self, valeurs):
        if "portal_published" in valeurs:
            self._exiger_le_droit_de_publier()
        return super().write(valeurs)

    @api.model_create_multi
    def create(self, liste_valeurs):
        for valeurs in liste_valeurs:
            if valeurs.get("portal_published"):
                self._exiger_le_droit_de_publier()
        return super().create(liste_valeurs)

    def _exiger_le_droit_de_publier(self):
        """⚠️ L'ACL borne déjà l'écriture sur ce modèle, mais la propriété
        reposait alors ENTIÈREMENT sur elle : desserrer le CSV plus tard aurait
        ouvert la publication sans que rien ne le signale. Les deux modèles
        échouent maintenant de la même façon, pour la même raison."""
        # ⚠️ Le superusager passe, comme partout dans Odoo : une migration, un
        # fichier de données ou une action serveur tournent sous `env.su`, et une
        # garde applicative qui les refuse casse l'installation sans rien
        # protéger de plus (qui peut écrire une action serveur est déjà admin).
        if not self.env.su and not self.env.user.has_group(GROUPE_GESTION):
            raise AccessError(_(
                "Publishing a timeline makes it readable without an "
                "account. This action requires the \"Timeline: management "
                "and publishing\" group."))

    def action_bf_gantt_publier(self):
        self._exiger_le_droit_de_publier()
        for plan in self:
            plan.portal_published = True
            plan._portal_ensure_token()
        return True

    def action_bf_gantt_depublier(self):
        self._exiger_le_droit_de_publier()
        self.write({"portal_published": False})
        return True

    def action_bf_gantt_regenerer_token(self):
        """Coupe les adresses déjà distribuées, et en ouvre une neuve."""
        self._exiger_le_droit_de_publier()
        for plan in self:
            plan.access_token = uuid.uuid4().hex
        return True

    def action_bf_gantt_importer(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Import a timeline"),
            "res_model": "bf.gantt.import",
            "view_mode": "form",
            "target": "new",
            "context": {"default_plan_id": self.id},
        }


class BfGanttItem(models.Model):
    _name = "bf.gantt.item"
    _description = "Standalone timeline line"
    _order = "sequence, date_start, id"

    name = fields.Char(string="Line", required=True)
    plan_id = fields.Many2one(
        "bf.gantt.plan", string="Timeline", required=True,
        ondelete="cascade", index=True,
    )
    company_id = fields.Many2one(
        related="plan_id.company_id", store=True, index=True,
    )
    sequence = fields.Integer(string="Sequence", default=10)
    lane = fields.Char(
        string="Lane",
        help="The horizontal grouping. A phase, a team, a work package: "
             "whatever you like, it is free text.",
    )
    date_start = fields.Date(string="Start", required=True)
    date_end = fields.Date(string="End")
    is_milestone = fields.Boolean(
        string="Milestone",
        help="A milestone is a zero-duration event. Its end date is ignored.",
    )
    progress = fields.Integer(string="Progress", default=0)
    assignee = fields.Char(string="Assignee")
    allocated_hours = fields.Float(string="Planned hours")
    state = fields.Selection(
        [("todo", "Upcoming"), ("doing", "In progress"),
         ("done", "Done"), ("cancel", "Cancelled")],
        string="Status", default="todo", required=True,
    )
    depend_on_ids = fields.Many2many(
        "bf.gantt.item", "bf_gantt_item_dep_rel", "item_id", "depends_on_id",
        string="Predecessors",
    )
    note = fields.Text(string="Note")

    @api.constrains("progress")
    def _check_progress(self):
        for item in self:
            if not 0 <= item.progress <= 100:
                raise ValidationError(_("Progress must be between 0 and 100."))

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for item in self:
            if item.date_end and item.date_start and item.date_end < item.date_start:
                raise ValidationError(_(
                    "The line \"%(nom)s\" ends before it starts.", nom=item.name))

    @api.constrains("depend_on_ids")
    def _check_no_cycle(self):
        """Un lien circulaire fige le tracé des flèches et n'a aucun sens."""
        for item in self:
            vus = set()
            pile = list(item.depend_on_ids.ids)
            while pile:
                courant = pile.pop()
                if courant == item.id:
                    raise ValidationError(_(
                        "\"%(nom)s\" would end up depending on itself.",
                        nom=item.name))
                if courant in vus:
                    continue
                vus.add(courant)
                pile.extend(self.browse(courant).depend_on_ids.ids)

    @api.onchange("is_milestone")
    def _onchange_is_milestone(self):
        if self.is_milestone:
            self.date_end = self.date_start

    def gantt_status(self, aujourdhui=None):
        """Le même vocabulaire de statut que pour une tâche de projet."""
        self.ensure_one()
        aujourdhui = aujourdhui or date.today()
        if self.state == "done":
            return "done"
        if self.state == "cancel":
            return "canceled"
        fin = self.date_end or self.date_start
        if fin and fin < aujourdhui:
            return "overdue"
        if self.state == "doing":
            return "in_progress"
        return "upcoming"
