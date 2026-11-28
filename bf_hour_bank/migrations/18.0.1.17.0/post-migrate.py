"""18.0.1.17.0, étape `post` : protéger les retouches AVANT le catalogue.

Odoo charge le catalogue du module juste APRÈS `post`, avant `end` : une valeur
`noupdate` retouchée à la main qui n'a pas de clé dans une langue installée (retouche
faite dans une interface anglaise, ou colonne qui devient traduisible) y recevrait le
français LIVRÉ, qui la masquerait. On y recopie la retouche ici; le catalogue ne
remplace jamais une clé existante d'un enregistrement `noupdate`.

L'anglais livré n'est pas une retouche : un enregistrement supprimé chez un locataire
est recréé par la montée depuis le fichier de données, déjà en anglais.
"""

import logging
import re

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_hour_bank"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.17.0
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_clients', 'button_text'): 'Voir les clients',
 ('data/bf_onboarding.xml', 'bf_onb_step_clients', 'description'): '[action:bf_hour_bank.action_hour_bank_client] '
                                                                   'Associez chaque client à une '
                                                                   "banque d'heures avec son solde "
                                                                   'initial.',
 ('data/bf_onboarding.xml', 'bf_onb_step_clients', 'done_text'): 'Clients liés',
 ('data/bf_onboarding.xml', 'bf_onb_step_clients', 'title'): 'Lier les clients à des banques',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'button_text'): 'Ouvrir Paramètres',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'description'): 'Définissez les règles de '
                                                                    "rollover, les seuils d'alerte "
                                                                    'et les rapports périodiques.',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'done_text'): "Banque d'heures configurée",
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'title'): "Configurer la banque d'heures",
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): "Banque d'heures",
 ('data/hour_bank_cron.xml', 'cron_hour_bank_check_thresholds', 'name'): "Banque d'heures : "
                                                                         'Vérifier les paliers de '
                                                                         'notification',
 ('data/hour_bank_cron.xml', 'cron_hour_bank_send_reports', 'name'): "Banque d'heures : Envoi "
                                                                     'automatique des rapports',
 ('data/hour_bank_mail_template.xml', 'email_template_hour_bank_report', 'body_html'): '<t '
                                                                                       't-set="company" '
                                                                                       't-value="(object.company_id '
                                                                                       'if '
                                                                                       'hasattr(object, '
                                                                                       "'company_id') "
                                                                                       'and '
                                                                                       'object.company_id '
                                                                                       'else '
                                                                                       'user.company_id)"></t>\n'
                                                                                       '<t '
                                                                                       't-set="brand_primary" '
                                                                                       't-value="(company '
                                                                                       'and '
                                                                                       'company.report_brand_primary) '
                                                                                       'or '
                                                                                       '\'#714B67\'"></t>\n'
                                                                                       '<t '
                                                                                       't-set="brand_dark" '
                                                                                       't-value="(company '
                                                                                       'and '
                                                                                       'company.report_brand_dark) '
                                                                                       'or '
                                                                                       '\'#212529\'"></t>\n'
                                                                                       '<table '
                                                                                       'role="presentation" '
                                                                                       'cellpadding="0" '
                                                                                       'cellspacing="0" '
                                                                                       'border="0" '
                                                                                       'width="100%" '
                                                                                       'style="background-color:#F8FAFC;">\n'
                                                                                       '    '
                                                                                       '<tbody>\n'
                                                                                       '        '
                                                                                       '<tr>\n'
                                                                                       '            '
                                                                                       '<td '
                                                                                       'align="center" '
                                                                                       'style="padding:24px;">\n'
                                                                                       '                '
                                                                                       '<table '
                                                                                       'cellspacing="0" '
                                                                                       'cellpadding="0" '
                                                                                       'border="0" '
                                                                                       'width="100%" '
                                                                                       'align="center" '
                                                                                       'role="presentation">\n'
                                                                                       '                    '
                                                                                       '<tbody>\n'
                                                                                       '                        '
                                                                                       '<tr>\n'
                                                                                       '                            '
                                                                                       '<td>\n'
                                                                                       '                                '
                                                                                       '<br>\n'
                                                                                       '                                '
                                                                                       '<table '
                                                                                       'role="presentation" '
                                                                                       'cellpadding="0" '
                                                                                       'cellspacing="0" '
                                                                                       'border="0" '
                                                                                       'width="600" '
                                                                                       'style="width:600px; '
                                                                                       'max-width:600px; '
                                                                                       'margin:0 '
                                                                                       'auto; '
                                                                                       'background-color:#ffffff; '
                                                                                       'border-radius:12px; '
                                                                                       'border:1px '
                                                                                       'solid '
                                                                                       '#e5e7eb; '
                                                                                       'border-collapse:collapse;">\n'
                                                                                       '                                    '
                                                                                       '<tbody>\n'
                                                                                       '                                        '
                                                                                       '<!-- '
                                                                                       'En-t&#234;te '
                                                                                       '-->\n'
                                                                                       '                                        '
                                                                                       '<tr>\n'
                                                                                       '                                            '
                                                                                       '<td '
                                                                                       't-attf-style="background-color:{{ '
                                                                                       'brand_dark '
                                                                                       '}}; '
                                                                                       'padding:16px '
                                                                                       '24px; '
                                                                                       'border-radius:12px '
                                                                                       '12px 0 '
                                                                                       '0;">\n'
                                                                                       '                                                '
                                                                                       '<table '
                                                                                       'role="presentation" '
                                                                                       'width="100%" '
                                                                                       'cellpadding="0" '
                                                                                       'cellspacing="0" '
                                                                                       'border="0">\n'
                                                                                       '                                                    '
                                                                                       '<tbody>\n'
                                                                                       '                                                        '
                                                                                       '<tr>\n'
                                                                                       '                                                            '
                                                                                       '<td '
                                                                                       'align="left" '
                                                                                       'style="color:#FFFFFF; '
                                                                                       "font-family:'Lexend','Segoe "
                                                                                       "UI',Arial,sans-serif; "
                                                                                       'font-size:16px; '
                                                                                       'font-weight:600;">\n'
                                                                                       '                                                                '
                                                                                       '<a '
                                                                                       't-attf-href="{{ '
                                                                                       'company.website '
                                                                                       'or '
                                                                                       'object.get_base_url() '
                                                                                       '}}" '
                                                                                       'style="text-decoration:none;">\n'
                                                                                       '                                                                    '
                                                                                       '<img '
                                                                                       't-attf-src="/brand/logo/{{ '
                                                                                       'company.id '
                                                                                       '}}" '
                                                                                       't-att-alt="company.name '
                                                                                       'or '
                                                                                       '\'Logo\'" '
                                                                                       'style="height:48px; '
                                                                                       'width:auto; '
                                                                                       'display:block; '
                                                                                       'border:0;">\n'
                                                                                       '                                                                '
                                                                                       '</a>\n'
                                                                                       '                                                            '
                                                                                       '</td>\n'
                                                                                       '                                                            '
                                                                                       '<td '
                                                                                       'align="right" '
                                                                                       'style="color:#E6EDF3; '
                                                                                       "font-family:'Lexend','Segoe "
                                                                                       "UI',Arial,sans-serif; "
                                                                                       'font-size:22px; '
                                                                                       'font-weight:800; '
                                                                                       'letter-spacing:0.2px;">\n'
                                                                                       '                                                                '
                                                                                       'Banque '
                                                                                       "d'heures\n"
                                                                                       '                                                            '
                                                                                       '</td>\n'
                                                                                       '                                                        '
                                                                                       '</tr>\n'
                                                                                       '                                                    '
                                                                                       '</tbody>\n'
                                                                                       '                                                '
                                                                                       '</table>\n'
                                                                                       '                                            '
                                                                                       '</td>\n'
                                                                                       '                                        '
                                                                                       '</tr>\n'
                                                                                       '\n'
                                                                                       '                                        '
                                                                                       '<!-- Barre '
                                                                                       "d'accentuation "
                                                                                       '-->\n'
                                                                                       '                                        '
                                                                                       '<tr>\n'
                                                                                       '                                            '
                                                                                       '<td '
                                                                                       't-attf-style="height:4px; '
                                                                                       'line-height:4px; '
                                                                                       'background-color:{{ '
                                                                                       'brand_primary '
                                                                                       '}};">&amp;nbsp;</td>\n'
                                                                                       '                                        '
                                                                                       '</tr>\n'
                                                                                       '\n'
                                                                                       '                                        '
                                                                                       '<!-- '
                                                                                       'Contenu '
                                                                                       '-->\n'
                                                                                       '                                        '
                                                                                       '<tr>\n'
                                                                                       '                                            '
                                                                                       '<td '
                                                                                       'style="padding:24px; '
                                                                                       "font-family:'Lexend','Segoe "
                                                                                       'UI\',Arial,sans-serif;">\n'
                                                                                       '                                                '
                                                                                       '<p '
                                                                                       'style="font-size:16px; '
                                                                                       'line-height:26px; '
                                                                                       'color:#374151; '
                                                                                       'margin:0 0 '
                                                                                       '16px 0;">\n'
                                                                                       '                                                    '
                                                                                       'Bonjour,\n'
                                                                                       '                                                '
                                                                                       '</p>\n'
                                                                                       '                                                '
                                                                                       '<p '
                                                                                       'style="font-size:16px; '
                                                                                       'line-height:26px; '
                                                                                       'color:#374151; '
                                                                                       'margin:0 0 '
                                                                                       '20px 0;">\n'
                                                                                       '                                                    '
                                                                                       'Veuillez '
                                                                                       'trouver '
                                                                                       'ci-joint '
                                                                                       "l'état des "
                                                                                       'banques '
                                                                                       "d'heures "
                                                                                       'pour\n'
                                                                                       '                                                    '
                                                                                       '<strong><t '
                                                                                       't-out="object.partner_id.name"></t></strong>.\n'
                                                                                       '                                                '
                                                                                       '</p>\n'
                                                                                       '                                                '
                                                                                       '<p '
                                                                                       'style="font-size:16px; '
                                                                                       'line-height:26px; '
                                                                                       'color:#374151; '
                                                                                       'margin:0 0 '
                                                                                       '20px 0;">\n'
                                                                                       '                                                    '
                                                                                       'Les '
                                                                                       'fichiers '
                                                                                       'PDF et '
                                                                                       'Excel sont '
                                                                                       'en pièces '
                                                                                       'jointes.\n'
                                                                                       '                                                '
                                                                                       '</p>\n'
                                                                                       '                                                '
                                                                                       '<p '
                                                                                       'style="font-size:16px; '
                                                                                       'line-height:26px; '
                                                                                       'color:#374151; '
                                                                                       'margin:0;">\n'
                                                                                       '                                                    '
                                                                                       'Cordialement,<br>\n'
                                                                                       '                                                    '
                                                                                       '<t '
                                                                                       't-out="user.name"></t>\n'
                                                                                       '                                                '
                                                                                       '</p>\n'
                                                                                       '                                                '
                                                                                       '<p '
                                                                                       'style="font-size:13px; '
                                                                                       'line-height:20px; '
                                                                                       'color:#6B7280; '
                                                                                       'margin:16px '
                                                                                       '0 0 0;">\n'
                                                                                       '                                                    '
                                                                                       'Pour toute '
                                                                                       'question, '
                                                                                       'contactez-nous '
                                                                                       'à\n'
                                                                                       '                                                    '
                                                                                       '<a '
                                                                                       't-attf-href="mailto:{{ '
                                                                                       'company.email '
                                                                                       'or '
                                                                                       "'service@example.com' "
                                                                                       '}}" '
                                                                                       't-attf-style="color:{{ '
                                                                                       'brand_primary '
                                                                                       '}}; '
                                                                                       'text-decoration:none;"><t '
                                                                                       't-out="company.email '
                                                                                       'or '
                                                                                       '\'service@example.com\'"></t></a>\n'
                                                                                       '                                                    '
                                                                                       'ou appelez '
                                                                                       'le\n'
                                                                                       '                                                    '
                                                                                       '<a '
                                                                                       't-attf-href="tel:{{ '
                                                                                       'company.phone '
                                                                                       'or '
                                                                                       "'+15555555555' "
                                                                                       '}}" '
                                                                                       't-attf-style="color:{{ '
                                                                                       'brand_primary '
                                                                                       '}}; '
                                                                                       'text-decoration:none;"><t '
                                                                                       't-out="company.phone '
                                                                                       'or '
                                                                                       '\'555-555-5555\'"></t></a>.\n'
                                                                                       '                                                '
                                                                                       '</p>\n'
                                                                                       '                                            '
                                                                                       '</td>\n'
                                                                                       '                                        '
                                                                                       '</tr>\n'
                                                                                       '\n'
                                                                                       '                                        '
                                                                                       '<!-- '
                                                                                       'S&#233;parateur '
                                                                                       '-->\n'
                                                                                       '                                        '
                                                                                       '<tr>\n'
                                                                                       '                                            '
                                                                                       '<td '
                                                                                       'style="height:1px; '
                                                                                       'line-height:1px; '
                                                                                       'background-color:#E5E7EB;">&amp;nbsp;</td>\n'
                                                                                       '                                        '
                                                                                       '</tr>\n'
                                                                                       '\n'
                                                                                       '                                        '
                                                                                       '<!-- Pied '
                                                                                       'de page '
                                                                                       '-->\n'
                                                                                       '                                        '
                                                                                       '<tr>\n'
                                                                                       '                                            '
                                                                                       '<td '
                                                                                       'style="padding:16px '
                                                                                       '24px 24px '
                                                                                       '24px; '
                                                                                       'background-color:#FFFFFF; '
                                                                                       'border-radius:0 '
                                                                                       '0 12px '
                                                                                       '12px;">\n'
                                                                                       '                                                '
                                                                                       '<table '
                                                                                       'role="presentation" '
                                                                                       'width="100%" '
                                                                                       'cellpadding="0" '
                                                                                       'cellspacing="0" '
                                                                                       'border="0">\n'
                                                                                       '                                                    '
                                                                                       '<tbody>\n'
                                                                                       '                                                        '
                                                                                       '<tr>\n'
                                                                                       '                                                            '
                                                                                       '<td '
                                                                                       'style="font-family:\'Lexend\',\'Segoe '
                                                                                       "UI',Arial,sans-serif; "
                                                                                       'font-size:12px; '
                                                                                       'color:#6B7280;">\n'
                                                                                       '                                                                '
                                                                                       '<strong '
                                                                                       't-attf-style="color:{{ '
                                                                                       'brand_dark '
                                                                                       '}};">Banque '
                                                                                       "d'heures "
                                                                                       'par <t '
                                                                                       't-out="company.name"></t></strong><br>\n'
                                                                                       '                                                                '
                                                                                       'Suivi '
                                                                                       'automatique '
                                                                                       'des heures '
                                                                                       'consommées '
                                                                                       'vs. '
                                                                                       'provisionnées.\n'
                                                                                       '                                                            '
                                                                                       '</td>\n'
                                                                                       '                                                            '
                                                                                       '<td '
                                                                                       'align="right" '
                                                                                       'style="font-family:\'Lexend\',\'Segoe '
                                                                                       "UI',Arial,sans-serif; "
                                                                                       'font-size:12px; '
                                                                                       'color:#9CA3AF;">\n'
                                                                                       '                                                                '
                                                                                       '<a '
                                                                                       't-if="company '
                                                                                       'and '
                                                                                       "'brand_privacy_url' "
                                                                                       'in company '
                                                                                       'and '
                                                                                       'company.brand_privacy_url" '
                                                                                       't-attf-href="{{ '
                                                                                       'company.brand_privacy_url '
                                                                                       '}}" '
                                                                                       'style="color:#9CA3AF; '
                                                                                       'text-decoration:underline;">Politique '
                                                                                       'de '
                                                                                       'confidentialité</a>\n'
                                                                                       '                                                                '
                                                                                       '<span '
                                                                                       't-if="company '
                                                                                       'and '
                                                                                       "'brand_privacy_url' "
                                                                                       'in company '
                                                                                       'and '
                                                                                       'company.brand_privacy_url '
                                                                                       'and '
                                                                                       'company '
                                                                                       'and '
                                                                                       "'brand_terms_url' "
                                                                                       'in company '
                                                                                       'and '
                                                                                       'company.brand_terms_url" '
                                                                                       'style="color:#9CA3AF;"> '
                                                                                       '| </span>\n'
                                                                                       '                                                                '
                                                                                       '<a '
                                                                                       't-if="company '
                                                                                       'and '
                                                                                       "'brand_terms_url' "
                                                                                       'in company '
                                                                                       'and '
                                                                                       'company.brand_terms_url" '
                                                                                       't-attf-href="{{ '
                                                                                       'company.brand_terms_url '
                                                                                       '}}" '
                                                                                       'style="color:#9CA3AF; '
                                                                                       'text-decoration:underline;">Conditions</a>\n'
                                                                                       '                                                            '
                                                                                       '</td>\n'
                                                                                       '                                                        '
                                                                                       '</tr>\n'
                                                                                       '                                                    '
                                                                                       '</tbody>\n'
                                                                                       '                                                '
                                                                                       '</table>\n'
                                                                                       '                                            '
                                                                                       '</td>\n'
                                                                                       '                                        '
                                                                                       '</tr>\n'
                                                                                       '                                    '
                                                                                       '</tbody>\n'
                                                                                       '                                '
                                                                                       '</table>\n'
                                                                                       '\n'
                                                                                       '                                '
                                                                                       '<!-- Barre '
                                                                                       "d'accentuation "
                                                                                       'inf&#233;rieure '
                                                                                       '-->\n'
                                                                                       '                                '
                                                                                       '<table '
                                                                                       'role="presentation" '
                                                                                       'cellpadding="0" '
                                                                                       'cellspacing="0" '
                                                                                       'border="0" '
                                                                                       'width="600" '
                                                                                       'style="width:600px; '
                                                                                       'max-width:600px; '
                                                                                       'margin:12px '
                                                                                       'auto 0;">\n'
                                                                                       '                                    '
                                                                                       '<tbody>\n'
                                                                                       '                                        '
                                                                                       '<tr>\n'
                                                                                       '                                            '
                                                                                       '<td '
                                                                                       't-attf-style="height:3px; '
                                                                                       'line-height:3px; '
                                                                                       'background-color:{{ '
                                                                                       'brand_primary '
                                                                                       '}}; '
                                                                                       'width:50%;">&amp;nbsp;</td>\n'
                                                                                       '                                            '
                                                                                       '<td '
                                                                                       't-attf-style="height:3px; '
                                                                                       'line-height:3px; '
                                                                                       'background-color:{{ '
                                                                                       'brand_dark '
                                                                                       '}}; '
                                                                                       'width:50%;">&amp;nbsp;</td>\n'
                                                                                       '                                        '
                                                                                       '</tr>\n'
                                                                                       '                                    '
                                                                                       '</tbody>\n'
                                                                                       '                                '
                                                                                       '</table>\n'
                                                                                       '                            '
                                                                                       '</td>\n'
                                                                                       '                        '
                                                                                       '</tr>\n'
                                                                                       '                    '
                                                                                       '</tbody>\n'
                                                                                       '                '
                                                                                       '</table>\n'
                                                                                       '            '
                                                                                       '</td>\n'
                                                                                       '        '
                                                                                       '</tr>\n'
                                                                                       '    '
                                                                                       '</tbody>\n'
                                                                                       '</table>',
 ('data/hour_bank_mail_template.xml', 'email_template_hour_bank_report', 'name'): "Banque d'heures "
                                                                                  ': Rapport',
 ('data/hour_bank_mail_template.xml', 'email_template_hour_bank_report', 'subject'): 'État des '
                                                                                     'banques '
                                                                                     "d'heures — "
                                                                                     '{{ '
                                                                                     'object.partner_id.name '
                                                                                     '}}'}
# Langues dont la clé, si l'en_US lui est égal, est la sortie d'un ancien crochet (pas une retouche).
CROCHET = []
# Anciennes formes LIVRÉES connues (avant, après) : ramenées à l'actuelle avant de comparer.
ANCIENNES = []


def _canon(texte):
    """La meme chaine, serialisee canoniquement.

    🔴 Un corps de courriel EN BASE n'est pas le texte du fichier : Odoo y range
    `<t/>` la ou `tostring(method="html")` rend `<t></t>`, et l'ecriture retire les
    commentaires XML. Comparer deux textes bruts faisait donc passer un corps livre
    pour une retouche, et le corps restait francais en `en_US` apres une montee
    propre. On compare deux formes canoniques.

    L'espacement est normalise : il n'a pas de sens en HTML, et le garder faisait
    dependre la garde de l'indentation du fichier.
    """
    try:
        frag = etree.fromstring(f"<racine>{texte}</racine>")
    except Exception:  # noqa: BLE001
        return re.sub(r"\s+", " ", texte).strip()
    for c in frag.xpath("//comment()"):
        c.getparent().remove(c)
    out = (frag.text or "") + "".join(etree.tostring(n, encoding="unicode") for n in frag)
    return re.sub(r"\s+", " ", out).strip()


def _n(texte):
    for avant, apres in ANCIENNES:
        texte = texte.replace(avant, apres)
    return texte


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
        if not retouche or _n(_canon(retouche)) in {_n(_canon(x)) for x in (francais, anglais, _forme(rec, champ, francais),
                                                          _forme(rec, champ, anglais))}:
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
