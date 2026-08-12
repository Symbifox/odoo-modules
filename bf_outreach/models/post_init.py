# -*- coding: utf-8 -*-
"""Enregistrements posés après installation, quand le module visé est présent.

`bf_universal_search` n'est pas une dépendance : le démarchage doit rester
installable sans lui. On se contente d'ajouter la configuration si le modèle
existe au moment de l'installation.
"""
import logging

_logger = logging.getLogger(__name__)

SEARCH_CONFIG = {
    "name": "Cibles de démarchage",
    "search_fields": "name,contact_name,email,phone,source",
    "detail_fields": "campaign_id,stage_id",
    "closed_domain": "[('stage_type', 'in', ['won', 'lost'])]",
    "order": "next_action_date asc",
    "min_length": 2,
    "icon": "fa fa-bullhorn",
    "category": "Démarchage",
    "sequence": 36,
}


def register_universal_search(env):
    """Ajoute les cibles à la recherche universelle, si elle est installée."""
    Config = env.get("bf.universal.search.config")
    if Config is None:
        return False
    model = env["ir.model"]._get("bf.outreach.target")
    if not model:
        return False
    if Config.sudo().search_count([("model_id", "=", model.id)]):
        return False
    values = dict(SEARCH_CONFIG, model_id=model.id)
    # On ne renseigne que les colonnes réellement présentes : la recherche
    # universelle a gagné des options au fil du temps.
    values = {key: val for key, val in values.items() if key in Config._fields}
    try:
        Config.sudo().create(values)
    except Exception:  # noqa: BLE001 — une commodité ne bloque jamais une installation
        _logger.exception("bf_outreach : enregistrement dans la recherche universelle refusé")
        return False
    _logger.info("bf_outreach : cibles ajoutées à la recherche universelle")
    return True
