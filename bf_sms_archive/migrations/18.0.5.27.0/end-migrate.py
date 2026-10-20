"""18.0.5.27.0 : les libellés passent d'une source française à une source anglaise.

Odoo ne traduit jamais vers en_US, la langue source : tant que la source était
française, un usager réglé en anglais lisait ce module en français.

🔴 Une mise à jour n'écrase jamais une traduction existante : le catalogue du
module est rechargé en écrasant (le catalogue d'avant ne traduisait rien, ou
recopiait le français).

Les données `noupdate` gardent leur valeur en_US française après la mise à jour.
On bascule chaque champ SEULEMENT s'il porte encore le texte livré; l'anglais est
lu dans le fichier de données du module. Une valeur retouchée est laissée.

🔴 Une retouche qui n'a pas de clé dans une langue installée (faite dans une
interface anglaise, ou colonne qui devient traduisible) recevrait du catalogue le
français LIVRÉ, qui la masquerait : `post-migrate.py` l'y recopie AVANT le catalogue.

⚠️ En `end` : Odoo charge les traductions du module juste après le `post`.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_sms_archive"
# (fichier, identifiant, champ) -> français livré avant 18.0.5.27.0
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_devices', 'button_text'): 'Voir les appareils',
 ('data/bf_onboarding.xml', 'bf_onb_step_devices', 'description'): '[action:bf_sms_archive.sms_device_action] '
                                                                   'Déclarez les téléphones et '
                                                                   'numéros dont vous voulez '
                                                                   'archiver les SMS et appels.',
 ('data/bf_onboarding.xml', 'bf_onb_step_devices', 'done_text'): 'Appareils enregistrés',
 ('data/bf_onboarding.xml', 'bf_onb_step_devices', 'title'): 'Enregistrer les appareils suivis',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'button_text'): 'Ouvrir Paramètres',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'description'): 'Renseignez les chemins NC à '
                                                                    'surveiller pour les '
                                                                    'sauvegardes SMS/appels et les '
                                                                    'options de purge.',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'done_text'): 'Paramètres prêts',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'title'): 'Configurer SMS Archive',
 ('data/bf_onboarding.xml', 'bf_onb_step_threads', 'button_text'): 'Voir les fils',
 ('data/bf_onboarding.xml', 'bf_onb_step_threads', 'description'): '[action:bf_sms_archive.sms_thread_action] '
                                                                   'Validez que les conversations '
                                                                   'apparaissent et sont reliées '
                                                                   'aux contacts Odoo.',
 ('data/bf_onboarding.xml', 'bf_onb_step_threads', 'done_text'): 'Archivage validé',
 ('data/bf_onboarding.xml', 'bf_onb_step_threads', 'title'): 'Inspecter les fils SMS',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'SMS Archive',
 ('data/sms_nc_watch_cron.xml', 'ir_cron_sms_disk_import', 'name'): 'SMS & Calls : import depuis '
                                                                    '/mnt/sms-inbox',
 ('data/sms_nc_watch_cron.xml', 'ir_cron_sms_nc_watch', 'name'): 'SMS & Calls : surveillance '
                                                                 'dossier Nextcloud'}


def _porteur(rec):
    """L'enregistrement qui stocke la valeur : un travail planifié, son action."""
    return rec.ir_actions_server_id if rec._name == "ir.cron" else rec


def _forme(rec, champ, valeur):
    """La valeur telle qu'Odoo la range. 🔴 Un champ HTML est ASSAINI à l'écriture : le corps
    d'un gabarit de courriel en base n'est jamais égal au texte brut du fichier de données, et la
    comparaison brute le prenait pour une retouche (corps laissés
    en français en en_US, puis recopiés dans les autres langues)."""
    try:
        return str(rec._fields[champ].convert_to_column(valeur, rec) or "").strip()
    except Exception:  # noqa: BLE001
        return valeur


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
    arbres = {}
    for (fichier, xmlid, champ), francais in LIVRES_FR.items():
        if fichier not in arbres:
            with file_open(f"{MODULE}/{fichier}", "rb") as f:
                arbres[fichier] = etree.parse(f)
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        noeud = arbres[fichier].find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        if not rec or noeud is None or not (noeud.text or "").strip():
            continue
        rec_en = rec.with_context(lang="en_US")
        if (rec_en[champ] or "").strip() not in (francais, _forme(rec_en, champ, francais)):
            # Ses langues manquantes ont reçu la retouche à l'étape `post`.
            _logger.warning("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        # ⚠️ Un travail planifié s'écrit par son action : ir.cron.write prend un verrou
        # NOWAIT qui ferait ÉCHOUER la montée si le travail tourne à ce moment (le recalcul
        # de `cron_name` réécrit quand même sa ligne : la montée ATTEND le travail).
        _porteur(rec_en).write({champ: noeud.text.strip()})
