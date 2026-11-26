"""18.0.1.17.5, étape `post` : protéger les retouches AVANT le catalogue.

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
