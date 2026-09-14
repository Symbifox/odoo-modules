"""18.0.1.8.0 : les libellés passent d'une source française à une source anglaise.

Odoo ne traduit jamais vers en_US, la langue source : tant que la source était
française, un usager réglé en anglais recevait la notification en français.

Le catalogue du module est chargé en écrasant. Le panneau de prise en main est
une donnée `noupdate` : sa valeur en_US reste française après la mise à jour.
On la bascule seulement si elle porte encore le texte livré.

⚠️ En `end` : Odoo charge les traductions du module juste après le `post`.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_task_unblock_notify"
DONNEES = "data/bf_onboarding.xml"
LIVRES_FR = {
    ("bf_onb_step_settings", "title"): "Notifications de déblocage",
    ("bf_onb_step_settings", "description"):
        "Choisissez qui est notifié quand une tâche bloquante est débloquée et le format des messages.",
    ("bf_onb_step_settings", "button_text"): "Ouvrir Paramètres",
    ("bf_onb_step_settings", "done_text"): "Notifications actives",
    ("bf_onboarding_panel", "name"): "Notifications déblocage",
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
    _basculer_donnees_noupdate(env)


def _basculer_donnees_noupdate(env):
    """Passe en anglais la valeur en_US des données `noupdate` encore livrées.

    Le texte anglais est lu dans le fichier de données du module; une valeur
    retouchée à la main (qui ne vaut plus le français livré) est laissée.
    """
    with file_open(f"{MODULE}/{DONNEES}", "rb") as f:
        arbre = etree.parse(f)
    for (xmlid, champ), francais in LIVRES_FR.items():
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        noeud = arbre.find(f".//record[@id=\x27{xmlid}\x27]/field[@name=\x27{champ}\x27]")
        if not rec or noeud is None or not noeud.text:
            continue
        rec_en = rec.with_context(lang="en_US")
        if (rec_en[champ] or "").strip() != francais:
            _logger.info("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        rec_en.write({champ: noeud.text.strip()})

