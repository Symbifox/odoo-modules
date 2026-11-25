"""18.0.2.2.1, étape `post` : protéger les retouches AVANT le catalogue.

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
ANCIENNES = [('"></t>', '"/>'), ('"></strong>', '"/>'), ('"></div>', '"/>'), ('"></a>', '"/>'), ('"></blockquote>', '"/>')]


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
        if not retouche or _n(retouche) in {_n(x) for x in (francais, anglais, _forme(rec, champ, francais),
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
