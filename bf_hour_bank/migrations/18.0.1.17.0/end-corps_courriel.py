"""Corps du gabarit « Hour bank: report » : texte en anglais, balisage du locataire gardé.

Le gabarit est `noupdate` EN BASE (posé à la création, quoi que dise le fichier) et son
corps y diffère du fichier par l'enveloppe : la migration 18.0.1.15.3 y a écrit
les couleurs, le courriel et le téléphone de chaque maison, et la recharger de force les
effacerait. On ne remplace donc que des SEGMENTS DE TEXTE, jamais le balisage, et
seulement si TOUS les segments français livrés y sont, chacun le nombre de fois attendu
(tout ou rien) : un corps retouché à la main reste tel quel.

Ordre des écritures : en_US d'abord, puis fr_CA (écrire la langue source remet le jsonb à
la seule clé en_US). Le français du lecteur francophone est l'ancien en_US, mot pour mot,
sauf si la base portait déjà un fr_CA DIFFÉRENT : c'est une retouche, on la garde.
"""
import json
import logging
import re

_logger = logging.getLogger(__name__)

ENTITES = {
    "é": "(?:é|&#233;|&eacute;)", "è": "(?:è|&#232;|&egrave;)",
    "à": "(?:à|&#224;|&agrave;)", "É": "(?:É|&#201;|&Eacute;)",
}

# (français livré, anglais, occurrences attendues). L'ordre compte : la signature
# « Banque d'heures par » passe avant le titre « Banque d'heures ».
SEGMENTS = [
    ("Banque d'heures par", "Hour bank by", 1),
    ("Banque d'heures", "Hour bank", 1),
    ("Bonjour,", "Hello,", 1),
    ("Veuillez trouver ci-joint l'état des banques d'heures pour",
     "Please find attached the hour bank statement for", 1),
    ("Les fichiers PDF et Excel sont en pièces jointes.", "The PDF and Excel files are attached.", 1),
    ("Cordialement,", "Kind regards,", 1),
    ("Pour toute question, contactez-nous à", "If you have any questions, contact us at", 1),
    ("ou appelez le", "or call", 1),
    ("Suivi automatique des heures consommées vs. provisionnées.",
     "Automatic tracking of hours consumed vs. hours allocated.", 1),
    ("Politique de confidentialité", "Privacy policy", 1),
    (">Conditions<", ">Terms<", 1),
]


def _motif(francais):
    morceaux = []
    for car in francais:
        if car in ENTITES:
            morceaux.append(ENTITES[car])
        elif car == " ":
            morceaux.append(r"(?:\s|&nbsp;|&#160;|\xa0)+")
        elif car == "'":
            morceaux.append("(?:'|&#39;|&#x27;|’)")
        else:
            morceaux.append(re.escape(car))
    return re.compile("".join(morceaux))


def basculer(corps):
    """Le corps avec ses segments en anglais, ou None si un segment manque."""
    for francais, anglais, attendu in SEGMENTS:
        motif = _motif(francais)
        if len(motif.findall(corps)) != attendu:
            return None
        corps = motif.sub(lambda _m, a=anglais: a, corps)
    return corps


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT t.id, t.body_html
          FROM mail_template t
          JOIN ir_model_data d ON d.res_id = t.id AND d.model = 'mail.template'
         WHERE d.module = 'bf_hour_bank' AND d.name = 'email_template_hour_bank_report'
    """)
    ligne = cr.fetchone()
    if not ligne or not ligne[1]:
        return
    gabarit_id, corps = ligne
    ancien = corps.get("en_US") or ""
    anglais = basculer(ancien)
    if anglais is None:
        _logger.warning("bf_hour_bank : corps du gabarit du rapport retouché ou déjà "
                        "basculé, laissé tel quel")
        return
    cr.execute("SELECT code FROM res_lang WHERE active")
    actives = {code for (code,) in cr.fetchall()}
    neuf = dict(corps)
    neuf["en_US"] = anglais
    if "fr_CA" in actives and (not corps.get("fr_CA") or corps.get("fr_CA") == ancien):
        neuf["fr_CA"] = ancien
    # Les autres langues qui recopiaient le français (reste de l'ancienne source) retombent
    # sur l'anglais.
    for code, valeur in corps.items():
        if code not in ("en_US", "fr_CA") and not code.startswith("fr") and valeur == ancien:
            neuf.pop(code)
    cr.execute("UPDATE mail_template SET body_html = %s::jsonb WHERE id = %s",
               (json.dumps(neuf), gabarit_id))
    _logger.info("bf_hour_bank : corps du gabarit du rapport basculé en anglais "
                 "(langues : %s)", ", ".join(sorted(neuf)))
