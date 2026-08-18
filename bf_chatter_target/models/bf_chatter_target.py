"""Cible de chatter : une seule façon de désigner la fiche qui reçoit un import.

Trois briques, partagées par tous les importateurs (courriels IMAP, .eml,
notes, SMS, journaux d'appels) :

* ``_thread_model_selection()`` — les modèles porteurs d'un chatter, triés par
  priorité. C'était recopié dans quatre modules, dont un avec douze modèles
  codés en dur qui ne voyaient donc jamais un ordre du jour ni un transfert
  sécurisé.
* ``_resolve(text)`` — résout une URL Odoo, un numéro nu, un raccourci
  (``task:22299``), une référence technique (``bf.email:17``) ou un nom de
  facture (``INV/2026/00017``) en fiche. Fusion des deux résolveurs qui
  avaient forké entre ``bf_email_management`` et ``bf_bloc_notes``.
* ``search_targets(query)`` — la recherche transversale qui alimente le
  sélecteur. Elle réutilise les configurations de ``bf_universal_search``
  quand ce module est installé (icônes, ligne de contexte, biffage des fiches
  terminées) et retombe sinon sur ``name_search``.

Rien ici n'est propre à un type de contenu : un importateur décide *quoi*
poser, ce module décide seulement *où*.
"""

import logging
import re
from urllib.parse import parse_qs, urlparse

from odoo import _, api, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

# « project.task:22299 », « bf.email,17 » — l'échappatoire qui atteint n'importe
# quel modèle compatible, y compris ceux qu'aucun raccourci ne nomme.
_MODEL_REF_RE = re.compile(r"^([a-z_]+(?:\.[a-z_]+)+)\s*[:,]\s*(\d+)$", re.IGNORECASE)
# « task:22299 », « facture#42 » — raccourcis pour les modèles du quotidien.
_ALIAS_REF_RE = re.compile(r"^([A-Za-zÀ-ÿ]+)\s*[:#]\s*(\d+)$")
_ALIAS_TO_MODEL = {
    "task": "project.task",
    "tache": "project.task",
    "tâche": "project.task",
    "ticket": "helpdesk.ticket",
    "billet": "helpdesk.ticket",
    "partner": "res.partner",
    "contact": "res.partner",
    "invoice": "account.move",
    "facture": "account.move",
    "move": "account.move",
    "lead": "crm.lead",
    "piste": "crm.lead",
    "opportunite": "crm.lead",
    "opportunité": "crm.lead",
    "order": "sale.order",
    "commande": "sale.order",
    "project": "project.project",
    "projet": "project.project",
    "note": "bf.note",
    "email": "bf.email",
    "courriel": "bf.email",
    "rencontre": "meeting.record",
    "meeting": "meeting.record",
    "agenda": "meeting.agenda",
    "odj": "meeting.agenda",
}
_INVOICE_NAME_RE = re.compile(r"^[A-Za-z0-9]+/\d{4}/\d+$")
_DIGITS_RE = re.compile(r"^\d+$")
_ACTION_SEGMENT_RE = re.compile(r"^action-([\w.]+)$")
# Formes d'URL ambiguës pour le résolveur générique : /odoo/project/<pid>/<tid>
# désigne une tâche, pas le projet nommé par le segment d'action.
_URL_TASK_RE = re.compile(r"/all-tasks/(\d+)|/odoo/project/\d+/(\d+)")

# Modèles remontés en tête du sélecteur, et essayés dans cet ordre quand seul un
# identifiant nu est fourni.
PRIORITY_MODELS = (
    "project.task",
    "helpdesk.ticket",
    "res.partner",
    "crm.lead",
    "project.project",
    "meeting.record",
    "meeting.agenda",
    "calendar.event",
    "account.move",
    "sale.order",
    "purchase.order",
    "bf.email",
    "bf.note",
    "hosting.service",
    "project.document",
    "project.knowledge.item",
    "secure.transfer",
    "bf.sign.request",
)

# Ordre d'essai quand la saisie n'est qu'un nombre : les modèles où un numéro
# nu veut effectivement dire quelque chose pour un humain.
_GUESS_MODELS = (
    "project.task",
    "helpdesk.ticket",
    "crm.lead",
    "account.move",
    "res.partner",
)

# Icône par défaut quand `bf_universal_search` n'est pas là pour en fournir une.
_FALLBACK_ICONS = {
    "project.task": "fa fa-tasks",
    "helpdesk.ticket": "fa fa-ticket",
    "res.partner": "fa fa-users",
    "crm.lead": "fa fa-star",
    "project.project": "fa fa-folder",
    "meeting.record": "fa fa-comments",
    "meeting.agenda": "fa fa-list-ol",
    "calendar.event": "fa fa-calendar",
    "account.move": "fa fa-file-text-o",
    "sale.order": "fa fa-shopping-cart",
    "purchase.order": "fa fa-shopping-basket",
    "bf.email": "fa fa-envelope",
    "bf.note": "fa fa-sticky-note-o",
    "hosting.service": "fa fa-server",
    "project.document": "fa fa-file-text",
    "project.knowledge.item": "fa fa-lightbulb-o",
    "secure.transfer": "fa fa-lock",
    "bf.sign.request": "fa fa-pencil-square-o",
}
_DEFAULT_ICON = "fa fa-file-o"

# Bornes appliquées aux arguments venus du client : `search_targets` ne doit pas
# pouvoir se transformer en export ni en balayage d'un nombre illimité de modèles.
_MAX_LIMIT = 20
_MAX_FALLBACK_MODELS = 10
_NAME_MAX_LEN = 120


class BfChatterTarget(models.AbstractModel):
    """Abstrait à dessein : ce modèle ne porte aucune donnée, seulement les trois
    services partagés. Un `models.Model` avec `_auto = False` aurait fait la même
    chose, au prix d'un « Model … has no table » en ERREUR à chaque chargement du
    registre — du bruit dans les journaux de production pour rien."""

    _name = "bf.chatter.target"
    _description = "Cible de chatter"

    # ------------------------------------------------------------------
    # Modèles compatibles
    # ------------------------------------------------------------------
    @api.model
    def _thread_model_selection(self):
        """[(modèle, libellé)] — toute fiche non transiente porteuse d'un chatter.

        Deux paramètres système peuvent restreindre l'ensemble ; le second n'est
        gardé que pour ne pas casser les bases où `bf_bloc_notes` l'avait déjà
        renseigné avant l'unification.
        """
        Param = self.env["ir.config_parameter"].sudo()
        raw = (
            Param.get_param("bf_chatter_target.models", "")
            or Param.get_param("bf_bloc_notes.reference_models", "")
        )
        wanted = [m.strip() for m in (raw or "").split(",") if m.strip()]
        if wanted:
            domain = [("model", "in", wanted), ("transient", "=", False)]
        else:
            domain = [("is_mail_thread", "=", True), ("transient", "=", False)]
        records = self.env["ir.model"].sudo().search(domain)
        # `ir.model` garde des lignes pour des modèles absents du registre
        # (module désinstallé) : les proposer donnerait un Reference cassé.
        items = [(r.model, r.name) for r in records if r.model and r.model in self.env]
        items.sort(key=lambda item: (
            PRIORITY_MODELS.index(item[0])
            if item[0] in PRIORITY_MODELS
            else len(PRIORITY_MODELS),
            item[1] or item[0],
        ))
        return items

    @api.model
    def _thread_models(self):
        """Ensemble des noms techniques compatibles, pour un test d'appartenance."""
        return {model for model, _label in self._thread_model_selection()}

    # ------------------------------------------------------------------
    # Résolution d'une référence collée
    # ------------------------------------------------------------------
    @api.model
    def _resolve(self, text):
        """Renvoie la fiche désignée par `text`, ou None. Ne lève jamais.

        Formes acceptées :
          * une URL Odoo (schéma 18 ``/odoo/<action>/<id>``, ancien
            ``/web#model=…&id=…``, ``/odoo/project/<pid>/<tid>``) ;
          * ``project.task:22299`` ou ``project.task,22299`` ;
          * ``task:22299``, ``facture#42`` et les autres raccourcis ;
          * ``22299`` seul, essayé sur les modèles où un numéro parle ;
          * ``INV/2026/00017``, cherché par nom sur ``account.move``.
        """
        text = (text or "").strip()
        if not text:
            return None
        try:
            if text.startswith(("http://", "https://")):
                return self._resolve_url(text)
            match = _MODEL_REF_RE.match(text)
            if match:
                return self._browse_if_allowed(
                    match.group(1).lower(), int(match.group(2))
                )
            match = _ALIAS_REF_RE.match(text)
            if match:
                model = _ALIAS_TO_MODEL.get(match.group(1).lower())
                return (
                    self._browse_if_allowed(model, int(match.group(2)))
                    if model else None
                )
            if _DIGITS_RE.match(text):
                return self._guess_by_id(int(text))
            if _INVOICE_NAME_RE.match(text) and "account.move" in self.env:
                move = self.env["account.move"].search([("name", "=", text)], limit=1)
                return self._browse_if_allowed("account.move", move.id) if move else None
        except Exception:  # pragma: no cover — le résolveur est un confort
            _logger.debug("bf.chatter.target: résolution échouée sur %r", text,
                          exc_info=True)
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

        # 3. Schéma Odoo 18 /odoo/<action>/<id> : le modèle se déduit de l'action
        #    elle-même (`ir.actions.act_window.path`), donc n'importe quelle URL
        #    de menu fonctionne, pas seulement celles codées ici.
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
        """La fiche si elle existe, porte un chatter et est lisible — sinon None."""
        if not model or not res_id or model not in self.env:
            return None
        if model not in self._thread_models():
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
    # Recherche transversale (RPC du sélecteur)
    # ------------------------------------------------------------------
    @api.model
    @api.readonly
    def search_targets(self, query, limit=5):
        """Fiches compatibles correspondant à `query`, groupées par modèle.

        Même forme de retour que ``bf.universal.search.search_all`` :

        [{"model": "project.task", "model_label": "Tâches", "icon": "fa fa-tasks",
          "results": [{"id": 42, "name": "…", "detail": "Projet · Étape",
                       "closed": False}]}]

        Le premier groupe, quand il existe, est la référence exacte tirée de la
        saisie (URL collée, ``task:22299``, ``INV/2026/00017``) : c'est ce qui
        remplace l'ancien champ « Lien rapide » séparé.
        """
        query = (query or "").strip()
        if len(query) < 2:
            return []
        try:
            limit = min(max(int(limit or 5), 1), _MAX_LIMIT)
        except (TypeError, ValueError):
            limit = 5

        allowed = dict(self._thread_model_selection())
        groups = []
        seen = set()

        exact = self._resolve(query)
        if exact is not None:
            seen.add((exact._name, exact.id))
            groups.append({
                "model": exact._name,
                "model_label": _("Référence exacte"),
                "icon": _FALLBACK_ICONS.get(exact._name, "fa fa-crosshairs"),
                "results": [{
                    "id": exact.id,
                    "name": self._truncate(exact.display_name),
                    "detail": allowed.get(exact._name, exact._name),
                    "closed": False,
                }],
            })

        covered = set()
        if "bf.universal.search" in self.env:
            groups.extend(self._universal_groups(query, allowed, seen, limit, covered))

        groups.extend(self._name_search_groups(query, allowed, seen, limit, covered))
        return groups

    @api.model
    def _universal_groups(self, query, allowed, seen, limit, covered):
        """Groupes tirés des configurations de `bf_universal_search`.

        `covered` est renseigné au passage : un modèle qui a une configuration
        active a déjà été cherché, donc le repli ``name_search`` doit le sauter
        même quand il n'a rien rendu.
        """
        Config = self.env["bf.universal.search.config"].sudo()
        covered.update(
            Config.search([("active", "=", True)]).mapped("model_name") or []
        )
        try:
            raw = self.env["bf.universal.search"].search_all(
                query, model_filters=list(allowed), limit=limit,
            )
        except Exception:
            _logger.warning(
                "bf.chatter.target: recherche universelle indisponible", exc_info=True,
            )
            return []
        groups = []
        for group in raw:
            model = group.get("model")
            if model not in allowed:
                continue
            results = []
            for result in group.get("results") or []:
                key = (model, result["id"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(result)
            if results:
                groups.append({
                    "model": model,
                    "model_label": group.get("model_label") or allowed[model],
                    "icon": group.get("icon") or _FALLBACK_ICONS.get(model, _DEFAULT_ICON),
                    "results": results,
                })
        return groups

    @api.model
    def _name_search_groups(self, query, allowed, seen, limit, covered):
        """Repli ``name_search`` sur les modèles prioritaires sans configuration.

        Sans `bf_universal_search`, c'est toute la recherche ; avec, ça ne
        couvre que le reste (bon de commande, événement…), donc quelques
        requêtes au plus.
        """
        models_to_scan = [
            model for model in PRIORITY_MODELS
            if model in allowed and model not in covered
        ][:_MAX_FALLBACK_MODELS]
        groups = []
        for model in models_to_scan:
            Model = self.env[model]
            try:
                self.env["ir.model.access"].check(model, "read")
            except AccessError:
                continue
            try:
                matches = Model.name_search(query, limit=limit)
            except Exception:
                _logger.warning(
                    "bf.chatter.target: name_search a échoué sur %s", model,
                    exc_info=True,
                )
                continue
            results = []
            for res_id, label in matches:
                key = (model, res_id)
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "id": res_id,
                    "name": self._truncate(label),
                    "detail": "",
                    "closed": False,
                })
            if results:
                groups.append({
                    "model": model,
                    "model_label": allowed[model],
                    "icon": _FALLBACK_ICONS.get(model, _DEFAULT_ICON),
                    "results": results,
                })
        return groups

    # ------------------------------------------------------------------
    # Divers
    # ------------------------------------------------------------------
    @api.model
    def _truncate(self, value, max_len=_NAME_MAX_LEN):
        value = " ".join((value or "").split())
        if len(value) > max_len:
            return value[: max_len - 1].rstrip() + "…"
        return value
