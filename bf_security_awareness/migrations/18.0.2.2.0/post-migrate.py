"""18.0.2.2.0, étape `post` : protéger les retouches AVANT le catalogue.

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

MODULE = "bf_security_awareness"
# (fichier, identifiant, champ) -> français livré avant 18.0.2.2.0
LIVRES_FR = {('data/bf_security_awareness_cron.xml', 'cron_campaign_scheduler', 'name'): 'Sensibilisation : '
                                                                             'lancer les campagnes '
                                                                             'planifiées',
 ('data/bf_security_awareness_cron.xml', 'cron_process_clawback', 'name'): 'Sensibilisation : '
                                                                           'reprise des retraits '
                                                                           'de courriel',
 ('data/bf_security_awareness_cron.xml', 'cron_recompute_profiles', 'name'): 'Sensibilisation : '
                                                                             'recalcul des scores '
                                                                             'de risque',
 ('data/bf_security_awareness_cron.xml', 'cron_training_reminders', 'name'): 'Sensibilisation : '
                                                                             'rappels de formation',
 ('data/bf_security_awareness_data.xml', 'training_category_cyber', 'name'): 'Sensibilisation à la '
                                                                             'cybersécurité',
 ('data/mail_template_data.xml', 'mail_template_training_invite', 'body_html'): '<div '
                                                                                'style="margin: 0; '
                                                                                'padding: 0;">\n'
                                                                                '                    '
                                                                                '<p>Bonjour <t '
                                                                                't-out="object.partner_id.name '
                                                                                'or '
                                                                                '\'\'">Prénom</t>,</p>\n'
                                                                                '                    '
                                                                                '<p>Une courte '
                                                                                'formation de '
                                                                                'sensibilisation à '
                                                                                'la cybersécurité '
                                                                                'vous a été\n'
                                                                                '                        '
                                                                                'assignée : '
                                                                                '<strong '
                                                                                't-out="object.channel_id.name '
                                                                                'or '
                                                                                '\'\'">Reconnaître '
                                                                                "l'hameçonnage</strong>.</p>\n"
                                                                                '                    '
                                                                                '<p '
                                                                                't-if="object.due_date">Merci '
                                                                                'de la compléter '
                                                                                'avant le\n'
                                                                                '                        '
                                                                                '<strong '
                                                                                't-out="format_date(object.due_date, '
                                                                                "date_format='d "
                                                                                "MMMM yyyy', "
                                                                                'lang_code=object.partner_id.lang)">16 '
                                                                                'septembre '
                                                                                '2026</strong>.</p>\n'
                                                                                '                    '
                                                                                '<p '
                                                                                't-if="object.training_url" '
                                                                                'style="margin: '
                                                                                '24px 0;">\n'
                                                                                '                        '
                                                                                '<a '
                                                                                't-att-href="object.training_url" '
                                                                                't-attf-style="background-color: '
                                                                                '{{ '
                                                                                'object.reminder_button_color '
                                                                                '}}; padding: 10px '
                                                                                '18px; '
                                                                                'text-decoration: '
                                                                                'none; color: '
                                                                                '#fff; '
                                                                                'border-radius: '
                                                                                '5px; font-weight: '
                                                                                'bold;">\n'
                                                                                '                            '
                                                                                'Accéder à la '
                                                                                'formation\n'
                                                                                '                        '
                                                                                '</a>\n'
                                                                                '                    '
                                                                                '</p>\n'
                                                                                '                    '
                                                                                '<p>Merci,<t '
                                                                                't-if="object.reminder_signature_name"><br><t '
                                                                                't-out="object.reminder_signature_name">La '
                                                                                'société</t></t></p>\n'
                                                                                '                '
                                                                                '</div>',
 ('data/mail_template_data.xml', 'mail_template_training_invite', 'name'): 'Sensibilisation : '
                                                                           'invitation à la '
                                                                           'formation',
 ('data/mail_template_data.xml', 'mail_template_training_invite', 'subject'): 'Formation en '
                                                                              'cybersécurité à '
                                                                              'compléter'}
# Langues dont la clé, si l'en_US lui est égal, est la sortie d'un ancien crochet (pas une retouche).
CROCHET = []


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


def _contenu(noeud):
    """Le contenu d'un <field> comme Odoo le charge : un champ `type="html"` est la sérialisation
    HTML de ses enfants, sinon le texte."""
    if noeud is None:
        return ""
    if noeud.get("type") == "html":
        return "".join(etree.tostring(n, method="html", encoding="unicode") for n in noeud)
    return noeud.text or ""


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
        anglais = _contenu(noeud).strip()
        porteur = _porteur(rec)
        porteur.flush_recordset([champ])  # la base, pas un tampon de l'ORM
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        brut = (cr.fetchone() or [None])[0]
        valeurs = brut if isinstance(brut, dict) else {"en_US": brut}
        retouche = (valeurs.get("en_US") or "").strip()
        if not retouche or retouche in (francais, anglais, _forme(rec, champ, francais),
                                        _forme(rec, champ, anglais)):
            continue
        if any((valeurs.get(l) or "").strip() == retouche for l in CROCHET):
            continue  # sortie d'un ancien crochet, pas une retouche
        manquantes = [lang for lang in langues if lang not in valeurs]
        if manquantes:
            _logger.warning("%s : %s.%s retouché, recopié en %s", MODULE, xmlid, champ, manquantes)
            if callable(porteur._fields[champ].translate):
                # Champ traduit terme à terme : update_field_translations attend des termes, pas une
                # valeur entière (TypeError) ; on recopie la retouche telle quelle par SQL.
                for lang in manquantes:
                    cr.execute(SQL("UPDATE %s SET %s = %s || jsonb_build_object(%s, %s->>'en_US') WHERE id = %s",
                                   SQL.identifier(porteur._table), SQL.identifier(champ), SQL.identifier(champ),
                                   lang, SQL.identifier(champ), porteur.id))
                porteur.invalidate_recordset([champ])
            else:
                porteur.update_field_translations(champ, {lang: valeurs["en_US"] for lang in manquantes})
