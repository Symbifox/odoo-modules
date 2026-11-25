"""18.0.1.8.1, étape `post` : protéger les retouches AVANT le catalogue.

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

MODULE = "bf_babillard"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.8.1
LIVRES_FR = {('data/babillard_reaction.xml', 'reaction_a_creuser', 'name'): 'À creuser',
 ('data/babillard_reaction.xml', 'reaction_bien_joue', 'name'): 'Bien joué',
 ('data/babillard_reaction.xml', 'reaction_bonne_idee', 'name'): 'Bonne idée',
 ('data/babillard_reaction.xml', 'reaction_bravo', 'name'): 'Bravo',
 ('data/babillard_reaction.xml', 'reaction_coup_de_coeur', 'name'): 'Coup de cœur',
 ('data/babillard_reaction.xml', 'reaction_d_accord', 'name'): "D'accord",
 ('data/babillard_reaction.xml', 'reaction_en_route', 'name'): 'En route',
 ('data/babillard_reaction.xml', 'reaction_etonnant', 'name'): 'Étonnant',
 ('data/babillard_reaction.xml', 'reaction_excellent', 'name'): 'Excellent',
 ('data/babillard_reaction.xml', 'reaction_jaime', 'name'): "J'aime",
 ('data/babillard_reaction.xml', 'reaction_merci', 'name'): 'Merci',
 ('data/babillard_reaction.xml', 'reaction_rire', 'name'): "Ça m'a fait rire",
 ('data/mail_template.xml', 'mail_template_annonce_a_lire', 'body_html'): '<div>\n'
                                                                          '    <t t-set="societe" '
                                                                          't-value="object.company_id"></t>\n'
                                                                          '    <t t-set="accent" '
                                                                          't-value="(\'report_brand_primary\' '
                                                                          'in societe._fields and '
                                                                          'societe.report_brand_primary) '
                                                                          'or '
                                                                          'societe.email_secondary_color '
                                                                          'or \'#875A7B\'"></t>\n'
                                                                          '    <p style="margin:0 '
                                                                          '0 12px '
                                                                          '0;">Bonjour,</p>\n'
                                                                          '    <p style="margin:0 '
                                                                          '0 12px 0;">\n'
                                                                          '        Une annonce '
                                                                          'vous demande de '
                                                                          'confirmer sa lecture :\n'
                                                                          '        <strong '
                                                                          't-out="object.name or '
                                                                          '\'\'"></strong>\n'
                                                                          '    </p>\n'
                                                                          '    <div t-if="not '
                                                                          'is_html_empty(object.corps_html)" '
                                                                          'style="margin:0 0 16px '
                                                                          '0;" '
                                                                          't-out="object.corps_html"></div>\n'
                                                                          '    <p '
                                                                          'style="margin:24px '
                                                                          '0;">\n'
                                                                          '        <a '
                                                                          't-attf-href="{{ '
                                                                          'object.get_base_url() '
                                                                          '}}/mail/view?model=bf.babillard.post&amp;res_id={{ '
                                                                          'object.id }}" '
                                                                          't-attf-style="background-color:{{ '
                                                                          'accent '
                                                                          '}};color:#FFFFFF;text-decoration:none;padding:12px '
                                                                          '24px;border-radius:6px;font-weight:600;display:inline-block;">\n'
                                                                          '            Lire et '
                                                                          'confirmer\n'
                                                                          '        </a>\n'
                                                                          '    </p>\n'
                                                                          '    <p '
                                                                          'style="margin:0;font-size:13px;color:#6B7280;">\n'
                                                                          '        La confirmation '
                                                                          'se fait dans '
                                                                          "l'application, après la "
                                                                          'connexion : ouvrir ce\n'
                                                                          '        courriel ou '
                                                                          'suivre son lien ne '
                                                                          'compte pas comme une '
                                                                          'lecture.\n'
                                                                          '    </p>\n'
                                                                          '</div>',
 ('data/mail_template.xml', 'mail_template_annonce_a_lire', 'name'): 'Babillard : annonce à lire',
 ('data/mail_template.xml', 'mail_template_annonce_a_lire', 'subject'): 'À lire : {{ object.name '
                                                                        '}}',
 ('data/mail_template.xml', 'mail_template_signalement_recu', 'body_html'): '<div>\n'
                                                                            '    <t '
                                                                            't-set="societe" '
                                                                            't-value="object.post_id.company_id"></t>\n'
                                                                            '    <t t-set="accent" '
                                                                            't-value="(\'report_brand_primary\' '
                                                                            'in societe._fields '
                                                                            'and '
                                                                            'societe.report_brand_primary) '
                                                                            'or '
                                                                            'societe.email_secondary_color '
                                                                            'or \'#875A7B\'"></t>\n'
                                                                            '    <p '
                                                                            'style="margin:0 0 '
                                                                            '12px '
                                                                            '0;">Bonjour,</p>\n'
                                                                            '    <p '
                                                                            'style="margin:0 0 '
                                                                            '12px 0;">Un '
                                                                            'signalement attend '
                                                                            'votre attention au '
                                                                            'babillard.</p>\n'
                                                                            '    <p '
                                                                            'style="margin:24px '
                                                                            '0;">\n'
                                                                            '        <a '
                                                                            't-attf-href="{{ '
                                                                            'object.get_base_url() '
                                                                            '}}/mail/view?model=bf.babillard.signalement&amp;res_id={{ '
                                                                            'object.id }}" '
                                                                            't-attf-style="background-color:{{ '
                                                                            'accent '
                                                                            '}};color:#FFFFFF;text-decoration:none;padding:12px '
                                                                            '24px;border-radius:6px;font-weight:600;display:inline-block;">\n'
                                                                            '            Ouvrir le '
                                                                            'signalement\n'
                                                                            '        </a>\n'
                                                                            '    </p>\n'
                                                                            '    <p '
                                                                            'style="margin:0;font-size:13px;color:#6B7280;">\n'
                                                                            '        Pour '
                                                                            'préserver la '
                                                                            'confidentialité, ce '
                                                                            'courriel ne dit ni ce '
                                                                            'qui est signalé,\n'
                                                                            '        ni qui le '
                                                                            'signale.\n'
                                                                            '    </p>\n'
                                                                            '</div>',
 ('data/mail_template.xml', 'mail_template_signalement_recu', 'name'): 'Babillard : signalement '
                                                                       'reçu',
 ('data/mail_template.xml', 'mail_template_signalement_recu', 'subject'): 'Un signalement attend '
                                                                          'au babillard'}
# Langues dont la clé, si l'en_US lui est égal, est la sortie d'un ancien crochet (pas une retouche).
CROCHET = []
# Anciennes formes LIVRÉES connues (avant, après) : ramenées à l'actuelle avant de comparer.
ANCIENNES = [('"></t>', '"/>'), ('"></strong>', '"/>'), ('"></div>', '"/>')]


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
