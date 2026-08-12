from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval
from odoo.tools.safe_eval import datetime as safe_datetime
from odoo.tools.safe_eval import dateutil as safe_dateutil
from odoo.tools.safe_eval import time as safe_time


class BfUniversalSearchConfig(models.Model):
    _name = "bf.universal.search.config"
    _description = "Configuration de recherche universelle"
    _order = "sequence, id"

    name = fields.Char(string="Libellé", required=True)
    model_id = fields.Many2one(
        "ir.model",
        string="Modèle",
        required=True,
        ondelete="cascade",
    )
    model_name = fields.Char(
        related="model_id.model",
        string="Nom technique",
        store=True,
    )
    search_fields = fields.Char(
        string="Champs de recherche",
        required=True,
        help="Noms de champs séparés par des virgules (ex: name,email,phone)",
    )
    detail_fields = fields.Char(
        string="Champs de contexte",
        help="Champs affichés à droite du résultat, séparés par des virgules "
             "(ex: project_id,stage_id). Un many2one affiche son nom, "
             "une sélection son libellé.",
    )
    domain = fields.Char(
        string="Filtre",
        help="Domaine Odoo appliqué en plus de la recherche textuelle "
             "(ex: [('move_type','!=','entry')] pour ne garder que les factures).",
    )
    closed_domain = fields.Char(
        string="Domaine « terminé »",
        help="Les fiches correspondant à ce domaine passent en fin de liste, "
             "grisées et biffées (ex: [('state','in',['1_done','1_canceled'])]).",
    )
    order = fields.Char(
        string="Tri",
        help="Ordre passé au search_read (ex: write_date desc). "
             "Vide = ordre par défaut du modèle.",
    )
    search_by_id = fields.Boolean(
        string="Recherche par numéro",
        default=False,
        help="Si coché, une requête numérique (ex: 142 ou #142) retrouve aussi "
             "la fiche portant cet identifiant.",
    )
    min_length = fields.Integer(
        string="Longueur minimale",
        default=2,
        help="Nombre minimal de caractères avant d'interroger ce modèle. "
             "À monter à 3 ou 4 pour les modèles volumineux (SMS, courriels).",
    )
    icon = fields.Char(
        string="Icône FontAwesome",
        default="fa fa-search",
        help="Classe CSS FontAwesome (ex: fa fa-users)",
    )
    category = fields.Char(
        string="Catégorie",
        required=True,
        help="Clé de catégorie pour le regroupement (ex: search_contacts)",
    )
    sequence = fields.Integer(string="Séquence", default=100)
    limit = fields.Integer(
        string="Limite par modèle",
        default=5,
        help="Nombre maximum de résultats retournés par modèle",
    )
    active = fields.Boolean(string="Actif", default=True)

    def _domain_eval_context(self):
        """Same helpers as an ir.filters domain, so dates can be relative."""
        self.ensure_one()
        return {
            "uid": self.env.uid,
            "context_today": lambda: fields.Date.context_today(self),
            "datetime": safe_datetime,
            "dateutil": safe_dateutil,
            "time": safe_time,
        }

    def _eval_domain(self, raw):
        """Evaluate a domain stored on this config. Returns [] when empty.

        Private on purpose: this runs ``safe_eval`` on a stored string, so it
        must never be reachable over RPC (Odoo rejects underscore-prefixed
        method names in ``call_kw``). Only the search engine and the field
        constraint call it, and both pass a config-authored value.
        """
        self.ensure_one()
        if not raw:
            return []
        return safe_eval(raw, self._domain_eval_context())

    @api.constrains("domain", "closed_domain")
    def _check_domains(self):
        for config in self:
            for field_name in ("domain", "closed_domain"):
                raw = config[field_name]
                if not raw:
                    continue
                try:
                    parsed = config._eval_domain(raw)
                except Exception as exc:
                    raise ValidationError(
                        _("Domaine invalide sur « %(label)s » : %(error)s",
                          label=config.name, error=exc)
                    ) from exc
                if not isinstance(parsed, list):
                    raise ValidationError(
                        _("Le domaine de « %(label)s » doit être une liste.",
                          label=config.name)
                    )
