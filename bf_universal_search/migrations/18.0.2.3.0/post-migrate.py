"""18.0.2.3.0 — la palette cherche le corps ENTIER des courriels.

La fiche de recherche des courriels listait `subject,email_from,body_preview`.
`body_preview` est un `Char(300)` : mesuré sur une base réelle le 2026-09-13, il
couvre **11,2 %** du texte reçu. `bf.email.body_text`, né avec
`bf_email_management` 11.34.0, porte le corps entier et un index trigrammes.

⚠️ La fiche est créée avec `noupdate: True` : le fichier de données ne la
rattrapera jamais tout seul, il faut l'écrire ici.
"""
import logging

_logger = logging.getLogger(__name__)

ANCIEN = "subject,email_from,body_preview"
NOUVEAU = "subject,email_from,body_text"


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "bf.email" not in env or "body_text" not in env["bf.email"]._fields:
        _logger.info(
            "Universal search 2.3.0 : `bf.email.body_text` absent, la fiche "
            "des courriels reste sur l'aperçu")
        return
    config = env.ref("bf_universal_search.search_config_emails",
                     raise_if_not_found=False)
    if not config:
        return
    if (config.search_fields or "").strip() != ANCIEN:
        _logger.info(
            "Universal search 2.3.0 : fiche des courriels modifiée à la main "
            "(%s), laissée telle quelle", config.search_fields)
        return
    config.search_fields = NOUVEAU
    # ⚠️ Flush explicite : une écriture ORM en fin de migration reste dans le
    # cache jusqu'au prochain flush, et ce qui lit la base entre-temps (une
    # autre migration, un contrôle) verrait encore l'ancienne valeur.
    env.flush_all()
    _logger.info("Universal search 2.3.0 : la palette lit désormais body_text")
