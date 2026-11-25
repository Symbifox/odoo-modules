"""18.0.2.2.1 : les libellés passent d'une source française à une source anglaise.

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
from odoo.tools import SQL, file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_celebrations"
# (fichier, identifiant, champ) -> français livré avant 18.0.2.2.1
LIVRES_FR = {('data/celebration_cron.xml', 'cron_generer', 'name'): 'Célébrations : poser les occasions à '
                                                        'venir',
 ('data/celebration_cron.xml', 'cron_inviter', 'name'): 'Célébrations : inviter à répondre',
 ('data/celebration_cron.xml', 'cron_livrer', 'name'): 'Célébrations : livrer les tableaux dus',
 ('data/celebration_cron.xml', 'cron_purger', 'name'): 'Célébrations : effacer les cartes livrées '
                                                       'échues',
 ('data/celebration_cron.xml', 'cron_rappeler', 'name'): 'Célébrations : prévenir qui organise',
 ('data/celebration_cron.xml', 'cron_relancer_minces', 'name'): 'Célébrations : relancer les '
                                                                'tableaux minces',
 ('data/celebration_mail_templates.xml', 'mail_template_invitation', 'body_html'): '<div '
                                                                                   'style="font-family:Lexend,Segoe '
                                                                                   'UI,Arial,sans-serif;font-size:14px;color:#2E3132;line-height:1.6">\n'
                                                                                   '    <p>Bonjour '
                                                                                   '<t '
                                                                                   't-out="object.employee_id.name '
                                                                                   'or '
                                                                                   '\'\'"></t>,</p>\n'
                                                                                   '    <p>On '
                                                                                   'aimerait '
                                                                                   'pouvoir '
                                                                                   'souligner les '
                                                                                   'fêtes au '
                                                                                   'bureau, et '
                                                                                   'pour ça il '
                                                                                   'faut\n'
                                                                                   '       vous '
                                                                                   'demander ce '
                                                                                   'que vous en '
                                                                                   'pensez. Vous '
                                                                                   'décidez, et '
                                                                                   'vous pouvez\n'
                                                                                   '       changer '
                                                                                   "d'idée quand "
                                                                                   'vous '
                                                                                   'voulez.</p>\n'
                                                                                   '    <p>Quatre '
                                                                                   'réponses '
                                                                                   'possibles '
                                                                                   ':</p>\n'
                                                                                   '    <ul '
                                                                                   'style="padding-left:18px">\n'
                                                                                   '        '
                                                                                   '<li><strong>Oui, '
                                                                                   'avec une '
                                                                                   'carte</strong> '
                                                                                   'signée par les '
                                                                                   'collègues</li>\n'
                                                                                   '        '
                                                                                   '<li><strong>Oui, '
                                                                                   'mais sans '
                                                                                   'carte</strong> '
                                                                                   ': la date '
                                                                                   'paraît, '
                                                                                   'personne\n'
                                                                                   '            '
                                                                                   "n'organise "
                                                                                   'rien</li>\n'
                                                                                   '        '
                                                                                   '<li><strong>Non '
                                                                                   'merci</strong> '
                                                                                   ': on vous '
                                                                                   'retire de '
                                                                                   'tout, et on '
                                                                                   'ne\n'
                                                                                   '            '
                                                                                   'vous le '
                                                                                   'redemande '
                                                                                   'plus</li>\n'
                                                                                   '        <li>Ne '
                                                                                   'rien faire, et '
                                                                                   'il ne se passe '
                                                                                   'rien</li>\n'
                                                                                   '    </ul>\n'
                                                                                   '    <p>On vous '
                                                                                   'demande le '
                                                                                   '<strong>jour '
                                                                                   'et le '
                                                                                   'mois</strong> '
                                                                                   'seulement. '
                                                                                   'Votre\n'
                                                                                   '       date de '
                                                                                   'naissance dans '
                                                                                   'le dossier RH '
                                                                                   "n'est pas lue, "
                                                                                   'pas copiée, '
                                                                                   'pas\n'
                                                                                   '       '
                                                                                   'affichée.</p>\n'
                                                                                   '    <p '
                                                                                   'style="margin:24px '
                                                                                   '0">\n'
                                                                                   '        <a '
                                                                                   't-att-href="\'%s/odoo/action-bf_celebrations.action_mon_profil\' '
                                                                                   '% '
                                                                                   "(object.env['ir.config_parameter'].sudo().get_param('web.base.url') "
                                                                                   'or \'\')" '
                                                                                   'style="background:#177AA3;color:#ffffff;padding:11px '
                                                                                   '20px;border-radius:6px;text-decoration:none;display:inline-block">\n'
                                                                                   '            '
                                                                                   'Répondre</a>\n'
                                                                                   '    </p>\n'
                                                                                   '    <p '
                                                                                   'style="color:#6b7280;font-size:12px">Personne '
                                                                                   "d'autre que "
                                                                                   'vous ne voit\n'
                                                                                   '       votre '
                                                                                   'réponse, pas '
                                                                                   'même les '
                                                                                   'ressources '
                                                                                   'humaines.</p>\n'
                                                                                   '</div>',
 ('data/celebration_mail_templates.xml', 'mail_template_invitation', 'name'): 'Célébrations : '
                                                                              'invitation à '
                                                                              'répondre',
 ('data/celebration_mail_templates.xml', 'mail_template_invitation', 'subject'): 'Souhaitez-vous '
                                                                                 "qu'on souligne "
                                                                                 'votre fête ?',
 ('data/celebration_mail_templates.xml', 'mail_template_invitation_signataire', 'body_html'): '<div '
                                                                                              'style="font-family:Lexend,Segoe '
                                                                                              'UI,Arial,sans-serif;font-size:14px;color:#2E3132;line-height:1.6">\n'
                                                                                              '    '
                                                                                              '<p>Bonjour '
                                                                                              '<t '
                                                                                              't-out="ctx.get(\'cel_invite_nom\') '
                                                                                              'or '
                                                                                              '\'\'"></t>,</p>\n'
                                                                                              '    '
                                                                                              '<p>Une '
                                                                                              'carte '
                                                                                              'collective '
                                                                                              'est '
                                                                                              'ouverte '
                                                                                              'pour\n'
                                                                                              '       '
                                                                                              '<strong '
                                                                                              't-out="object.recipient_name '
                                                                                              'or '
                                                                                              '\'\'"></strong> '
                                                                                              ':\n'
                                                                                              '       '
                                                                                              '<t '
                                                                                              't-out="object.name '
                                                                                              'or '
                                                                                              '\'\'"></t>.</p>\n'
                                                                                              '    '
                                                                                              '<p>Un '
                                                                                              'mot, '
                                                                                              'une '
                                                                                              'photo, '
                                                                                              'un '
                                                                                              'tracé '
                                                                                              'à '
                                                                                              'la '
                                                                                              'main '
                                                                                              ': '
                                                                                              'ce '
                                                                                              'que '
                                                                                              'vous '
                                                                                              'auriez '
                                                                                              'écrit\n'
                                                                                              '       '
                                                                                              'dans '
                                                                                              'la '
                                                                                              'carte '
                                                                                              'de '
                                                                                              'papier. '
                                                                                              'Aucun '
                                                                                              'compte '
                                                                                              "n'est "
                                                                                              'demandé.</p>\n'
                                                                                              '    '
                                                                                              '<p '
                                                                                              'style="margin:24px '
                                                                                              '0">\n'
                                                                                              '        '
                                                                                              '<a '
                                                                                              't-att-href="object.contribution_url" '
                                                                                              'style="background:#177AA3;color:#ffffff;padding:11px '
                                                                                              '20px;border-radius:6px;text-decoration:none;display:inline-block">\n'
                                                                                              '            '
                                                                                              'Signer '
                                                                                              'la '
                                                                                              'carte</a>\n'
                                                                                              '    '
                                                                                              '</p>\n'
                                                                                              '    '
                                                                                              '<p '
                                                                                              't-if="object.delivery_date">La '
                                                                                              'carte '
                                                                                              'part '
                                                                                              'le\n'
                                                                                              '       '
                                                                                              '<strong '
                                                                                              't-out="format_datetime(object.delivery_date, '
                                                                                              'tz=user.tz '
                                                                                              'or '
                                                                                              "'UTC', "
                                                                                              'dt_format=\'long\')"></strong>.</p>\n'
                                                                                              '    '
                                                                                              '<p '
                                                                                              'style="color:#6b7280;font-size:12px">La '
                                                                                              'personne '
                                                                                              'fêtée '
                                                                                              'ne '
                                                                                              'voit '
                                                                                              'rien\n'
                                                                                              '       '
                                                                                              'avant '
                                                                                              'la '
                                                                                              'livraison '
                                                                                              ': '
                                                                                              'gardez-lui '
                                                                                              'la '
                                                                                              'surprise, '
                                                                                              'et '
                                                                                              'ne '
                                                                                              'lui '
                                                                                              'transférez '
                                                                                              'pas\n'
                                                                                              '       '
                                                                                              'ce '
                                                                                              'message.</p>\n'
                                                                                              '</div>',
 ('data/celebration_mail_templates.xml', 'mail_template_invitation_signataire', 'name'): 'Célébrations '
                                                                                         ': '
                                                                                         'invitation '
                                                                                         'à signer',
 ('data/celebration_mail_templates.xml', 'mail_template_invitation_signataire', 'subject'): 'Une '
                                                                                            'carte '
                                                                                            'à '
                                                                                            'signer '
                                                                                            'pour '
                                                                                            '{{ '
                                                                                            'object.recipient_name '
                                                                                            '}}',
 ('data/celebration_mail_templates.xml', 'mail_template_livraison', 'body_html'): '<div '
                                                                                  'style="font-family:Lexend,Segoe '
                                                                                  'UI,Arial,sans-serif;font-size:14px;color:#2E3132;line-height:1.6">\n'
                                                                                  '    <p>Bonjour '
                                                                                  '<t '
                                                                                  't-out="object.recipient_name '
                                                                                  'or '
                                                                                  '\'\'"></t>,</p>\n'
                                                                                  '    <p>Vos '
                                                                                  'collègues vous '
                                                                                  'ont écrit.\n'
                                                                                  '       <strong '
                                                                                  't-out="object.published_post_count"></strong> '
                                                                                  'message(s) '
                                                                                  'vous\n'
                                                                                  '       '
                                                                                  'attendent. '
                                                                                  'Ouvrez '
                                                                                  "l'enveloppe.</p>\n"
                                                                                  '    <p '
                                                                                  'style="margin:24px '
                                                                                  '0">\n'
                                                                                  '        <a '
                                                                                  't-att-href="object.recipient_url" '
                                                                                  'style="background:#177AA3;color:#ffffff;padding:11px '
                                                                                  '20px;border-radius:6px;text-decoration:none;display:inline-block">\n'
                                                                                  '            '
                                                                                  'Ouvrir la '
                                                                                  'carte</a>\n'
                                                                                  '    </p>\n'
                                                                                  '    <p '
                                                                                  'style="color:#6b7280;font-size:12px">En '
                                                                                  'pièces jointes, '
                                                                                  'la carte à\n'
                                                                                  '       garder : '
                                                                                  'en PDF, et en '
                                                                                  'page complète '
                                                                                  'où les images '
                                                                                  'animées '
                                                                                  'bougent\n'
                                                                                  '       encore. '
                                                                                  'Le lien, lui, '
                                                                                  'ne durera pas '
                                                                                  'toujours ; ces '
                                                                                  'deux fichiers\n'
                                                                                  '       sont à '
                                                                                  'vous. Depuis la '
                                                                                  'carte, vous '
                                                                                  'pouvez dire '
                                                                                  'merci à celles '
                                                                                  'et ceux\n'
                                                                                  '       qui ont '
                                                                                  'signé : ce lien '
                                                                                  'est le vôtre, '
                                                                                  'ne le '
                                                                                  'transférez '
                                                                                  'pas.</p>\n'
                                                                                  '</div>',
 ('data/celebration_mail_templates.xml', 'mail_template_livraison', 'name'): 'Célébrations : '
                                                                             'livraison du tableau',
 ('data/celebration_mail_templates.xml', 'mail_template_livraison', 'subject'): '{{ object.name }}',
 ('data/celebration_mail_templates.xml', 'mail_template_merci', 'body_html'): '<div '
                                                                              'style="font-family:Lexend,Segoe '
                                                                              'UI,Arial,sans-serif;font-size:14px;color:#2E3132;line-height:1.6">\n'
                                                                              '    '
                                                                              '<p>Bonjour,</p>\n'
                                                                              '    <p><strong '
                                                                              't-out="object.recipient_name '
                                                                              'or \'\'"></strong> '
                                                                              'a reçu sa carte,\n'
                                                                              '       <t '
                                                                              't-out="object.name '
                                                                              'or \'\'"></t>, et a '
                                                                              'écrit un mot pour '
                                                                              'celles et ceux\n'
                                                                              "       qui l'ont "
                                                                              'signée :</p>\n'
                                                                              '    <blockquote '
                                                                              'style="margin:16px '
                                                                              '0;padding:12px '
                                                                              '16px;border-left:4px '
                                                                              'solid '
                                                                              '#177AA3;background:#F4F6F8" '
                                                                              't-out="object.thanks_html"></blockquote>\n'
                                                                              '    <p '
                                                                              'style="margin:24px '
                                                                              '0">\n'
                                                                              '        <a '
                                                                              't-att-href="object.board_url" '
                                                                              'style="background:#177AA3;color:#ffffff;padding:11px '
                                                                              '20px;border-radius:6px;text-decoration:none;display:inline-block">\n'
                                                                              '            Revoir '
                                                                              'la carte</a>\n'
                                                                              '    </p>\n'
                                                                              '</div>',
 ('data/celebration_mail_templates.xml', 'mail_template_merci', 'name'): 'Célébrations : le merci '
                                                                         'de la personne fêtée',
 ('data/celebration_mail_templates.xml', 'mail_template_merci', 'subject'): '{{ '
                                                                            'object.recipient_name '
                                                                            '}} vous remercie',
 ('data/celebration_mail_templates.xml', 'mail_template_rappel_organisateur', 'body_html'): '<div '
                                                                                            'style="font-family:Lexend,Segoe '
                                                                                            'UI,Arial,sans-serif;font-size:14px;color:#2E3132;line-height:1.6">\n'
                                                                                            '    '
                                                                                            '<p>Bonjour,</p>\n'
                                                                                            '    '
                                                                                            '<p><t '
                                                                                            't-out="object.name '
                                                                                            'or '
                                                                                            '\'\'"></t>, '
                                                                                            'le\n'
                                                                                            '       '
                                                                                            '<strong '
                                                                                            't-out="object.date '
                                                                                            'and '
                                                                                            "object.date.strftime('%d/%m/%Y') "
                                                                                            'or '
                                                                                            '\'\'"></strong>.</p>\n'
                                                                                            '    '
                                                                                            '<p>Rien '
                                                                                            "n'est "
                                                                                            'encore '
                                                                                            'ouvert. '
                                                                                            'Un '
                                                                                            'tableau '
                                                                                            'de '
                                                                                            'vœux '
                                                                                            'se '
                                                                                            'crée '
                                                                                            'en un '
                                                                                            'clic, '
                                                                                            'et '
                                                                                            'le\n'
                                                                                            '       '
                                                                                            'lien '
                                                                                            'se '
                                                                                            'partage '
                                                                                            'ensuite '
                                                                                            'comme '
                                                                                            'vous '
                                                                                            'voulez '
                                                                                            ': '
                                                                                            'courriel, '
                                                                                            'canal '
                                                                                            "d'équipe,\n"
                                                                                            '       '
                                                                                            'ou un '
                                                                                            'code '
                                                                                            'QR '
                                                                                            'imprimé.</p>\n'
                                                                                            '    '
                                                                                            '<p '
                                                                                            'style="margin:24px '
                                                                                            '0">\n'
                                                                                            '        '
                                                                                            '<a '
                                                                                            't-att-href="\'%s/odoo/bf.celebration.occasion/%s\' '
                                                                                            '% '
                                                                                            "(object.env['ir.config_parameter'].sudo().get_param('web.base.url') "
                                                                                            'or '
                                                                                            "'', "
                                                                                            'object.id)" '
                                                                                            'style="background:#177AA3;color:#ffffff;padding:11px '
                                                                                            '20px;border-radius:6px;text-decoration:none;display:inline-block">\n'
                                                                                            '            '
                                                                                            'Ouvrir '
                                                                                            'la '
                                                                                            'carte</a>\n'
                                                                                            '    '
                                                                                            '</p>\n'
                                                                                            '    '
                                                                                            '<p '
                                                                                            'style="color:#6b7280;font-size:12px">Cette '
                                                                                            'personne '
                                                                                            'a '
                                                                                            'accepté '
                                                                                            "qu'on\n"
                                                                                            '       '
                                                                                            'souligne '
                                                                                            'sa '
                                                                                            'fête. '
                                                                                            'Elle '
                                                                                            'ne '
                                                                                            'verra '
                                                                                            'rien '
                                                                                            'avant '
                                                                                            'la '
                                                                                            'livraison.</p>\n'
                                                                                            '</div>',
 ('data/celebration_mail_templates.xml', 'mail_template_rappel_organisateur', 'name'): 'Célébrations '
                                                                                       ': rappel à '
                                                                                       'qui '
                                                                                       'organise',
 ('data/celebration_mail_templates.xml', 'mail_template_rappel_organisateur', 'subject'): '{{ '
                                                                                          'object.person_name '
                                                                                          '}} : '
                                                                                          'une '
                                                                                          'carte à '
                                                                                          'ouvrir '
                                                                                          'avant '
                                                                                          'le {{ '
                                                                                          'object.date '
                                                                                          '}}',
 ('data/celebration_mail_templates.xml', 'mail_template_tableau_mince', 'body_html'): '<div '
                                                                                      'style="font-family:Lexend,Segoe '
                                                                                      'UI,Arial,sans-serif;font-size:14px;color:#2E3132;line-height:1.6">\n'
                                                                                      '    '
                                                                                      '<p>Bonjour,</p>\n'
                                                                                      '    <p>La '
                                                                                      'carte de '
                                                                                      '<strong '
                                                                                      't-out="object.recipient_name '
                                                                                      'or '
                                                                                      '\'\'"></strong> '
                                                                                      'part '
                                                                                      'bientôt\n'
                                                                                      '       et '
                                                                                      'elle porte\n'
                                                                                      '       '
                                                                                      '<strong '
                                                                                      't-out="object.published_post_count"></strong> '
                                                                                      'message(s).</p>\n'
                                                                                      '    <p>Le '
                                                                                      'lien à '
                                                                                      'repartager '
                                                                                      ':</p>\n'
                                                                                      '    <p><a '
                                                                                      't-att-href="object.contribution_url" '
                                                                                      't-out="object.contribution_url"></a></p>\n'
                                                                                      '    <p '
                                                                                      'style="color:#6b7280;font-size:12px">Une '
                                                                                      'carte vide '
                                                                                      "n'est pas "
                                                                                      'livrée :\n'
                                                                                      '       elle '
                                                                                      'est retenue '
                                                                                      'et vous '
                                                                                      'êtes '
                                                                                      'prévenu.</p>\n'
                                                                                      '</div>',
 ('data/celebration_mail_templates.xml', 'mail_template_tableau_mince', 'name'): 'Célébrations : '
                                                                                 'le tableau est '
                                                                                 'mince',
 ('data/celebration_mail_templates.xml', 'mail_template_tableau_mince', 'subject'): 'La carte de '
                                                                                    '{{ '
                                                                                    'object.recipient_name '
                                                                                    '}} est encore '
                                                                                    'bien vide'}
# Langues dont la clé, si l'en_US lui est égal, est la sortie d'un ancien crochet (pas une retouche).
CROCHET = []
# Anciennes formes LIVRÉES connues (avant, après) : ramenées à l'actuelle avant de comparer.
# Les cinq premières formes ne sont que de la sérialisation : `etree.tostring`
# développe une balise vide en `<t></t>` là où Odoo la range en `<t/>`.
#
# 🔴 Les deux dernières sont autre chose : le corps du gabarit de LIVRAISON a
# été RÉÉCRIT dans le fichier de données depuis l'installation, et `noupdate`
# a empêché la base de suivre. Elle porte donc une rédaction antérieure — du
# français, dans la fente `en_US` — que la garde « non retouché ? » refusait à
# juste titre de reconnaître. Ce n'est pas la retouche d'un opérateur, c'est
# une version périmée du produit : les deux phrases d'avant sont déclarées ici
# pour que la bascule la reconnaisse et la remplace. Mesuré sur une base
# d'essai (687 caractères en base contre 923 au fichier).
ANCIENNES = [
    # 🔴 Répétition sur une base réelle : les six corps y étaient jugés
    # « retouchés à la main » et restaient en français dans la fente en_US. Ce n'étaient pas des
    # retouches : (1) la couleur du texte d'avant la charte (#2D3031), (2) les attributs que
    # l'assainisseur d'Odoo ajoute à un <blockquote> en le rangeant, (3) l'invitation dans sa
    # rédaction d'AVANT l'épicène. Normalisés EN TÊTE : la paire de la
    # livraison, plus bas, doit mordre aussi sur la couleur d'avant.
    ('color:#2D3031', 'color:#2E3132'),
    (' data-o-mail-quote-node="1" data-o-mail-quote="1"', ''),
    (' data-o-mail-quote="1"', ''),
    ('<strong>Non merci</strong> : vous êtes retiré de tout, et on ne',
     '<strong>Non merci</strong> : on vous retire de tout, et on ne'),
    # 🔴 LA PREMIERE PAIRE N'EST PAS DE LA SERIALISATION. Le corps du gabarit de
    # LIVRAISON a ete reecrit dans le fichier de donnees depuis l'installation, et
    # `noupdate` a empeche la base de suivre : elle porte une redaction
    # ANTERIEURE — du francais, dans la fente `en_US` — que la garde « non
    # retouche ? » refusait a juste titre de reconnaitre. Ce n'est pas la retouche
    # d'un operateur, c'est une version perimee du produit.
    #
    # ⚠️ Les deux textes sont RECOPIES DES OCTETS, jamais retapes : un premier
    # essai les avait ecrits a la main avec une espace insecable que la base
    # n'avait pas, et la bascule a echoue en silence (mesuré sur une base d'essai).
    ('<div style="font-family:Lexend,Segoe UI,Arial,sans-serif;font-size:14px;color:#2E3132;line-height:1.6">\n    <p>Bonjour <t t-out="object.recipient_name or \'\'"/>,</p>\n    <p>Vos collègues vous ont écrit.\n       <strong t-out="object.published_post_count"/> message(s) vous\n       attendent sur une page qui restera à vous.</p>\n    <p style="margin:24px 0">\n        <a t-att-href="object.recipient_url" style="background:#177AA3;color:#ffffff;padding:11px 20px;border-radius:6px;text-decoration:none;display:inline-block">\n            Ouvrir la carte</a>\n    </p>\n    <p style="color:#6b7280;font-size:12px">Le lien reste valide : revenez-y\n       quand vous voulez.</p>\n</div>',
     '<div style="font-family:Lexend,Segoe UI,Arial,sans-serif;font-size:14px;color:#2E3132;line-height:1.6">\n    <p>Bonjour <t t-out="object.recipient_name or \'\'"></t>,</p>\n    <p>Vos collègues vous ont écrit.\n       <strong t-out="object.published_post_count"></strong> message(s) vous\n       attendent. Ouvrez l\'enveloppe.</p>\n    <p style="margin:24px 0">\n        <a t-att-href="object.recipient_url" style="background:#177AA3;color:#ffffff;padding:11px 20px;border-radius:6px;text-decoration:none;display:inline-block">\n            Ouvrir la carte</a>\n    </p>\n    <p style="color:#6b7280;font-size:12px">En pièces jointes, la carte à\n       garder : en PDF, et en page complète où les images animées bougent\n       encore. Le lien, lui, ne durera pas toujours ; ces deux fichiers\n       sont à vous. Depuis la carte, vous pouvez dire merci à celles et ceux\n       qui ont signé : ce lien est le vôtre, ne le transférez pas.</p>\n</div>'),
    # Le reste n'est que de la serialisation : `etree.tostring` developpe une
    # balise vide en « <t></t> » la ou Odoo la range en « <t/> ».
    ('"></t>', '"/>'), ('"></strong>', '"/>'), ('"></div>', '"/>'),
    ('"></a>', '"/>'), ('"></blockquote>', '"/>'),
]

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
        if _n(en_us) not in {_n(x) for x in livre_fr} and not crochet:
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
                if lang.startswith("fr") or _n((brut.get(lang) or "").strip()) not in {_n(x) for x in formes}:
                    continue
                # La valeur en_US telle qu'Odoo l'a RANGÉE (assainie), pas le texte brut du fichier.
                cr.execute(SQL("UPDATE %s SET %s = %s || jsonb_build_object(%s, %s::text) WHERE id = %s",
                               SQL.identifier(porteur._table), SQL.identifier(champ),
                               SQL.identifier(champ), lang, brut.get("en_US") or anglais, porteur.id))
                _logger.info("%s : %s.%s, clé %s passée à l'anglais livré", MODULE, xmlid, champ, lang)
            porteur.invalidate_recordset([champ])
