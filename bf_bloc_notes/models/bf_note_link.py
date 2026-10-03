from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class BfNoteLink(models.Model):
    _name = "bf.note.link"
    _description = "Link from a note to a record"
    _order = "sequence, id"

    note_id = fields.Many2one("bf.note", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    res_model = fields.Char(string="Model", required=True, index=True)
    res_id = fields.Many2oneReference(
        string="ID", model_field="res_model", required=True, index=True
    )
    # Calculé sous les droits de QUI LIT, plus stocké. Stocké, il
    # se calculait pour l'auteur, et un collègue relisait sur une note partagée
    # le nom d'une fiche que lui ne peut pas ouvrir.
    res_name = fields.Char(string="Record name", compute="_compute_res_name",
                           compute_sudo=False)
    target_ref = fields.Reference(
        string="Form",
        selection="_selection_target_model",
        compute="_compute_target_ref",
        inverse="_inverse_target_ref",
        help="Model + record picker, to edit a link without typing the "
             "model's technical name by hand.",
    )

    _sql_constraints = [
        (
            "uniq_note_target",
            "unique(note_id, res_model, res_id)",
            "This note is already linked to this record.",
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

    def write(self, vals):
        """Un lien ne change pas de note hors superutilisateur.

        Le nom d'une fiche liée se calcule sous les droits de l'auteur de la
        note : un lien déplacé vers la note d'une autre personne verrait son
        nom recalculé sous les droits de celle-ci. Toutes les
        commandes x2many de la note (1, id, {note_id}) passent par ici ; un
        (4, id) écrit la note cible, gardée par sa règle d'écriture. Aucun
        chemin du module ne déplace un lien."""
        if "note_id" in vals and not self.env.su:
            cible = vals["note_id"]
            cible = cible.id if hasattr(cible, "id") else cible
            if any(link.note_id.id != cible for link in self):
                raise AccessError(_("A link cannot be moved to another note."))
        return super().write(vals)

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
    @api.depends_context("uid")
    def _compute_res_name(self):
        # Même règle que l'API mobile (`_mobile_link_name`) : une fiche absente
        # ou interdite rendent la même chose, aucun nom. ⚠️ Jamais en sudo :
        # `compute_sudo=False` sur le champ, et `sudo(False)` ici au cas où un
        # appelant aurait passé un environnement superutilisateur. Une lecture
        # par modèle, pas par lien : la liste et le kanban en affichent des
        # dizaines.
        par_modele = defaultdict(set)
        for link in self:
            link.res_name = False
            if link.res_model and link.res_id and link.res_model in self.env:
                par_modele[link.res_model].add(link.res_id)
        noms = {}
        for model_name, ids in par_modele.items():
            try:
                lisibles = self.env[model_name].sudo(False).browse(list(ids)).exists() \
                    ._filtered_access("read")
                noms[model_name] = {rec.id: rec.display_name for rec in lisibles}
            except Exception:  # noqa: BLE001 un nom illisible ne casse pas la liste
                noms[model_name] = {}
        for link in self:
            link.res_name = noms.get(link.res_model, {}).get(link.res_id) or False

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
