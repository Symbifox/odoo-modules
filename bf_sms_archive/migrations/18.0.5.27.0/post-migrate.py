"""18.0.5.27.0, étape `post` : protéger les retouches AVANT le catalogue.

Odoo charge le catalogue du module juste APRÈS `post`, avant `end` : une valeur
`noupdate` retouchée à la main qui n'a pas de clé dans une langue installée (retouche
faite dans une interface anglaise, ou colonne qui devient traduisible) y recevrait le
français LIVRÉ, qui la masquerait. On y recopie la retouche ici; le catalogue ne
remplace jamais une clé existante d'un enregistrement `noupdate`.

L'anglais livré n'est pas une retouche : un enregistrement supprimé chez un locataire
est recréé par la montée depuis le fichier de données, déjà en anglais.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, file_open

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
    arbres = {}
    for (fichier, xmlid, champ), francais in LIVRES_FR.items():
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        if not rec:
            continue
        if fichier not in arbres:
            with file_open(f"{MODULE}/{fichier}", "rb") as f:
                arbres[fichier] = etree.parse(f)
        noeud = arbres[fichier].find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        anglais = (noeud.text or "").strip() if noeud is not None else ""
        porteur = _porteur(rec)
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        brut = (cr.fetchone() or [None])[0]
        valeurs = brut if isinstance(brut, dict) else {"en_US": brut}
        retouche = (valeurs.get("en_US") or "").strip()
        if not retouche or retouche in (francais, anglais, _forme(rec, champ, francais),
                                        _forme(rec, champ, anglais)):
            continue
        manquantes = [lang for lang in langues if lang not in valeurs]
        if manquantes:
            _logger.warning("%s : %s.%s retouché, recopié en %s", MODULE, xmlid, champ, manquantes)
            porteur.update_field_translations(champ, {lang: valeurs["en_US"] for lang in manquantes})
