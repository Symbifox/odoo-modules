"""Re-routage d'une note vers n'importe quelle fiche compatible.

Pendant de `bf.email.reroute` : ici on ne re-poste rien, on déplace (ou on
ajoute) les lignes `bf.note.link` de la note. La désignation de la cible, elle,
est commune — modèles compatibles, résolution d'une URL ou d'un raccourci
collé, contrôle d'accès — et vit dans `bf.chatter.target.mixin` depuis la
2.9.0. Ce fichier en portait la version la plus complète : c'est elle qui a été
promue au socle, le résolveur de `bf_email_management` ayant été retiré.
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class BfNoteReroute(models.TransientModel):
    _name = "bf.note.reroute"
    _inherit = ["bf.chatter.target.mixin"]
    _description = "Re-router une note vers une autre fiche"

    note_ids = fields.Many2many(
        comodel_name="bf.note",
        string="Notes",
        # Sans `active_test`, une note archivée est écrite dans la relation mais
        # relue vide : le wizard répondrait « Aucune note à re-router » alors
        # qu'on vient de la lui passer. Or re-router puis archiver est un
        # enchaînement courant, et la liste a un filtre « Archivées ».
        context={"active_test": False},
    )
    note_count = fields.Integer(string="Nb notes", compute="_compute_note_count")
    sample_name = fields.Char(string="Note", compute="_compute_sample")
    current_target = fields.Char(string="Lien actuel", compute="_compute_sample")

    # Obligatoire côté vue, pas côté modèle : `required=True` poserait un
    # NOT NULL sur la colonne du transient, donc plus moyen d'instancier le
    # wizard avant que l'utilisateur ait choisi sa cible.
    target_reference = fields.Reference(
        string="Nouvelle fiche",
        help="Cherchez la fiche par son nom, son numéro, un raccourci "
             "(task:22299, ticket:42), une référence technique (bf.email:17) "
             "ou collez une URL Odoo.",
    )
    mode = fields.Selection(
        selection=[
            ("replace", "Remplacer les liens existants"),
            ("add", "Ajouter aux liens existants"),
        ],
        string="Mode",
        default="replace",
        required=True,
    )

    state = fields.Selection(
        selection=[("draft", "Prêt"), ("done", "Terminé")],
        default="draft",
    )
    result_text = fields.Text(string="Résultat", readonly=True)

    # ------------------------------------------------------------------
    # Defaults / computes
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        ctx = self.env.context
        ids = ctx.get("default_note_ids") or ctx.get("active_ids") or []
        if isinstance(ids, list) and ids and isinstance(ids[0], (list, tuple)):
            # commande (6, 0, ids) posée par le contexte du bouton
            ids = ids[0][2] if len(ids[0]) > 2 else []
        if ids:
            vals.setdefault("note_ids", [(6, 0, ids)])
        return vals

    @api.depends("note_ids")
    def _compute_note_count(self):
        for wiz in self:
            wiz.note_count = len(wiz.note_ids)

    @api.depends("note_ids")
    def _compute_sample(self):
        for wiz in self:
            first = wiz.note_ids[:1]
            wiz.sample_name = first.name or ""
            labels = [
                link.res_name or f"{link.res_model} #{link.res_id}"
                for link in first.link_ids
            ]
            wiz.current_target = ", ".join(labels) if labels else "Aucun lien"

    # ------------------------------------------------------------------
    # Résolution d'une référence collée
    # ------------------------------------------------------------------
    @api.model
    def _resolve_quick_paste(self, text):
        """Point d'entrée conservé : la logique est passée au socle en 2.9.0.

        Le sélecteur résout lui-même la saisie, donc plus personne ne l'appelle
        depuis l'interface — mais la suite de tests s'y accroche, et c'est le
        seul endroit où l'on documente que ce module ne porte plus sa copie.
        """
        return self._resolve_chatter_target(text)

    # ------------------------------------------------------------------
    # Action
    # ------------------------------------------------------------------
    def action_confirm(self):
        self.ensure_one()
        if not self.note_ids:
            raise UserError("Aucune note à re-router.")
        # Re-router une note ne publie rien sur la cible : la lecture suffit.
        target = self._get_chatter_target("read")

        Link = self.env["bf.note.link"]
        results = []
        successes = 0
        for note in self.note_ids:
            try:
                with self.env.cr.savepoint():
                    existing = note.link_ids.filtered(
                        lambda link, m=target._name, r=target.id:
                        link.res_model == m and link.res_id == r
                    )
                    if self.mode == "replace":
                        (note.link_ids - existing).unlink()
                    if not existing:
                        Link.create({
                            "note_id": note.id,
                            "res_model": target._name,
                            "res_id": target.id,
                        })
                results.append(f"OK {note.display_name}")
                successes += 1
            except (AccessError, UserError, ValueError) as exc:
                _logger.warning(
                    "Re-routage bf.note #%s échoué : %s", note.id, exc, exc_info=True,
                )
                results.append(f"ERR {note.display_name} → {exc}")

        self.write({
            "state": "done",
            "result_text": "\n".join(results)
            + f"\n\n{successes}/{len(self.note_ids)} note(s) re-routée(s) vers "
            + f"{target.display_name}.",
        })

        if successes == 1 and len(self.note_ids) == 1:
            # Re-routage unitaire : on rouvre la note, ses liens à jour.
            return {
                "type": "ir.actions.act_window",
                "res_model": "bf.note",
                "res_id": self.note_ids.id,
                "views": [(False, "form")],
                "target": "current",
            }
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
