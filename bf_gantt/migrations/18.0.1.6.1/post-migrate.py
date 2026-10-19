"""18.0.1.6.1 : le point qui suit le nom de la société sort du terme traduit.

Avant, le terme était « . It reflects the state… » et sa traduction « . Elle
reflète… ». Le gabarit pose maintenant le point lui-même, sauf quand le nom finit
déjà par un point (« Boréal Services-conseils inc. »).

🔴 À la montée, Odoo rapproche l'ancien terme du nouveau et GARDE l'ancienne
traduction, point compris : la page française lisait alors « Boréal. . Elle
reflète… ». Le catalogue, chargé ensuite sans écrasement, ne la corrige pas. On
retire donc ce point de tête dans chaque langue de la vue, et rien d'autre.
Rejouable : une vue déjà corrigée ne correspond plus au motif.
"""
import json
import logging
import re

_logger = logging.getLogger(__name__)

# Le point posé par le gabarit, suivi du point de tête hérité de l'ancien terme.
POINT_DOUBLE = re.compile(r"""(else '\.'"\s*/>)\s*\.\s+""")


def corriger(texte):
    return POINT_DOUBLE.subn(r"\1 ", texte)


def migrate(cr, version):
    cr.execute("""
        SELECT v.id, v.arch_db FROM ir_ui_view v
        JOIN ir_model_data d ON d.model = 'ir.ui.view' AND d.res_id = v.id
        WHERE d.module = 'bf_gantt' AND d.name = 'portail_echeancier'""")
    for vid, arch in cr.fetchall():
        if not isinstance(arch, dict):
            continue
        neuf, total = {}, 0
        for lang, texte in arch.items():
            if isinstance(texte, str):
                texte, n = corriger(texte)
                total += n
            neuf[lang] = texte
        if total:
            cr.execute("UPDATE ir_ui_view SET arch_db = %s::jsonb WHERE id = %s",
                       [json.dumps(neuf), vid])
        _logger.info("bf_gantt : %s point(s) doublé(s) retiré(s) du pied du portail", total)
