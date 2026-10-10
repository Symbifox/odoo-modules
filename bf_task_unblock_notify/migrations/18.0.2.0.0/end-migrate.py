"""18.0.2.0.0 : le panneau de prise en main mène aux vrais réglages du module.

Il promettait « qui est notifié et le format des messages » et ouvrait la page
générale des Paramètres, où le module n'avait rien. Les réglages existent
maintenant (Projet) : texte et bouton suivent. Données `noupdate` : on ne
touche qu'aux valeurs encore livrées, une valeur retouchée à la main reste.

🔴 Le chargeur de traductions GARDE les traductions existantes d'un
enregistrement `noupdate`, même en écrasant (`TranslationImporter.save` :
seul `force_overwrite` passe outre). Basculer l'anglais ne suffit donc pas :
le français livré resterait. On retire les autres langues du champ basculé, et
le catalogue les remplit.

⚠️ En `end` : Odoo charge les traductions du module juste après le `post`.
"""

import json
import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_task_unblock_notify"
DONNEES = "data/bf_onboarding.xml"
LIVRES = {
    ("bf_onb_step_settings", "description"):
        "Choose who is notified when a blocking task is cleared, and the format of the messages.",
    ("bf_onb_step_settings", "panel_step_open_action_name"): "bf_open_res_config_settings",
}
#: Ce que 18.0.1.8.0 a livré dans les autres langues : seules ces valeurs-là sont
#: retirées pour que le catalogue les remplisse ; une traduction retouchée reste.
LIVRES_TRADUITS = {
    ("bf_onb_step_settings", "description"): {
        "Choisissez qui est notifié quand une tâche bloquante est débloquée et le format des messages.",
        "Choose who is notified when a blocking task is cleared, and the format of the messages.",
    },
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    with file_open(f"{MODULE}/{DONNEES}", "rb") as f:
        arbre = etree.parse(f)
    for (xmlid, champ), livre in LIVRES.items():
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        noeud = arbre.find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        if not rec or noeud is None or not noeud.text:
            continue
        rec_en = rec.with_context(lang="en_US")
        if (rec_en[champ] or "").strip() != livre:
            _logger.info("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        rec_en.write({champ: noeud.text.strip()})
        if rec._fields[champ].translate:
            env.cr.execute(SQL("SELECT %s FROM %s WHERE id = %s",
                               SQL.identifier(champ), SQL.identifier(rec._table), rec.id))
            valeurs = env.cr.fetchone()[0] or {}
            livrees = LIVRES_TRADUITS.get((xmlid, champ), set())
            gardees = {lang: v for lang, v in valeurs.items()
                       if lang == "en_US" or (v or "").strip() not in livrees}
            env.cr.execute(SQL("UPDATE %s SET %s = %s::jsonb WHERE id = %s",
                               SQL.identifier(rec._table), SQL.identifier(champ),
                               json.dumps(gardees), rec.id))
            rec.invalidate_recordset([champ])
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
