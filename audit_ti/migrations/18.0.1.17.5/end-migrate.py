"""18.0.1.17.5 : les libellés passent d'une source française à une source anglaise.

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

MODULE = "audit_ti"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.17.5
LIVRES_FR = {('data/audit_element_data.xml', 'element_1', 'best_practices'): '<ul>\n'
                                                                 '                <li>Politique de '
                                                                 'mots de passe documentée '
                                                                 '(longueur min 12 car., '
                                                                 'complexité, rotation)</li>\n'
                                                                 '                <li>Politique de '
                                                                 'chiffrement des données au repos '
                                                                 'et en transit</li>\n'
                                                                 '                <li>Procédure de '
                                                                 'sauvegarde et restauration '
                                                                 'testée</li>\n'
                                                                 '                <li>Plan de '
                                                                 'réponse aux incidents '
                                                                 'documenté</li>\n'
                                                                 '                <li>Revue '
                                                                 'annuelle des politiques</li>\n'
                                                                 '            </ul>',
 ('data/audit_element_data.xml', 'element_1', 'description'): 'Politiques mots de passe, '
                                                              'chiffrement, sauvegardes',
 ('data/audit_element_data.xml', 'element_1', 'name'): 'Politiques & Procédures TI',
 ('data/audit_element_data.xml', 'element_10', 'best_practices'): '<ul>\n'
                                                                  '                <li>Sauvegardes '
                                                                  'régulières (quotidiennes '
                                                                  'minimum)</li>\n'
                                                                  '                <li>Règle 3-2-1 '
                                                                  '(3 copies, 2 médias, 1 '
                                                                  'hors-site)</li>\n'
                                                                  '                <li>Tests de '
                                                                  'restauration périodiques '
                                                                  '(mock-restore)</li>\n'
                                                                  '                <li>Plan de '
                                                                  'reprise après sinistre '
                                                                  'documenté et testé</li>\n'
                                                                  '                <li>Chiffrement '
                                                                  'des sauvegardes</li>\n'
                                                                  '            </ul>',
 ('data/audit_element_data.xml', 'element_10', 'description'): 'Redondance, mock-restore, accès',
 ('data/audit_element_data.xml', 'element_10', 'name'): 'Sauvegardes & reprise',
 ('data/audit_element_data.xml', 'element_11', 'best_practices'): '<ul>\n'
                                                                  '                <li>Chiffrement '
                                                                  'de disque complet (BitLocker, '
                                                                  'FileVault)</li>\n'
                                                                  '                <li>Politique '
                                                                  'BYOD documentée si '
                                                                  'applicable</li>\n'
                                                                  '                <li>Gestion des '
                                                                  'appareils mobiles (MDM) si '
                                                                  'applicable</li>\n'
                                                                  '                '
                                                                  '<li>Verrouillage automatique '
                                                                  'des sessions</li>\n'
                                                                  '            </ul>',
 ('data/audit_element_data.xml', 'element_11', 'description'): 'Chiffrement disque, MDM, BYOD',
 ('data/audit_element_data.xml', 'element_11', 'name'): 'Clients (PC/mobiles)',
 ('data/audit_element_data.xml', 'element_12', 'best_practices'): '<ul>\n'
                                                                  '                <li>Clause de '
                                                                  'confidentialité dans tous les '
                                                                  'contrats fournisseurs</li>\n'
                                                                  '                <li>SLA '
                                                                  'documenté avec temps de réponse '
                                                                  'pour les incidents</li>\n'
                                                                  '                <li>Clause de '
                                                                  "notification en cas d'incident "
                                                                  'de confidentialité</li>\n'
                                                                  '                <li>Clause de '
                                                                  'restitution/destruction des '
                                                                  'données en fin de contrat</li>\n'
                                                                  '            </ul>',
 ('data/audit_element_data.xml', 'element_12', 'description'): 'Confidentialité, incidents, '
                                                               'restitution',
 ('data/audit_element_data.xml', 'element_12', 'name'): 'Contrats & SLA',
 ('data/audit_element_data.xml', 'element_13', 'best_practices'): '<ul>\n'
                                                                  '                <li>Engagement '
                                                                  'écrit du fournisseur à protéger '
                                                                  'les RP</li>\n'
                                                                  '                <li>Mesures de '
                                                                  'sécurité proportionnelles à la '
                                                                  'sensibilité des données</li>\n'
                                                                  '                <li>Droit '
                                                                  "d'audit sur les pratiques du "
                                                                  'fournisseur</li>\n'
                                                                  '                '
                                                                  '<li>Notification obligatoire en '
                                                                  "cas d'incident impliquant des "
                                                                  'RP</li>\n'
                                                                  '                <li>Liste des '
                                                                  'sous-traitants ayant accès aux '
                                                                  'données</li>\n'
                                                                  '                <li>Mêmes '
                                                                  'exigences de sécurité imposées '
                                                                  'aux sous-traitants</li>\n'
                                                                  '                '
                                                                  '<li>Notification en cas de '
                                                                  'changement de '
                                                                  'sous-traitant</li>\n'
                                                                  '            </ul>',
 ('data/audit_element_data.xml', 'element_13', 'description'): 'Protection RP fournisseur et ses '
                                                               'sous-traitants',
 ('data/audit_element_data.xml', 'element_13', 'name'): 'Conformité fournisseurs & sous-traitants',
 ('data/audit_element_data.xml', 'element_14', 'best_practices'): '<ul>\n'
                                                                  '                <li>Données '
                                                                  'hébergées par un fournisseur '
                                                                  'souverain (non soumis au Cloud '
                                                                  'Act/FISA)</li>\n'
                                                                  '                <li>OU '
                                                                  'hébergement on-prem contrôlé '
                                                                  "par l'organisation</li>\n"
                                                                  '                <li>Si '
                                                                  'fournisseur US (Microsoft, '
                                                                  'Google, AWS) : risque Cloud Act '
                                                                  'documenté, EFVP réalisée</li>\n'
                                                                  '                <li>Pas de '
                                                                  'transfert hors Québec sans '
                                                                  'évaluation des facteurs '
                                                                  'relatifs à la vie privée</li>\n'
                                                                  '                <li>Préférence '
                                                                  'pour les fournisseurs '
                                                                  'québécois/canadiens pour les RP '
                                                                  'sensibles</li>\n'
                                                                  '                <li>Garantie '
                                                                  'contractuelle de localisation '
                                                                  'et de contrôle des '
                                                                  'données</li>\n'
                                                                  '                <li>Plan de '
                                                                  'rapatriement des données si '
                                                                  'nécessaire</li>\n'
                                                                  '            </ul>',
 ('data/audit_element_data.xml', 'element_14', 'description'): 'Stockage souverain, Cloud Act, '
                                                               'localisation données',
 ('data/audit_element_data.xml', 'element_14', 'name'): 'Souveraineté des données',
 ('data/audit_element_data.xml', 'element_2', 'best_practices'): '<ul>\n'
                                                                 '                <li>Inventaire '
                                                                 'complet des systèmes et '
                                                                 'applications</li>\n'
                                                                 '                <li>Cartographie '
                                                                 'des bases de données contenant '
                                                                 'des RP</li>\n'
                                                                 '                <li>Liste des '
                                                                 'services SaaS et cloud '
                                                                 'utilisés</li>\n'
                                                                 '                '
                                                                 '<li>Documentation des flux de '
                                                                 'données entre systèmes</li>\n'
                                                                 '            </ul>',
 ('data/audit_element_data.xml', 'element_2', 'description'): 'Systèmes, BDD, SaaS, flux de '
                                                              'données',
 ('data/audit_element_data.xml', 'element_2', 'name'): 'Registre des actifs',
 ('data/audit_element_data.xml', 'element_3', 'best_practices'): '<ul>\n'
                                                                 '                <li>Inventaire '
                                                                 'des renseignements personnels '
                                                                 'par catégorie (clients, '
                                                                 'employés, enfants)</li>\n'
                                                                 '                '
                                                                 '<li>Identification des données '
                                                                 'sensibles (santé, '
                                                                 'financier)</li>\n'
                                                                 '                <li>Registre des '
                                                                 'caméras de surveillance et '
                                                                 "journaux d'accès</li>\n"
                                                                 '                <li>Durée de '
                                                                 'conservation définie pour chaque '
                                                                 'catégorie</li>\n'
                                                                 '            </ul>',
 ('data/audit_element_data.xml', 'element_3', 'description'): 'Clients, employés, caméras, '
                                                              'journaux',
 ('data/audit_element_data.xml', 'element_3', 'name'): 'Inventaire des RP',
 ('data/audit_element_data.xml', 'element_4', 'best_practices'): '<ul>\n'
                                                                 '                <li>Principe du '
                                                                 'moindre privilège appliqué à '
                                                                 'tous les comptes</li>\n'
                                                                 '                <li>Revue '
                                                                 'périodique des droits '
                                                                 "d'accès</li>\n"
                                                                 '                <li>Séparation '
                                                                 'des rôles administrateurs et '
                                                                 'utilisateurs</li>\n'
                                                                 '                <li>Collecte '
                                                                 'minimale de données '
                                                                 'personnelles</li>\n'
                                                                 '            </ul>',
 ('data/audit_element_data.xml', 'element_4', 'description'): "Droits d'accès minimaux, données "
                                                              'minimales',
 ('data/audit_element_data.xml', 'element_4', 'name'): 'Moindre privilège',
 ('data/audit_element_data.xml', 'element_5', 'best_practices'): '<ul>\n'
                                                                 '                <li>TLS 1.2+ '
                                                                 'pour toutes les '
                                                                 'communications</li>\n'
                                                                 '                <li>HTTPS '
                                                                 'obligatoire sur tous les '
                                                                 'portails web</li>\n'
                                                                 '                <li>Certificats '
                                                                 'valides et renouvelés '
                                                                 'automatiquement</li>\n'
                                                                 '                <li>Rotation '
                                                                 'périodique des clés de '
                                                                 'chiffrement</li>\n'
                                                                 '                <li>Séparation '
                                                                 'des rôles pour la gestion des '
                                                                 'clés</li>\n'
                                                                 '                <li>Chiffrement '
                                                                 'des courriels sensibles</li>\n'
                                                                 '            </ul>',
 ('data/audit_element_data.xml', 'element_5', 'description'): 'TLS/HTTPS, rotation clefs, '
                                                              'séparation rôles',
 ('data/audit_element_data.xml', 'element_5', 'name'): 'Chiffrement & gestion des clefs',
 ('data/audit_element_data.xml', 'element_6', 'best_practices'): '<ul>\n'
                                                                 '                '
                                                                 '<li>Authentification '
                                                                 'multifacteur (MFA) obligatoire '
                                                                 'pour les accès '
                                                                 'administrateurs</li>\n'
                                                                 '                <li>MFA '
                                                                 'recommandée pour tous les '
                                                                 'utilisateurs</li>\n'
                                                                 '                <li>Gestion '
                                                                 'centralisée des identités '
                                                                 '(IAM/SSO)</li>\n'
                                                                 '                <li>Processus de '
                                                                 'révocation des accès lors de '
                                                                 'départs</li>\n'
                                                                 '            </ul>',
 ('data/audit_element_data.xml', 'element_6', 'description'): 'MFA, IAM/SSO, révocation',
 ('data/audit_element_data.xml', 'element_6', 'name'): "Contrôles d'accès (MFA)",
 ('data/audit_element_data.xml', 'element_7', 'best_practices'): '<ul>\n'
                                                                 '                <li>Correctifs '
                                                                 'de sécurité appliqués dans les '
                                                                 '30 jours</li>\n'
                                                                 '                <li>Solution '
                                                                 'EDR/antivirus sur tous les '
                                                                 'postes et serveurs</li>\n'
                                                                 '                <li>Durcissement '
                                                                 'des configurations (CIS '
                                                                 'Benchmarks)</li>\n'
                                                                 '                '
                                                                 '<li>Journalisation centralisée '
                                                                 'des événements de sécurité</li>\n'
                                                                 '                <li>Alertes '
                                                                 'automatiques pour les '
                                                                 'anomalies</li>\n'
                                                                 '                <li>Revue '
                                                                 'régulière des journaux '
                                                                 "d'accès</li>\n"
                                                                 '            </ul>',
 ('data/audit_element_data.xml', 'element_7', 'description'): 'Correctifs, EDR/AV, durcissement, '
                                                              'journalisation, alertes',
 ('data/audit_element_data.xml', 'element_7', 'name'): 'Hygiène & surveillance',
 ('data/audit_element_data.xml', 'element_8', 'best_practices'): '<ul>\n'
                                                                 '                <li>Pare-feu '
                                                                 'configuré avec règles '
                                                                 'restrictives</li>\n'
                                                                 '                <li>Segmentation '
                                                                 'réseau (VLAN) pour isoler les '
                                                                 'données sensibles</li>\n'
                                                                 '                <li>VPN pour les '
                                                                 'accès distants</li>\n'
                                                                 '                <li>Wi-Fi '
                                                                 'sécurisé (WPA3 ou '
                                                                 'WPA2-Enterprise)</li>\n'
                                                                 '            </ul>',
 ('data/audit_element_data.xml', 'element_8', 'description'): 'Segmentation, pare-feu, VPN',
 ('data/audit_element_data.xml', 'element_8', 'name'): 'Réseau',
 ('data/audit_element_data.xml', 'element_9', 'best_practices'): '<ul>\n'
                                                                 '                <li>Contrôle '
                                                                 "d'accès physique aux salles "
                                                                 'serveurs</li>\n'
                                                                 '                <li>Procédure de '
                                                                 'destruction sécurisée des '
                                                                 'supports de données</li>\n'
                                                                 '                <li>Registre des '
                                                                 'accès physiques</li>\n'
                                                                 '                <li>Protection '
                                                                 'contre les sinistres (incendie, '
                                                                 'inondation)</li>\n'
                                                                 '            </ul>',
 ('data/audit_element_data.xml', 'element_9', 'description'): 'Contrôle accès, destruction '
                                                              'supports',
 ('data/audit_element_data.xml', 'element_9', 'name'): 'Sécurité physique',
 ('data/mail_activity_type_data.xml', 'mail_activity_type_audit_watchpoint', 'name'): 'Point de '
                                                                                      'vigilance '
                                                                                      'Audit TI',
 ('data/mail_activity_type_data.xml', 'mail_activity_type_audit_watchpoint', 'summary'): 'Suivi '
                                                                                         "d'un "
                                                                                         'point de '
                                                                                         'vigilance'}
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
