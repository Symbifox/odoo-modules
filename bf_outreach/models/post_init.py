# -*- coding: utf-8 -*-
"""Enregistrements posés après installation, quand le module visé est présent.

`bf_universal_search` n'est pas une dépendance : le démarchage doit rester
installable sans lui. On se contente d'ajouter la configuration si le modèle
existe au moment de l'installation.
"""
import logging

_logger = logging.getLogger(__name__)

# Ancien nom livré, semé en français dans la valeur source (en_US) par les
# versions précédentes : sert à reconnaître un enregistrement jamais retouché.
LEGACY_NAME = "Cibles de démarchage"

# Traductions du nom, posées sur l'enregistrement (la source est l'anglais).
NAME_TRANSLATIONS = {"fr_CA": "Cibles de démarchage"}

SEARCH_CONFIG = {
    "name": "Outreach targets",
    "search_fields": "name,contact_name,email,phone,source",
    "detail_fields": "campaign_id,stage_id",
    "closed_domain": "[('stage_type', 'in', ['won', 'lost'])]",
    "order": "next_action_date asc",
    "min_length": 2,
    "icon": "fa fa-bullhorn",
    # Clé de regroupement de la recherche universelle, pas un libellé : reste.
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
    existing = Config.sudo().search([("model_id", "=", model.id)])
    if existing:
        for config in existing:
            try:
                _refresh_legacy_name(config)
            except Exception:  # noqa: BLE001 — une commodité ne bloque jamais une mise à jour
                _logger.exception("bf_outreach : nom de la recherche universelle non rafraîchi")
        return False
    values = dict(SEARCH_CONFIG, model_id=model.id)
    # On ne renseigne que les colonnes réellement présentes : la recherche
    # universelle a gagné des options au fil du temps.
    values = {key: val for key, val in values.items() if key in Config._fields}
    try:
        # Contexte imposé : le crochet et la migration n'ont pas de langue
        # fiable, la valeur source doit être l'anglais.
        config = Config.sudo().with_context(lang="en_US").create(values)
        _set_name_translations(config, NAME_TRANSLATIONS)
    except Exception:  # noqa: BLE001 — une commodité ne bloque jamais une installation
        _logger.exception("bf_outreach : enregistrement dans la recherche universelle refusé")
        return False
    _logger.info("bf_outreach : cibles ajoutées à la recherche universelle")
    return True


def _installed_langs(env):
    return {code for code, _name in env["res.lang"].get_installed()}


def _set_name_translations(config, translations):
    """Pose les traductions du nom, pour les seules langues actives.

    `update_field_translations` refuse une langue inactive : on filtre.
    """
    if not config._fields["name"].translate:
        return
    langs = _installed_langs(config.env) | {"en_US"}
    translations = {lang: value for lang, value in translations.items() if lang in langs}
    if translations:
        config.sudo().update_field_translations("name", translations)


def _refresh_legacy_name(config):
    """Passe à la source anglaise un nom semé en français par une version antérieure.

    Ne touche que ce qui vaut encore l'ancienne valeur livrée : un nom ou une
    traduction retouchés par le client restent tels quels.
    """
    if not config._fields["name"].translate:
        return
    config.flush_recordset(["name"])
    config.env.cr.execute(
        f'SELECT name FROM "{config._table}" WHERE id = %s', (config.id,)
    )
    row = config.env.cr.fetchone()
    stored = (row and row[0]) or {}
    if not isinstance(stored, dict) or stored.get("en_US") != LEGACY_NAME:
        return
    translations = {"en_US": SEARCH_CONFIG["name"]}
    for lang in _installed_langs(config.env) - {"en_US"}:
        current = stored.get(lang)
        if current not in (None, LEGACY_NAME):
            continue  # traduction retouchée : on la garde
        if lang in NAME_TRANSLATIONS:
            translations[lang] = NAME_TRANSLATIONS[lang]
        elif lang.startswith("fr"):
            # Un autre français voyait l'ancien nom : il le garde.
            translations[lang] = LEGACY_NAME
        elif current == LEGACY_NAME:
            # Langue non française qui portait le français : retour à la source.
            translations[lang] = False
    config.sudo().update_field_translations("name", translations)
    _logger.info("bf_outreach : nom de la recherche universelle passé à la source anglaise")
