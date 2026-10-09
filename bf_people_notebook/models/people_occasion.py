"""Ce qu'on range sous une fiche de personne : une occasion, un intérêt.

Les deux appartiennent à la personne qui les a créés, comme les fiches. On les lit
aussi par une fiche qu'on nous a partagée : sinon la fiche partagée montrerait une
occasion vide. La règle d'accès le dit (``people_rules.xml``) ; ce fichier garde le
nom, qu'Odoo lit en sudo là où la règle ne regarde pas (erreur d'accès en mode
debug, Many2one lu par un autre modèle). Voir ``people_person.py``, garde 4.
"""
from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError


class PeopleOwnedMixin(models.AbstractModel):
    _name = "bf.people.owned.mixin"
    _description = "Owned by one person, readable through the cards they share"

    # Le champ de bf.people.person qui vise ce modèle : par lui, une fiche partagée
    # rend son occasion et ses intérêts lisibles.
    _people_field = None

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        "res.users", string="Owner", required=True, index=True, readonly=True,
        default=lambda self: self.env.user, ondelete="cascade")
    active = fields.Boolean(default=True)

    def _private_label(self):
        raise NotImplementedError

    def _ids_seen_through_shared_cards(self):
        """Les ids de ``self`` portés par une fiche partagée avec la personne qui lit."""
        ids = [i for i in self.ids if isinstance(i, int)]
        if not ids:
            return set()
        cartes = self.env["bf.people.person"].sudo().with_context(active_test=False).search(
            [(self._people_field, "in", ids), ("shared_user_ids", "in", [self.env.uid])])
        return set(cartes.mapped(self._people_field).ids) & set(ids)

    @api.depends("name", "user_id")
    @api.depends_context("uid")
    def _compute_display_name(self):
        uid = self.env.uid
        partages = self._ids_seen_through_shared_cards()
        for rec, lu in zip(self, self.sudo()):
            visible = (uid == SUPERUSER_ID or not lu.user_id or lu.user_id.id == uid
                       or rec.id in partages)
            rec.display_name = lu.name if visible else rec._private_label()

    def write(self, vals):
        # On ne donne pas son occasion ou son intérêt à quelqu'un d'autre : il
        # emporterait avec lui les fiches qui le portent, chez une autre personne.
        if "user_id" in vals and not self.env.su:
            raise AccessError(self.env._("An occasion or an interest stays with its owner."))
        return super().write(vals)


class PeopleOccasion(models.Model):
    _name = "bf.people.occasion"
    _description = "Occasion: a trip, an event, a season"
    _inherit = ["bf.people.owned.mixin"]
    _order = "date_start desc, id desc"
    _people_field = "occasion_id"

    name = fields.Char(string="Occasion", help="A trip, an event, a season: Gaspésie 2026, a wedding.")
    date_start = fields.Date("From")
    date_end = fields.Date("To")
    person_ids = fields.One2many("bf.people.person", "occasion_id", string="People")
    person_count = fields.Integer(compute="_compute_person_count")

    _sql_constraints = [
        ("uniq_owner_name", "unique(user_id, name)", "You already have an occasion with this name."),
    ]

    def _private_label(self):
        return self.env._("Private occasion")

    @api.depends_context("uid")
    def _compute_person_count(self):
        # Sous les droits de qui lit : le compte égale ce que le bouton ouvre.
        comptes = dict(self.env["bf.people.person"]._read_group(
            [("occasion_id", "in", [i for i in self.ids if isinstance(i, int)])],
            ["occasion_id"], ["__count"]))
        for occasion in self:
            occasion.person_count = comptes.get(occasion, 0)

    def action_open_people(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bf_people_notebook.action_people_person")
        action["domain"] = [("occasion_id", "=", self.id)]
        action["context"] = {"default_occasion_id": self.id}
        return action
