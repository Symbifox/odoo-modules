from odoo import api, fields, models
from odoo.exceptions import AccessError


class BfNoteLink(models.Model):
    _name = "bf.note.link"
    _description = "Lien d'une note vers une fiche"
    _order = "sequence, id"

    note_id = fields.Many2one("bf.note", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    res_model = fields.Char(string="Modèle", required=True, index=True)
    res_id = fields.Many2oneReference(
        string="ID", model_field="res_model", required=True, index=True
    )
    res_name = fields.Char(string="Nom de la fiche", compute="_compute_res_name", store=True)
    target_ref = fields.Reference(
        string="Fiche",
        selection="_selection_target_model",
        compute="_compute_target_ref",
        inverse="_inverse_target_ref",
        help="Sélecteur modèle + fiche, pour éditer un lien sans saisir le nom "
             "technique du modèle à la main.",
    )

    _sql_constraints = [
        (
            "uniq_note_target",
            "unique(note_id, res_model, res_id)",
            "Cette note est déjà liée à cette fiche.",
        ),
    ]

    @api.model
    def _selection_target_model(self):
        return self.env["bf.note"]._selection_target_model()

    @api.model_create_multi
    def create(self, vals_list):
        # `target_ref` n'est pas stocké : l'ORM ne le poserait qu'après le
        # create, or `res_model` / `res_id` sont requis. On le traduit ici pour
        # que la création en ligne depuis la liste passe.
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            ref = vals.pop("target_ref", None)
            if ref and not vals.get("res_model"):
                model, _sep, rid = str(ref).rpartition(",")
                if model and rid.isdigit():
                    vals["res_model"] = model
                    vals["res_id"] = int(rid)
        return super().create(vals_list)

    @api.depends("res_model", "res_id")
    def _compute_target_ref(self):
        # Même garde que bf.note._compute_res_ref : une valeur hors sélection
        # lèverait un ValueError et casserait le web_read de toute la liste.
        allowed = {model for model, _label in self._selection_target_model()}
        for link in self:
            if link.res_model and link.res_id and link.res_model in allowed:
                link.target_ref = f"{link.res_model},{link.res_id}"
            else:
                link.target_ref = False

    def _inverse_target_ref(self):
        for link in self:
            if link.target_ref:
                link.res_model = link.target_ref._name
                link.res_id = link.target_ref.id

    @api.depends("res_model", "res_id")
    def _compute_res_name(self):
        # Run as the calling user so ACL applies — sudo() here would leak
        # display_name of records the author cannot otherwise read.
        for link in self:
            if link.res_model and link.res_id and link.res_model in self.env:
                try:
                    rec = self.env[link.res_model].browse(link.res_id).exists()
                    if not rec:
                        link.res_name = False
                        continue
                    rec.check_access("read")
                    link.res_name = rec.display_name
                except (AccessError, Exception):
                    link.res_name = False
            else:
                link.res_name = False

    def action_open(self):
        self.ensure_one()
        if not (self.res_model and self.res_id and self.res_model in self.env):
            return False
        target = self.env[self.res_model].browse(self.res_id).exists()
        if not target:
            return False
        try:
            target.check_access_rights("read")
            target.check_access_rule("read")
        except AccessError:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "views": [(False, "form")],
            "target": "current",
        }
