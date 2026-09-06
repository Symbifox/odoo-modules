import logging
import re

from odoo import api, models
from odoo.exceptions import AccessError
from odoo.osv import expression

_logger = logging.getLogger(__name__)

# A query like "142" or "#142" is treated as a record id lookup on the
# configs that opt in via search_by_id.
_ID_QUERY_RE = re.compile(r"^#?(\d{1,9})$")

_NAME_MAX_LEN = 120
_DETAIL_MAX_LEN = 90

# Upper bounds applied to the RPC arguments so a caller can't turn search_all
# into a bulk export or a fan-out across an unbounded number of models.
_MAX_LIMIT = 20
_MAX_CONFIGS = 40


class BfUniversalSearch(models.AbstractModel):
    _name = "bf.universal.search"
    _description = "Recherche universelle"
    # `AbstractModel` : ce modèle n'a ni champ ni table, il ne sert que de point
    # d'entrée RPC pour le composant OWL. Déclaré `models.Model` + `_auto = False`,
    # il entrait dans `Registry.check_tables_exist()`, qui ne dispense que
    # `_abstract` et les modèles à `_table_query` — d'où un `ERROR
    # odoo.modules.registry: Model <ce modèle> has no table.` journalisé à chaque
    # passe du chargeur sur une base neuve.

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _truncate(value, max_len):
        value = " ".join((value or "").split())
        if len(value) > max_len:
            return value[: max_len - 1].rstrip() + "…"
        return value

    def _format_detail(self, Model, rec, detail_field_names):
        """Build the right-hand context line (project, stage, client...)."""
        parts = []
        for field_name in detail_field_names:
            value = rec.get(field_name)
            if not value:
                continue
            field = Model._fields[field_name]
            if field.type == "many2one":
                parts.append(value[1])
            elif field.type == "selection":
                selection = dict(field._description_selection(self.env) or [])
                parts.append(selection.get(value, value))
            elif field.type in ("date", "datetime"):
                parts.append(str(value)[:10])
            elif field.type == "boolean":
                continue
            else:
                parts.append(str(value))
        return self._truncate(" · ".join(parts[:3]), _DETAIL_MAX_LEN)

    def _text_domain(self, query, valid_fields, rec_id, search_by_id):
        """OR domain over the searchable fields (+ the id when applicable)."""
        leaves = [[(field_name, "ilike", query)] for field_name in valid_fields]
        if rec_id is not None and search_by_id:
            leaves.append([("id", "=", rec_id)])
        return expression.OR(leaves)

    # ------------------------------------------------------------------
    # Public RPC
    # ------------------------------------------------------------------
    @api.model
    @api.readonly
    def search_all(self, query, model_filters=None, limit=5):
        """Search across all configured models, respecting ACLs and record rules.

        Returns a list of groups:
        [
            {
                "model": "project.task",
                "model_label": "Tâches",
                "icon": "fa fa-tasks",
                "category": "search_projects",
                "results": [
                    {
                        "id": 42,
                        "name": "Migrer le serveur de courriel",
                        "detail": "Projet Démo · En cours",
                        "closed": False,
                    },
                    ...
                ],
            },
            ...
        ]

        Records matching the config's ``closed_domain`` (done / cancelled) are
        pushed to the end of their group and flagged ``closed``, so the client can
        grey and strike them out.
        """
        query = (query or "").strip()
        if len(query) < 2:
            return []

        # Bound the caller-supplied limit: it is only a fallback for configs
        # whose own limit is 0, but an unbounded value would turn the endpoint
        # into a bulk dump.
        try:
            fallback_limit = min(max(int(limit or 5), 1), _MAX_LIMIT)
        except (TypeError, ValueError):
            fallback_limit = 5

        # Normalise model_filters to a set of technical model names. A single
        # string is accepted; anything non-iterable or non-string is dropped
        # rather than crashing the ``in`` test below. ``filter_active`` records
        # whether the caller asked to restrict at all, so a garbage filter
        # narrows to nothing instead of silently returning every model.
        filter_active = model_filters is not None and model_filters != []
        if isinstance(model_filters, str):
            model_filters = {model_filters}
        elif isinstance(model_filters, (list, tuple, set, frozenset)):
            model_filters = {m for m in model_filters if isinstance(m, str)}
        else:
            model_filters = set()

        id_match = _ID_QUERY_RE.match(query)
        rec_id = int(id_match.group(1)) if id_match else None

        configs = self.env["bf.universal.search.config"].search(
            [("active", "=", True)], limit=_MAX_CONFIGS
        )
        if filter_active:
            configs = configs.filtered(lambda c: c.model_name in model_filters)

        groups = []
        for config in configs:
            model_name = config.model_name
            if not model_name or model_name not in self.env:
                continue
            if len(query) < max(config.min_length or 2, 2):
                continue

            # Read access — record rules are applied by search_read itself.
            try:
                self.env["ir.model.access"].check(model_name, "read")
            except AccessError:
                continue

            Model = self.env[model_name]
            valid_fields = [
                f.strip()
                for f in (config.search_fields or "").split(",")
                if f.strip() and f.strip() in Model._fields
            ]
            if not valid_fields and not (rec_id is not None and config.search_by_id):
                continue

            detail_field_names = [
                f.strip()
                for f in (config.detail_fields or "").split(",")
                if f.strip() and f.strip() in Model._fields
            ]

            search_limit = min(config.limit or fallback_limit, _MAX_LIMIT)
            base_domain = self._text_domain(
                query, valid_fields, rec_id, config.search_by_id
            )
            try:
                extra_domain = config._eval_domain(config.domain)
                closed_domain = config._eval_domain(config.closed_domain)
            except Exception:
                _logger.warning(
                    "Universal search: invalid domain on config %s (%s) — skipped",
                    config.name, model_name, exc_info=True,
                )
                continue
            if extra_domain:
                base_domain = expression.AND([base_domain, extra_domain])
            read_fields = list(set(valid_fields + detail_field_names + ["display_name"]))
            order = config.order or None

            try:
                if closed_domain:
                    # Open records first, then fill the remaining slots with closed
                    # ones — a done task must never crowd out a live one.
                    open_domain = expression.AND([
                        base_domain,
                        ["!"] + expression.normalize_domain(closed_domain),
                    ])
                    records = Model.search_read(
                        open_domain, fields=read_fields, limit=search_limit, order=order
                    )
                    open_count = len(records)
                    if open_count < search_limit:
                        records = records + Model.search_read(
                            expression.AND([base_domain, closed_domain]),
                            fields=read_fields,
                            limit=search_limit - open_count,
                            order=order,
                        )
                else:
                    open_count = None
                    records = Model.search_read(
                        base_domain, fields=read_fields, limit=search_limit, order=order
                    )
            except Exception:
                _logger.warning(
                    "Universal search: error searching %s", model_name, exc_info=True
                )
                continue

            if not records:
                continue

            results = []
            for index, rec in enumerate(records):
                name = rec.get("display_name") or (
                    rec.get(valid_fields[0]) if valid_fields else ""
                )
                results.append({
                    "id": rec["id"],
                    "name": self._truncate(str(name or ""), _NAME_MAX_LEN),
                    "detail": self._format_detail(Model, rec, detail_field_names),
                    "closed": open_count is not None and index >= open_count,
                })

            groups.append({
                "model": model_name,
                "model_label": config.name,
                "icon": config.icon or "fa fa-search",
                "category": config.category,
                "results": results,
            })

        return groups
