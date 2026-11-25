"""18.0.1.2.1, étape `post` : protéger les retouches AVANT le catalogue.

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

MODULE = "bf_appointment_visit"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.2.1
LIVRES_FR = {('data/bf_color_rules.xml', 'bf_color_rule_visit_listing', 'name'): "Visites selon l'inscription",
 ('data/bf_visit_cron.xml', 'cron_bf_visit_close', 'name'): 'Visites : fermer celles qui sont '
                                                            'passées',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_approved', 'body_html'): '<t t-set="contenu">\n'
                                                                           '                    '
                                                                           '<p>Bonjour <t '
                                                                           't-out="object.visitor_name '
                                                                           'or \'\'"></t>,</p>\n'
                                                                           '                    '
                                                                           '<p>\n'
                                                                           '                        '
                                                                           'Votre visite du '
                                                                           '<strong '
                                                                           't-out="object.listing_id.name"></strong> '
                                                                           'est confirmée pour le\n'
                                                                           '                        '
                                                                           '<strong '
                                                                           't-out="format_datetime(object.start, '
                                                                           'tz=object.listing_id.tz_name, '
                                                                           "dt_format='EEEE d MMMM "
                                                                           'y, '
                                                                           'HH:mm\')"></strong>.\n'
                                                                           '                    '
                                                                           '</p>\n'
                                                                           '                    '
                                                                           '<div '
                                                                           't-if="object.listing_id.showing_instructions">\n'
                                                                           '                        '
                                                                           '<p '
                                                                           'style="margin-bottom:4px;"><strong>Avant '
                                                                           'de venir</strong></p>\n'
                                                                           '                        '
                                                                           '<div '
                                                                           't-out="object.listing_id.showing_instructions"></div>\n'
                                                                           '                    '
                                                                           '</div>\n'
                                                                           '                    '
                                                                           '<div '
                                                                           't-if="object.access_released '
                                                                           'and '
                                                                           'object.listing_id.access_instructions" '
                                                                           'style="margin-top:14px; '
                                                                           'padding:12px 14px; '
                                                                           'background-color:#F4F8FB; '
                                                                           'border-left-width:4px; '
                                                                           'border-left-style:solid; '
                                                                           'border-left-color:#29ABE2;">\n'
                                                                           '                        '
                                                                           '<p style="margin:0 0 '
                                                                           '4px '
                                                                           '0;"><strong>Accès</strong></p>\n'
                                                                           '                        '
                                                                           '<div '
                                                                           't-out="object.listing_id.access_instructions"></div>\n'
                                                                           '                    '
                                                                           '</div>\n'
                                                                           '                    <p '
                                                                           't-if="object.listing_id.require_identity" '
                                                                           'style="margin-top:14px; '
                                                                           'color:#6b7280; '
                                                                           'font-size:13px;">\n'
                                                                           '                        '
                                                                           "Une pièce d'identité "
                                                                           'vous sera demandée à '
                                                                           "l'arrivée. Rien n'en "
                                                                           'est photocopié ni '
                                                                           'conservé.\n'
                                                                           '                    '
                                                                           '</p>\n'
                                                                           '                </t>\n'
                                                                           '                <t '
                                                                           't-call="bf_appointment_visit.visit_email_layout"></t>',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_approved', 'name'): 'Visite : confirmée',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_approved', 'subject'): 'Visite confirmée : {{ '
                                                                         'object.listing_id.name '
                                                                         '}}',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_declined', 'body_html'): '<t t-set="contenu">\n'
                                                                           '                    '
                                                                           '<p>Bonjour <t '
                                                                           't-out="object.visitor_name '
                                                                           'or \'\'"></t>,</p>\n'
                                                                           '                    '
                                                                           '<p>\n'
                                                                           '                        '
                                                                           "L'heure demandée pour "
                                                                           'visiter le <strong '
                                                                           't-out="object.listing_id.name"></strong> '
                                                                           'ne peut pas être '
                                                                           'retenue.\n'
                                                                           '                    '
                                                                           '</p>\n'
                                                                           '                    <p '
                                                                           't-if="object.proposed_start">\n'
                                                                           '                        '
                                                                           'Une autre heure vous '
                                                                           'est proposée :\n'
                                                                           '                        '
                                                                           '<strong '
                                                                           't-out="format_datetime(object.proposed_start, '
                                                                           'tz=object.listing_id.tz_name, '
                                                                           "dt_format='EEEE d MMMM "
                                                                           'y, '
                                                                           'HH:mm\')"></strong>.\n'
                                                                           '                        '
                                                                           'Répondez à ce courriel '
                                                                           'si elle vous '
                                                                           'convient.\n'
                                                                           '                    '
                                                                           '</p>\n'
                                                                           '                    <p '
                                                                           't-if="object.seller_note" '
                                                                           't-out="object.seller_note"></p>\n'
                                                                           '                    <p '
                                                                           't-if="not '
                                                                           'object.proposed_start">\n'
                                                                           '                        '
                                                                           'Les autres créneaux '
                                                                           'offerts restent '
                                                                           'disponibles sur la '
                                                                           'page de réservation.\n'
                                                                           '                    '
                                                                           '</p>\n'
                                                                           '                </t>\n'
                                                                           '                <t '
                                                                           't-call="bf_appointment_visit.visit_email_layout"></t>',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_declined', 'name'): 'Visite : refusée',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_declined', 'subject'): 'Votre demande de visite '
                                                                         ': {{ '
                                                                         'object.listing_id.name '
                                                                         '}}',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_feedback_request', 'body_html'): '<t '
                                                                                   't-set="contenu">\n'
                                                                                   '                    '
                                                                                   '<p>Bonjour <t '
                                                                                   't-out="object.visitor_name '
                                                                                   'or '
                                                                                   '\'\'"></t>,</p>\n'
                                                                                   '                    '
                                                                                   '<p>\n'
                                                                                   '                        '
                                                                                   "Merci d'être "
                                                                                   'passé visiter '
                                                                                   'le <strong '
                                                                                   't-out="object.listing_id.name"></strong>.\n'
                                                                                   '                        '
                                                                                   'Deux '
                                                                                   'questions, '
                                                                                   'trente '
                                                                                   'secondes, et '
                                                                                   'le vendeur '
                                                                                   'saura où il en '
                                                                                   'est.\n'
                                                                                   '                    '
                                                                                   '</p>\n'
                                                                                   '                '
                                                                                   '</t>\n'
                                                                                   '                '
                                                                                   '<t '
                                                                                   't-set="bouton_url" '
                                                                                   't-value="object.url_feedback"></t>\n'
                                                                                   '                '
                                                                                   '<t '
                                                                                   't-set="bouton_texte">Donner '
                                                                                   'mon '
                                                                                   'impression</t>\n'
                                                                                   '                '
                                                                                   '<t '
                                                                                   't-call="bf_appointment_visit.visit_email_layout"></t>',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_feedback_request', 'name'): 'Visite : demande de '
                                                                              'rétroaction',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_feedback_request', 'subject'): 'Votre visite du '
                                                                                 '{{ '
                                                                                 'object.listing_id.name '
                                                                                 '}}',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_pending', 'body_html'): '<t t-set="contenu">\n'
                                                                          '                    '
                                                                          '<p>Bonjour <t '
                                                                          't-out="object.visitor_name '
                                                                          'or \'\'"></t>,</p>\n'
                                                                          '                    '
                                                                          '<p>\n'
                                                                          '                        '
                                                                          'Votre demande de visite '
                                                                          'du <strong '
                                                                          't-out="object.listing_id.name"></strong>\n'
                                                                          '                        '
                                                                          'le <strong '
                                                                          't-out="format_datetime(object.start, '
                                                                          'tz=object.listing_id.tz_name, '
                                                                          "dt_format='EEEE d MMMM "
                                                                          'y, HH:mm\')"></strong>\n'
                                                                          '                        '
                                                                          'est transmise pour '
                                                                          'approbation.\n'
                                                                          '                    '
                                                                          '</p>\n'
                                                                          '                    '
                                                                          '<p>Le créneau vous est '
                                                                          'réservé le temps de la '
                                                                          'réponse. Vous recevrez '
                                                                          "un courriel dès qu'elle "
                                                                          'est donnée.</p>\n'
                                                                          '                    <p '
                                                                          't-if="object.listing_id.occupancy '
                                                                          '== \'tenant\'">\n'
                                                                          '                        '
                                                                          'Le logement est occupé '
                                                                          ': la visite est '
                                                                          'annoncée au locataire '
                                                                          'au moins 24 heures '
                                                                          "d'avance.\n"
                                                                          '                    '
                                                                          '</p>\n'
                                                                          '                </t>\n'
                                                                          '                <t '
                                                                          't-call="bf_appointment_visit.visit_email_layout"></t>',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_pending', 'name'): 'Visite : demande transmise',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_pending', 'subject'): 'Votre demande de visite : '
                                                                        '{{ object.listing_id.name '
                                                                        '}}',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_seller_request', 'body_html'): '<t '
                                                                                 't-set="contenu">\n'
                                                                                 '                    '
                                                                                 '<p>Bonjour,</p>\n'
                                                                                 '                    '
                                                                                 '<p>\n'
                                                                                 '                        '
                                                                                 'Une visite du '
                                                                                 '<strong '
                                                                                 't-out="object.listing_id.name"></strong> '
                                                                                 'est demandée '
                                                                                 'pour le\n'
                                                                                 '                        '
                                                                                 '<strong '
                                                                                 't-out="format_datetime(object.start, '
                                                                                 'tz=object.listing_id.tz_name, '
                                                                                 "dt_format='EEEE "
                                                                                 'd MMMM y, '
                                                                                 'HH:mm\')"></strong>.\n'
                                                                                 '                    '
                                                                                 '</p>\n'
                                                                                 '                    '
                                                                                 '<table '
                                                                                 'role="presentation" '
                                                                                 'cellpadding="0" '
                                                                                 'cellspacing="0" '
                                                                                 'border="0" '
                                                                                 'style="margin:12px '
                                                                                 '0; '
                                                                                 'font-size:14px;">\n'
                                                                                 '                        '
                                                                                 '<tbody>\n'
                                                                                 '                            '
                                                                                 '<tr>\n'
                                                                                 '                                '
                                                                                 '<td '
                                                                                 'style="padding:3px '
                                                                                 '14px 3px 0; '
                                                                                 'color:#6b7280;">Personnes '
                                                                                 'attendues</td>\n'
                                                                                 '                                '
                                                                                 '<td '
                                                                                 'style="padding:3px '
                                                                                 '0;"><t '
                                                                                 't-out="object.visitor_count '
                                                                                 'or 1"></t></td>\n'
                                                                                 '                            '
                                                                                 '</tr>\n'
                                                                                 '                            '
                                                                                 '<tr '
                                                                                 't-if="object.representation '
                                                                                 '== \'broker\'">\n'
                                                                                 '                                '
                                                                                 '<td '
                                                                                 'style="padding:3px '
                                                                                 '14px 3px 0; '
                                                                                 'color:#6b7280;">Accompagné '
                                                                                 'de son '
                                                                                 'courtier</td>\n'
                                                                                 '                                '
                                                                                 '<td '
                                                                                 'style="padding:3px '
                                                                                 '0;"><t '
                                                                                 't-out="object.buyer_broker_name '
                                                                                 'or '
                                                                                 '\'oui\'"></t></td>\n'
                                                                                 '                            '
                                                                                 '</tr>\n'
                                                                                 '                            '
                                                                                 '<tr '
                                                                                 't-if="object.representation '
                                                                                 '== \'none\'">\n'
                                                                                 '                                '
                                                                                 '<td '
                                                                                 'style="padding:3px '
                                                                                 '14px 3px 0; '
                                                                                 'color:#6b7280;">Représentation</td>\n'
                                                                                 '                                '
                                                                                 '<td '
                                                                                 'style="padding:3px '
                                                                                 '0;">la personne '
                                                                                 "n'a pas de "
                                                                                 'courtier</td>\n'
                                                                                 '                            '
                                                                                 '</tr>\n'
                                                                                 '                        '
                                                                                 '</tbody>\n'
                                                                                 '                    '
                                                                                 '</table>\n'
                                                                                 '                    '
                                                                                 '<p>Vous pouvez '
                                                                                 'accepter, '
                                                                                 'refuser, ou '
                                                                                 'proposer une '
                                                                                 'autre '
                                                                                 'heure.</p>\n'
                                                                                 '                '
                                                                                 '</t>\n'
                                                                                 '                '
                                                                                 '<t '
                                                                                 't-set="bouton_url" '
                                                                                 't-value="object.url_decision"></t>\n'
                                                                                 '                '
                                                                                 '<t '
                                                                                 't-set="bouton_texte">Répondre '
                                                                                 'à la '
                                                                                 'demande</t>\n'
                                                                                 '                '
                                                                                 '<t '
                                                                                 't-call="bf_appointment_visit.visit_email_layout"></t>',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_seller_request', 'name'): 'Visite : demande au '
                                                                            'vendeur',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_seller_request', 'subject'): 'Demande de visite '
                                                                               ': {{ '
                                                                               'object.listing_id.name '
                                                                               '}}',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_tenant_notice', 'body_html'): '<t '
                                                                                't-set="contenu">\n'
                                                                                '                    '
                                                                                '<p>Bonjour <t '
                                                                                't-out="object.listing_id.tenant_id.name '
                                                                                'or '
                                                                                '\'\'"></t>,</p>\n'
                                                                                '                    '
                                                                                '<p>\n'
                                                                                '                        '
                                                                                'Le logement que '
                                                                                'vous occupez sera '
                                                                                'visité le\n'
                                                                                '                        '
                                                                                '<strong '
                                                                                't-out="format_datetime(object.start, '
                                                                                'tz=object.listing_id.tz_name, '
                                                                                "dt_format='EEEE d "
                                                                                'MMMM y, '
                                                                                'HH:mm\')"></strong>,\n'
                                                                                '                        '
                                                                                'pour une durée '
                                                                                "d'environ <t "
                                                                                't-out="int((object.duration '
                                                                                'or 0.5) * '
                                                                                '60)"></t> '
                                                                                'minutes.\n'
                                                                                '                    '
                                                                                '</p>\n'
                                                                                '                    '
                                                                                '<p>\n'
                                                                                '                        '
                                                                                'Cet avis vous est '
                                                                                'donné au moins 24 '
                                                                                'heures à '
                                                                                "l'avance, et la "
                                                                                'visite a lieu '
                                                                                'entre\n'
                                                                                '                        '
                                                                                '9 h et 21 h, '
                                                                                "comme l'exige "
                                                                                "l'article 1931 du "
                                                                                'Code civil du '
                                                                                'Québec.\n'
                                                                                '                    '
                                                                                '</p>\n'
                                                                                '                    '
                                                                                '<p '
                                                                                't-if="object.listing_id.tenant_wants_presence">\n'
                                                                                '                        '
                                                                                'Vous avez demandé '
                                                                                'la présence du '
                                                                                'locateur pendant '
                                                                                'les visites : '
                                                                                'elle est prévue.\n'
                                                                                '                    '
                                                                                '</p>\n'
                                                                                '                    '
                                                                                '<p '
                                                                                'style="color:#6b7280; '
                                                                                'font-size:13px;">\n'
                                                                                '                        '
                                                                                'Si ce moment ne '
                                                                                'convient pas, '
                                                                                'répondez à ce '
                                                                                'courriel.\n'
                                                                                '                    '
                                                                                '</p>\n'
                                                                                '                '
                                                                                '</t>\n'
                                                                                '                '
                                                                                '<t '
                                                                                't-call="bf_appointment_visit.visit_email_layout"></t>',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_tenant_notice', 'name'): 'Visite : avis au '
                                                                           'locataire',
 ('data/bf_visit_mail_templates.xml', 'mail_visit_tenant_notice', 'subject'): 'Avis de visite : {{ '
                                                                              'object.listing_id.name '
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
    propre (bf_hour_bank ; bf_babillard l'avait eu sous la forme
    `--ancienne-forme`). On compare deux formes canoniques.

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
