"""bf.email.rule.quick — the « common rules » picker.

Outlook ships a rules gallery because a blank rule editor is a wall: the person
who most needs a rule is the one least likely to know which condition to pick.
This wizard shows the catalogue in ``bf_email_rule.RULE_RECIPES`` as a
checklist, says which ones are already installed, and creates the rest in one
click.

Nothing here is a second source of truth — a recipe improved in the catalogue
shows up here on the next open.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.bf_email_rule import RULE_RECIPES


class BfEmailRuleQuick(models.TransientModel):
    _name = "bf.email.rule.quick"
    _description = "Ajouter des règles courantes"

    scope = fields.Selection(
        selection=[
            ("user", "Pour moi"),
            ("company", "Pour toute l'organisation"),
        ],
        string="Créer",
        required=True,
        default="user",
    )
    can_create_company_rules = fields.Boolean(
        string="Peut créer des règles d'organisation",
        default=lambda self: self.env.user.has_group("base.group_system"),
        readonly=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
        required=True,
    )
    line_ids = fields.One2many(
        comodel_name="bf.email.rule.quick.line",
        inverse_name="wizard_id",
        string="Règles proposées",
    )
    apply_now = fields.Boolean(
        string="Appliquer aux courriels déjà reçus",
        default=True,
        help="Une règle ne s'exécute qu'à l'arrivée d'un courriel. Sans "
             "cette case, la boîte ne bouge pas tant qu'un nouveau message "
             "n'est pas arrivé — ce qui donne l'impression que les règles "
             "ajoutées ne fonctionnent pas.",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if "line_ids" not in fields_list:
            return values
        installed = set(self.env["bf.email.rule"].sudo().with_context(
            active_test=False,
        ).search([
            ("recipe_key", "!=", False),
            "|", ("user_id", "=", self.env.uid), ("user_id", "=", False),
        ]).mapped("recipe_key"))
        values["line_ids"] = [
            (0, 0, {
                "recipe_key": recipe["key"],
                "name": recipe["name"],
                "description": recipe.get("description", ""),
                "already_installed": recipe["key"] in installed,
                "selected": False,
            })
            for recipe in RULE_RECIPES
        ]
        return values

    def action_create_rules(self):
        self.ensure_one()
        chosen = self.line_ids.filtered("selected")
        if not chosen:
            raise UserError(_("Aucune règle sélectionnée."))
        if self.scope == "company" and not self.can_create_company_rules:
            raise UserError(_(
                "Les règles d'organisation sont réservées aux "
                "administrateurs."))

        Rule = self.env["bf.email.rule"]
        recipes = {r["key"]: r for r in RULE_RECIPES}
        vals_list = []
        for line in chosen:
            recipe = recipes.get(line.recipe_key)
            if not recipe:
                continue
            vals = Rule._recipe_to_vals(
                recipe,
                user=self.env.user if self.scope == "user" else None,
                company=self.company_id,
            )
            vals_list.append(vals)
        created = Rule.create(vals_list)

        if self.apply_now and created:
            # Only the box of whoever is standing here: a company-wide recipe
            # applies to everybody from now on, but reaching into everybody's
            # archive is not something a checkbox gets to do.
            rows = self.env["bf.email"].browse(sorted({
                row_id
                for rule in created
                for row_id in rule._matching_rows().ids
            }))
            if rows:
                rows._apply_rules(allow_outbound=False, rules=created)

        return {
            "type": "ir.actions.act_window",
            "name": _("Règles ajoutées"),
            "res_model": "bf.email.rule",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }

    def action_select_all(self):
        self.ensure_one()
        self.line_ids.filtered(
            lambda line: not line.already_installed).selected = True
        return self._reopen()

    def action_select_none(self):
        self.ensure_one()
        self.line_ids.selected = False
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class BfEmailRuleQuickLine(models.TransientModel):
    _name = "bf.email.rule.quick.line"
    _description = "Règle courante proposée"
    _order = "id"

    wizard_id = fields.Many2one(
        comodel_name="bf.email.rule.quick",
        required=True,
        ondelete="cascade",
    )
    recipe_key = fields.Char(string="Clé", required=True)
    name = fields.Char(string="Règle", required=True)
    description = fields.Text(string="Ce qu'elle fait")
    already_installed = fields.Boolean(string="Déjà présente", readonly=True)
    selected = fields.Boolean(string="Ajouter")
