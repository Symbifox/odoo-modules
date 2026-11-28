"""18.0.1.17.0 : les libellés passent d'une source française à une source anglaise.

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
    # AVANT le rechargement du catalogue : après lui, fr_CA porte déjà le NOUVEAU
    # français, et une clé copiée de l'ancien fr_CA ne serait plus reconnue.
    # Gabarits de courriel du module : une langue NON française (en_CA par exemple) dont la valeur est
    # la copie exacte de fr_CA est un reste de l'ancienne source française ; on retire sa clé, et
    # le lecteur retombe sur la source anglaise. Une valeur propre à cette langue reste.
    autres = [l for l in langues if not l.startswith("fr")]
    if autres and "fr_CA" in langues:
        gabarits = env["ir.model.data"].search([("module", "=", MODULE), ("model", "=", "mail.template")])
        for champ in ("name", "subject", "body_html"):
            for lang in autres:
                cr.execute(SQL(
                    "UPDATE mail_template SET %s = %s - %s WHERE id = ANY(%s) AND %s ? %s"
                    " AND %s->>%s = %s->>'fr_CA' RETURNING id",
                    SQL.identifier(champ), SQL.identifier(champ), lang, gabarits.mapped("res_id"),
                    SQL.identifier(champ), lang, SQL.identifier(champ), lang, SQL.identifier(champ)))
                ids = [r[0] for r in cr.fetchall()]
                if ids:
                    _logger.info("%s : %s, clé %s copiée de fr_CA retirée (%s gabarit(s))", MODULE, champ, lang, len(ids))
        env["mail.template"].invalidate_model()
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
    arbres = {}
    for (fichier, xmlid, champ), francais in LIVRES_FR.items():
        if fichier not in arbres:
            with file_open(f"{MODULE}/{fichier}", "rb") as f:
                arbres[fichier] = etree.parse(f)
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        noeud = arbres[fichier].find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        anglais = _contenu(noeud).strip()
        if not rec or not anglais:
            continue
        rec_en = rec.with_context(lang="en_US")
        porteur = _porteur(rec_en)
        porteur.flush_recordset([champ])  # la base, pas un tampon de l'ORM
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        avant = (cr.fetchone() or [None])[0]
        avant = avant if isinstance(avant, dict) else {"en_US": avant or ""}
        en_us = (avant.get("en_US") or "").strip()
        if en_us in (anglais, _forme(rec_en, champ, anglais)):
            continue  # déjà l'anglais livré : rien à faire, rien à signaler
        crochet = any(en_us and (avant.get(l) or "").strip() == en_us for l in CROCHET)
        livre_fr = (francais, _forme(rec_en, champ, francais))
        if _n(_canon(en_us)) not in {_n(_canon(x)) for x in livre_fr} and not crochet:
            # Ses langues manquantes ont reçu la retouche à l'étape `post`.
            _logger.warning("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        # ⚠️ Un travail planifié s'écrit par son action : ir.cron.write prend un verrou
        # NOWAIT qui ferait ÉCHOUER la montée si le travail tourne à ce moment (le recalcul
        # de `cron_name` réécrit quand même sa ligne : la montée ATTEND le travail).
        if callable(porteur._fields[champ].translate):
            # 🔴 Champ traduit TERME À TERME (html_translate) : un write en en_US réassocie les
            # termes des autres langues à la nouvelle source anglaise, et le français livré, sans
            # terme commun, y serait remplacé par l'anglais. On pose la seule clé en_US.
            cr.execute(SQL("UPDATE %s SET %s = COALESCE(%s, '{}'::jsonb) || jsonb_build_object('en_US', %s::text) WHERE id = %s",
                           SQL.identifier(porteur._table), SQL.identifier(champ), SQL.identifier(champ),
                           anglais, porteur.id))
            porteur.invalidate_recordset([champ])
        else:
            porteur.write({champ: anglais})
            # 🔴 L'ORM diffère l'écriture : sans ce vidage, la relecture SQL ci-dessous lit l'ANCIEN
            # en_US, et la clé en_CA d'une sortie de crochet reçoit l'ancienne valeur.
            porteur.flush_recordset([champ])
        # Les autres langues NON françaises installées (en_CA par exemple) qui portent encore le français
        # LIVRÉ reçoivent aussi l'anglais : sinon un lecteur en_CA garde le français.
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        brut = (cr.fetchone() or [None])[0]
        if isinstance(brut, dict):
            formes = (francais, _forme(rec_en, champ, francais), en_us)
            for lang in langues:
                # Langue non française qui portait le français livré, ou la copie de l'ancien en_US
                # (sortie du crochet) : l'anglais livré.
                if lang.startswith("fr") or _n(_canon((brut.get(lang) or "").strip())) not in {_n(_canon(x)) for x in formes}:
                    continue
                # La valeur en_US telle qu'Odoo l'a RANGÉE (assainie), pas le texte brut du fichier.
                cr.execute(SQL("UPDATE %s SET %s = %s || jsonb_build_object(%s, %s::text) WHERE id = %s",
                               SQL.identifier(porteur._table), SQL.identifier(champ),
                               SQL.identifier(champ), lang, brut.get("en_US") or anglais, porteur.id))
                _logger.info("%s : %s.%s, clé %s passée à l'anglais livré", MODULE, xmlid, champ, lang)
            porteur.invalidate_recordset([champ])
