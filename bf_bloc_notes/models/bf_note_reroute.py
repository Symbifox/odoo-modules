"""Re-routage d'une note vers n'importe quelle fiche compatible.

Pendant de `bf.email.reroute` (module `bf_email_management`) : même notion de
cible compatible (tout modèle non transient porteur de `mail.thread`), même
champ « Lien rapide » qui résout une URL Odoo, un numéro ou une référence
`modèle:id` en fiche. Ici on ne re-poste rien : on déplace (ou on ajoute) les
lignes `bf.note.link` de la note.
"""

import logging
import re
from urllib.parse import parse_qs, urlparse

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# « project.task:22299 », « bf.email,17 » — l'échappatoire qui atteint
# n'importe quel modèle compatible, y compris ceux sans alias convivial.
_MODEL_REF_RE = re.compile(r"^([a-z_]+(?:\.[a-z_]+)+)\s*[:,]\s*(\d+)$", re.IGNORECASE)
# « task:22299 », « facture#42 » — raccourcis pour les modèles du quotidien.
_ALIAS_REF_RE = re.compile(r"^([A-Za-zÀ-ÿ]+)\s*[:#]\s*(\d+)$")
_ALIAS_TO_MODEL = {
    "task": "project.task",
    "tache": "project.task",
    "tâche": "project.task",
    "ticket": "helpdesk.ticket",
    "partner": "res.partner",
    "contact": "res.partner",
    "invoice": "account.move",
    "facture": "account.move",
    "move": "account.move",
    "lead": "crm.lead",
    "piste": "crm.lead",
    "order": "sale.order",
    "project": "project.project",
    "projet": "project.project",
    "note": "bf.note",
    "email": "bf.email",
    "courriel": "bf.email",
}
_INVOICE_NAME_RE = re.compile(r"^[A-Za-z0-9]+/\d{4}/\d+$")
_DIGITS_RE = re.compile(r"^\d+$")
_ACTION_SEGMENT_RE = re.compile(r"^action-([\w.]+)$")
# Formes d'URL ambiguës pour le résolveur générique : /odoo/project/<pid>/<tid>
# désigne une tâche, pas le projet nommé par le segment d'action.
_URL_TASK_RE = re.compile(r"/all-tasks/(\d+)|/odoo/project/\d+/(\d+)")

# Ordre d'essai quand seul un identifiant nu est fourni.
_GUESS_MODELS = (
    "project.task",
    "helpdesk.ticket",
    "crm.lead",
    "account.move",
    "res.partner",
)


class BfNoteReroute(models.TransientModel):
    _name = "bf.note.reroute"
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

    quick_paste = fields.Char(
        string="Lien rapide",
        help="Coller une URL Odoo, un numéro de tâche (22299), un nom de facture "
             "(INV/2026/00017), un raccourci (task:22299, ticket:42) ou une "
             "référence technique (bf.email:17) — la cible se résout "
             "automatiquement.",
    )
    # Obligatoire côté vue, pas côté modèle : `required=True` poserait un
    # NOT NULL sur la colonne du transient, donc plus moyen d'instancier le
    # wizard avant que l'utilisateur ait choisi sa cible.
    target_reference = fields.Reference(
        selection="_selection_target_model",
        string="Nouvelle fiche",
        help="Sélectionnez le modèle puis la fiche vers laquelle re-router la note.",
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

    @api.model
    def _selection_target_model(self):
        return self.env["bf.note"]._selection_target_model()

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

    @api.onchange("quick_paste")
    def _onchange_quick_paste(self):
        for wiz in self:
            if not wiz.quick_paste:
                continue
            target = wiz._resolve_quick_paste(wiz.quick_paste)
            if target:
                wiz.target_reference = f"{target._name},{target.id}"

    # ------------------------------------------------------------------
    # Résolution du « lien rapide »
    # ------------------------------------------------------------------
    @api.model
    def _resolve_quick_paste(self, text):
        """Parse une URL, un identifiant ou une référence ; renvoie la fiche ou None."""
        text = (text or "").strip()
        if not text:
            return None
        if text.startswith(("http://", "https://")):
            return self._resolve_url(text)
        match = _MODEL_REF_RE.match(text)
        if match:
            return self._browse_if_allowed(match.group(1).lower(), int(match.group(2)))
        match = _ALIAS_REF_RE.match(text)
        if match:
            model = _ALIAS_TO_MODEL.get(match.group(1).lower())
            return self._browse_if_allowed(model, int(match.group(2))) if model else None
        if _DIGITS_RE.match(text):
            return self._guess_by_id(int(text))
        if _INVOICE_NAME_RE.match(text) and "account.move" in self.env:
            move = self.env["account.move"].search([("name", "=", text)], limit=1)
            return self._browse_if_allowed("account.move", move.id) if move else None
        return None

    @api.model
    def _resolve_url(self, text):
        try:
            parsed = urlparse(text)
        except ValueError:
            return None

        # 1. Ancien schéma /web#model=…&id=… (et ?model=…&id=… des URL de rapport).
        params = {}
        for chunk in (parsed.query, parsed.fragment):
            if chunk:
                params.update({k: v[-1] for k, v in parse_qs(chunk).items()})
        if params.get("model") and _DIGITS_RE.match(params.get("id") or ""):
            record = self._browse_if_allowed(params["model"], int(params["id"]))
            if record:
                return record

        # 2. Formes ambiguës pour le résolveur générique.
        match = _URL_TASK_RE.search(parsed.path)
        if match:
            record = self._browse_if_allowed(
                "project.task", int(match.group(1) or match.group(2))
            )
            if record:
                return record

        segments = [seg for seg in parsed.path.split("/") if seg]
        res_id = next(
            (int(seg) for seg in reversed(segments) if _DIGITS_RE.match(seg)), None
        )
        if res_id is None:
            return None

        # 3. Schéma Odoo 18 /odoo/<action>/<id> : le modèle se déduit de
        #    l'action elle-même (`ir.actions.act_window.path`), donc n'importe
        #    quelle URL de menu fonctionne, pas seulement celles codées ici.
        model = self._model_from_url_segments(segments)
        if model:
            record = self._browse_if_allowed(model, res_id)
            if record:
                return record
        return self._guess_by_id(res_id)

    @api.model
    def _model_from_url_segments(self, segments):
        Action = self.env["ir.actions.act_window"].sudo()
        for seg in reversed(segments):
            if _DIGITS_RE.match(seg):
                continue
            match = _ACTION_SEGMENT_RE.match(seg)
            if match:
                token = match.group(1)
                if token.isdigit():
                    action = Action.browse(int(token)).exists()
                else:
                    action = self.env.ref(token, raise_if_not_found=False)
                if action and action._name == "ir.actions.act_window":
                    return action.res_model
                continue
            action = Action.search([("path", "=", seg)], limit=1)
            if action:
                return action.res_model
        return None

    @api.model
    def _guess_by_id(self, res_id):
        for model in _GUESS_MODELS:
            record = self._browse_if_allowed(model, res_id)
            if record:
                return record
        return None

    @api.model
    def _browse_if_allowed(self, model, res_id):
        """Renvoie la fiche si elle existe, est compatible et lisible — sinon None."""
        if not model or not res_id or model not in self.env:
            return None
        if model not in {name for name, _label in self._selection_target_model()}:
            return None
        record = self.env[model].browse(res_id).exists()
        if not record:
            return None
        try:
            record.check_access("read")
        except AccessError:
            return None
        return record

    # ------------------------------------------------------------------
    # Action
    # ------------------------------------------------------------------
    def action_confirm(self):
        self.ensure_one()
        if not self.note_ids:
            raise UserError("Aucune note à re-router.")
        if not self.target_reference:
            raise UserError("Veuillez sélectionner la fiche de destination.")

        target = self.target_reference
        try:
            target.check_access("read")
        except AccessError as exc:
            raise UserError(
                f"Accès refusé sur {target._name} #{target.id} : {exc}"
            ) from exc

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
