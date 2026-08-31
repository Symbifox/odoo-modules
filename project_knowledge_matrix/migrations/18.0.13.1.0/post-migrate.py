"""Rafraîchir le corps des gabarits de courriel vers `_pkm_brand()`.

Les gabarits lisaient `report_brand_*` directement sur `res.company`. Ce champ
vient de `bf_onboarding_base` 18.0.2.0.0 ; sa 1.0.0 ne l'a pas, et les deux
tournent en production. Une lecture directe lève donc une AttributeError chez
un locataire resté sur l'ancienne version — la garde
« company and company.champ » teste la société, pas le champ.

La 18.0.13.1.0 fait tout passer par `res.company._pkm_brand()`, qui saute les
champs absents du registre et se replie sur les couleurs du cœur.

POURQUOI UNE PASSE DE MIGRATION
-------------------------------
Les six gabarits sont `noupdate` EN BASE, quoi qu'en dise le fichier de
données : `document_mail_templates.xml` déclare pourtant `noupdate="0"`. Le
drapeau de la base l'emporte, et une mise à niveau ordinaire ne réécrit donc
PAS `body_html`. Sans cette passe, le fichier dirait une chose et la production
en rendrait une autre — indéfiniment, et sans que rien ne le signale.

C'est la même mécanique que la passe 18.0.9.13.0, écrite pour la même raison
lors du passage aux champs de marque. Elle est reprise ici telle quelle.

Les vues QWeb des rapports PDF, elles, ne sont pas `noupdate` : le `-u` les
rafraîchit tout seul.
"""

import logging
import os
from xml.etree import ElementTree as ET

from odoo import SUPERUSER_ID, api
from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)

FICHIERS = [
    "document_mail_templates.xml",
    "document_report_mail_template.xml",
]


def _champ(record, nom):
    champ = record.find("./field[@name='%s']" % nom)
    if champ is None:
        return None
    morceaux = []
    if champ.text:
        morceaux.append(champ.text)
    for enfant in champ:
        morceaux.append(ET.tostring(enfant, encoding="unicode"))
    return "".join(morceaux).strip()


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = env["res.lang"].search([("active", "=", True)]).mapped("code") or ["en_US"]

    rafraichis = 0
    for fichier in FICHIERS:
        chemin = get_module_resource("project_knowledge_matrix", "data", fichier)
        if not chemin or not os.path.exists(chemin):
            continue
        try:
            arbre = ET.parse(chemin)
        except ET.ParseError:
            _logger.exception("Lecture impossible de %s", fichier)
            continue

        for record in arbre.findall(".//record[@model='mail.template']"):
            rec_id = record.get("id")
            if not rec_id:
                continue
            gabarit = env.ref(
                "project_knowledge_matrix.%s" % rec_id, raise_if_not_found=False
            )
            if not gabarit:
                continue
            corps = _champ(record, "body_html")
            if not corps:
                continue
            for langue in langues:
                gabarit.with_context(lang=langue).write({"body_html": corps})
            rafraichis += 1

    _logger.info(
        "project_knowledge_matrix 18.0.13.1.0 : %s gabarit(s) rafraîchi(s) sur "
        "%s langue(s) — la marque passe par _pkm_brand().",
        rafraichis, len(langues),
    )
