from random import randint

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class PeopleInterest(models.Model):
    _name = "bf.people.interest"
    _description = "Interest of a person I've met"
    _inherit = ["bf.people.owned.mixin"]
    _order = "name"
    _people_field = "interest_ids"

    name = fields.Char(string="Interest")
    # Une couleur d'office : en kanban, Odoo 18 masque les étiquettes de couleur 0
    # (kanban_many2many_tags_field.js), et la carte n'aurait montré aucun intérêt.
    color = fields.Integer(default=lambda self: randint(1, 11))
    # copy=False : la copie d'un intérêt lu par une fiche partagée emportait la fiche.
    person_ids = fields.Many2many(
        "bf.people.person", "bf_people_person_interest_rel", "interest_id", "person_id",
        string="People", copy=False)
    person_count = fields.Integer(compute="_compute_person_count")

    _sql_constraints = [
        ("uniq_owner_name", "unique(user_id, name)", "You already have an interest with this name."),
    ]

    @api.constrains("person_ids", "user_id")
    def _check_own_cards(self):
        """Un intérêt ne s'accroche qu'aux fiches de sa propriétaire. Un Many2many s'écrit
        en SQL sans contrôle d'écriture sur l'autre côté : sans cette garde, un membre
        créait un intérêt posé sur la fiche d'autrui."""
        for interet in self.sudo():
            if any(fiche.user_id != interet.user_id
                   for fiche in interet.with_context(active_test=False).person_ids):
                raise ValidationError(self.env._("An interest goes on your own cards only."))

    def _private_label(self):
        return self.env._("Private interest")

    @api.depends_context("uid")
    def _compute_person_count(self):
        comptes = dict(self.env["bf.people.person"]._read_group(
            [("interest_ids", "in", [i for i in self.ids if isinstance(i, int)])],
            ["interest_ids"], ["__count"]))
        for interet in self:
            interet.person_count = comptes.get(interet, 0)

    def action_open_people(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_people_notebook.action_people_person")
        action["domain"] = [("interest_ids", "in", self.ids)]
        action["context"] = {"default_interest_ids": [(6, 0, self.ids)]}
        return action
