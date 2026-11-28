"""L'étape d'accueil « Configurer la banque d'heures » ne promet plus ce qui n'existe pas.

Elle annonçait « les règles de rollover » : le module n'a aucune règle de report. Et son
bouton ouvrait les réglages généraux d'Odoo, où la banque d'heures n'a rien : paliers,
facturation et rapports se règlent sur chaque banque. L'étape ouvre maintenant la liste
des banques, comme la seconde.

L'enregistrement est `noupdate` : la montée ne relit pas le fichier. On réécrit SEULEMENT
si l'étape porte encore le texte livré (anglais de la 18.0.1.17.0, français d'avant), et
tout ou rien : une étape retouchée par le locataire reste la sienne. L'anglais d'abord,
le français ensuite (écrire la langue source remet le jsonb à la seule clé en_US).
"""
import json
import logging

_logger = logging.getLogger(__name__)

ANCIEN = {
    "description": ("Set the rollover rules, the alert thresholds and the periodic reports.",
                    "Définissez les règles de rollover, les seuils d'alerte et les rapports périodiques."),
    "button_text": ("Open Settings", "Ouvrir Paramètres"),
}
NOUVEAU = {
    "description": ("[action:bf_hour_bank.action_hour_bank_client] On each bank, set the alert "
                    "thresholds, the billing mode and the periodic reports.",
                    "[action:bf_hour_bank.action_hour_bank_client] Sur chaque banque, définissez "
                    "les paliers d'alerte, le mode de facturation et les rapports périodiques."),
    "button_text": ("Open the banks", "Ouvrir les banques"),
}


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT s.id, s.description, s.button_text, s.panel_step_open_action_name
          FROM onboarding_onboarding_step s
          JOIN ir_model_data d ON d.res_id = s.id AND d.model = 'onboarding.onboarding.step'
         WHERE d.module = 'bf_hour_bank' AND d.name = 'bf_onb_step_settings'
    """)
    ligne = cr.fetchone()
    if not ligne:
        return
    step_id, description, bouton, action = ligne
    valeurs = {"description": description or {}, "button_text": bouton or {}}
    # Livré = l'anglais de la 1.17.0 ou, sur une base passée d'un coup depuis l'ancienne
    # source, le français dans la fente en_US ; le fr_CA absent, ou égal au français livré.
    # Une base montée d'un coup depuis l'ancienne source (la démo) a DÉJÀ le texte neuf :
    # la migration de la 1.17.0 lit l'anglais dans le fichier. Seul le bouton reste à corriger.
    deja_neuf = all(valeurs[c].get("en_US") == NOUVEAU[c][0] for c in NOUVEAU)
    for champ, (en, fr) in ANCIEN.items():
        if deja_neuf:
            break
        v = valeurs[champ]
        if v.get("en_US") not in (en, fr) or v.get("fr_CA") not in (None, fr):
            _logger.warning("bf_hour_bank : étape d'accueil « Configurer » retouchée (%s), "
                            "laissée telle quelle", champ)
            return
    cr.execute("SELECT code FROM res_lang WHERE active")
    actives = {code for (code,) in cr.fetchall()}
    for champ, (en, fr) in NOUVEAU.items():
        if deja_neuf:
            break
        neuf = dict(valeurs[champ])
        neuf["en_US"] = en
        if "fr_CA" in actives:
            neuf["fr_CA"] = fr
        # Une autre langue qui recopiait l'ancien texte retombe sur l'anglais.
        for code, valeur in list(neuf.items()):
            if code not in ("en_US", "fr_CA") and valeur in ANCIEN[champ]:
                neuf.pop(code)
        cr.execute(f"UPDATE onboarding_onboarding_step SET {champ} = %s::jsonb WHERE id = %s",
                   (json.dumps(neuf), step_id))
    if action == "bf_open_res_config_settings":
        cr.execute("UPDATE onboarding_onboarding_step SET panel_step_open_action_name = %s "
                   "WHERE id = %s", ("bf_open_action", step_id))
    _logger.info("bf_hour_bank : étape d'accueil « Configurer » corrigée (ouvre les banques)")
