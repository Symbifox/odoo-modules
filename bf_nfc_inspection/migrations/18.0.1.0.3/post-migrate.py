"""18.0.1.0.3, étape `post` : protéger les retouches AVANT le catalogue.

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

MODULE = "bf_nfc_inspection"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.0.3
LIVRES_FR = {('data/bf_nfc_inspection_gabarits.xml', 'gabarit_alarme', 'description'): 'Une pastille sur le '
                                                                           'panneau : le relevé '
                                                                           'quotidien note '
                                                                           "l'alimentation et "
                                                                           "l'absence de trouble. "
                                                                           'Le guet signale un '
                                                                           'jour sans relevé.',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_alarme', 'exemple'): "Panneau d'alarme ; local "
                                                                       'technique',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_alarme', 'name'): "Panneau d'alarme incendie : "
                                                                    'vérification quotidienne',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_alarme', 'reference'): 'Code de sécurité du '
                                                                         'Québec, chapitre '
                                                                         'Bâtiment (CNPI, 6.3.1.2)',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_fermeture_bureau', 'description'): 'Une pastille '
                                                                                     'près de la '
                                                                                     'sortie. La '
                                                                                     'dernière '
                                                                                     'personne à '
                                                                                     'partir la '
                                                                                     'tape : '
                                                                                     'portes, '
                                                                                     'fenêtres, '
                                                                                     'alarme, '
                                                                                     'appareils, '
                                                                                     'lumières. Ce '
                                                                                     'qui manque '
                                                                                     'va à la '
                                                                                     'personne '
                                                                                     'responsable '
                                                                                     'dès le soir '
                                                                                     'même.',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_fermeture_bureau', 'exemple'): 'Fermeture ; '
                                                                                 'porte principale',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_fermeture_bureau', 'name'): 'Ronde de fermeture '
                                                                              'du bureau',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_premiers_secours', 'description'): 'Une pastille '
                                                                                     'sur chaque '
                                                                                     'trousse et '
                                                                                     'chaque '
                                                                                     'défibrillateur. '
                                                                                     'Le relevé '
                                                                                     'mensuel dit '
                                                                                     'si la '
                                                                                     'trousse est '
                                                                                     'complète et '
                                                                                     'si le '
                                                                                     'défibrillateur '
                                                                                     'est prêt ; '
                                                                                     'un produit '
                                                                                     'périmé '
                                                                                     'devient une '
                                                                                     'anomalie à '
                                                                                     'corriger.',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_premiers_secours', 'exemple'): 'Trousse ; '
                                                                                 'cuisine du 2e\n'
                                                                                 'Défibrillateur ; '
                                                                                 'accueil',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_premiers_secours', 'name'): 'Trousse de premiers '
                                                                              'secours et '
                                                                              'défibrillateur',
 ('data/bf_nfc_inspection_gabarits.xml', 'gabarit_premiers_secours', 'reference'): 'Règlement sur '
                                                                                   'les normes '
                                                                                   'minimales de '
                                                                                   'premiers '
                                                                                   'secours et de '
                                                                                   'premiers soins '
                                                                                   '(A-3.001, r. '
                                                                                   '10)',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_mensuelle', 'description'): 'Grille de '
                                                                                     'départ tirée '
                                                                                     "d'un résumé "
                                                                                     'de la norme '
                                                                                     'CSA Z614 par '
                                                                                     'un fabricant '
                                                                                     ': la norme '
                                                                                     'elle-même '
                                                                                     "n'a pas été "
                                                                                     'consultée. À '
                                                                                     'valider '
                                                                                     'avant usage.',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_mensuelle', 'name'): 'Aire de jeu '
                                                                              'extérieure : '
                                                                              'inspection '
                                                                              'détaillée mensuelle',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_mensuelle', 'reference'): 'Règlement sur '
                                                                                   'les services '
                                                                                   'de garde '
                                                                                   'éducatifs à '
                                                                                   "l'enfance "
                                                                                   '(S-4.1.1, r. '
                                                                                   '2, art. 40) et '
                                                                                   'CSA Z614',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_mensuelle_01', 'name'): 'Structures '
                                                                                 'solides et bien '
                                                                                 'ancrées',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_mensuelle_02', 'name'): 'Boulonnerie '
                                                                                 'serrée, '
                                                                                 'capuchons en '
                                                                                 'place',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_mensuelle_03', 'name'): 'Pièces mobiles '
                                                                                 'sans usure '
                                                                                 'excessive',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_mensuelle_04', 'name'): 'Épaisseur du '
                                                                                 'revêtement de '
                                                                                 'protection '
                                                                                 'suffisante',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_mensuelle_05', 'name'): 'Aucune ouverture '
                                                                                 'où une tête ou '
                                                                                 'un doigt se '
                                                                                 'coince',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_mensuelle_06', 'name'): 'Remarque',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_quotidienne', 'description'): 'Grille de '
                                                                                       'départ '
                                                                                       "tirée d'un "
                                                                                       'résumé de '
                                                                                       'la norme '
                                                                                       'CSA Z614 '
                                                                                       'par un '
                                                                                       'fabricant '
                                                                                       ': la norme '
                                                                                       'elle-même '
                                                                                       "n'a pas "
                                                                                       'été '
                                                                                       'consultée. '
                                                                                       'À valider '
                                                                                       'avant '
                                                                                       'usage.',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_quotidienne', 'name'): 'Aire de jeu '
                                                                                'extérieure : '
                                                                                'inspection '
                                                                                'visuelle',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_quotidienne', 'reference'): 'Règlement '
                                                                                     'sur les '
                                                                                     'services de '
                                                                                     'garde '
                                                                                     'éducatifs à '
                                                                                     "l'enfance "
                                                                                     '(S-4.1.1, r. '
                                                                                     '2, art. 40) '
                                                                                     'et CSA Z614',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_quotidienne_01', 'name'): 'Aucun débris '
                                                                                   'ni objet '
                                                                                   'dangereux',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_quotidienne_02', 'name'): 'Surface de '
                                                                                   'protection en '
                                                                                   'place, sans '
                                                                                   'creux',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_quotidienne_03', 'name'): 'Aucune pièce '
                                                                                   'brisée, lâche '
                                                                                   'ou manquante',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_quotidienne_04', 'name'): 'Aucune arête '
                                                                                   'vive ni point '
                                                                                   'de coincement '
                                                                                   'apparent',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_quotidienne_05', 'name'): 'Clôture et '
                                                                                   'barrière en '
                                                                                   'bon état',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_aire_jeu_quotidienne_06', 'name'): 'Remarque',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_alarme', 'name'): "Panneau d'alarme incendie : "
                                                                  'vérification quotidienne',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_alarme', 'reference'): 'Code de sécurité du '
                                                                       'Québec, chapitre Bâtiment '
                                                                       '(CNPI, 6.3.1.2 et '
                                                                       'CAN/ULC-S536)',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_alarme_01', 'name'): "Voyant d'alimentation allumé",
 ('data/bf_nfc_inspection_grilles.xml', 'grille_alarme_02', 'name'): 'Aucun voyant de trouble ni '
                                                                     "d'alarme",
 ('data/bf_nfc_inspection_grilles.xml', 'grille_alarme_03', 'name'): 'Remarque',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_eclairage', 'description'): "L'essai de durée "
                                                                            'annuel se consigne à '
                                                                            'part.',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_eclairage', 'name'): 'Éclairage de sécurité '
                                                                     'autonome : essai mensuel',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_eclairage', 'reference'): 'Code de sécurité du '
                                                                          'Québec, chapitre '
                                                                          'Bâtiment (CNPI, '
                                                                          '6.5.1.6)',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_eclairage_01', 'name'): 'Voyant de charge allumé',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_eclairage_02', 'name'): "Les lampes s'allument au "
                                                                        "bouton d'essai",
 ('data/bf_nfc_inspection_grilles.xml', 'grille_eclairage_03', 'name'): 'Bornes et cosses propres',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_eclairage_04', 'name'): 'Batteries propres et '
                                                                        'sèches',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_eclairage_05', 'name'): 'Remarque',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur', 'description'): 'La vérification '
                                                                             'mensuelle par le '
                                                                             'propriétaire ou la '
                                                                             "personne qu'il "
                                                                             'désigne. '
                                                                             "L'inspection "
                                                                             'annuelle et '
                                                                             "l'entretien aux 5 ou "
                                                                             '6 ans restent '
                                                                             "l'affaire d'une "
                                                                             'entreprise '
                                                                             'spécialisée.',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur', 'name'): 'Extincteur portatif : '
                                                                      'vérification mensuelle',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur', 'reference'): 'Code de sécurité du '
                                                                           'Québec, chapitre '
                                                                           'Bâtiment (CNPI, '
                                                                           '6.2.1.1 et NFPA 10) ; '
                                                                           'fiche de vérification '
                                                                           'de la RBQ',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur_01', 'name'): 'Présent à son '
                                                                         'emplacement et '
                                                                         'accessible',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur_02', 'name'): 'Signalisation visible',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur_03', 'anomaly_options'): 'Non',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur_03', 'name'): 'Pression dans la zone '
                                                                         'normale',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur_03', 'options'): 'Oui;Non;Sans '
                                                                            'manomètre',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur_04', 'name'): 'Sceaux et goupille '
                                                                         'intacts',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur_05', 'name'): 'Aucun dommage ni '
                                                                         'corrosion',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur_06', 'name'): "Étiquette d'inspection "
                                                                         'annuelle à jour',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_extincteur_07', 'name'): 'Remarque',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_fermeture_bureau', 'description'): "Ce qu'on "
                                                                                   'vérifie en '
                                                                                   'partant le '
                                                                                   'dernier. '
                                                                                   'Aucune '
                                                                                   'obligation '
                                                                                   'derrière : une '
                                                                                   'habitude qui '
                                                                                   'se prouve.',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_fermeture_bureau', 'name'): 'Ronde de fermeture du '
                                                                            'bureau',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_fermeture_bureau_01', 'name'): 'Portes extérieures '
                                                                               'verrouillées',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_fermeture_bureau_02', 'name'): 'Fenêtres fermées',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_fermeture_bureau_03', 'name'): "Système d'alarme "
                                                                               'armé',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_fermeture_bureau_04', 'name'): 'Appareils de '
                                                                               'cuisine éteints',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_fermeture_bureau_05', 'name'): 'Lumières éteintes',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_fermeture_bureau_06', 'name'): 'Remarque',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_porte_coupe_feu', 'description'): 'Une porte à '
                                                                                  'fermeture '
                                                                                  'automatique '
                                                                                  'doit être '
                                                                                  'trouvée fermée '
                                                                                  ': rien ne doit '
                                                                                  'la retenir '
                                                                                  'ouverte.',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_porte_coupe_feu', 'name'): 'Porte coupe-feu : '
                                                                           'vérification '
                                                                           'quotidienne',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_porte_coupe_feu', 'reference'): 'Code de sécurité '
                                                                                'du Québec, '
                                                                                'chapitre Bâtiment '
                                                                                '(CNPI, 2.2.2.4) ; '
                                                                                'fiche quotidienne '
                                                                                'de la RBQ',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_porte_coupe_feu_01', 'name'): 'Porte fermée',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_porte_coupe_feu_02', 'name'): 'Rien ne la retient '
                                                                              'ouverte (cale, '
                                                                              'crochet, objet)',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_porte_coupe_feu_03', 'name'): 'Se referme et se '
                                                                              'verrouille seule',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_porte_coupe_feu_04', 'name'): 'Remarque',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours', 'description'): 'Le règlement '
                                                                                   'ne fixe pas de '
                                                                                   'fréquence : le '
                                                                                   'rythme mensuel '
                                                                                   'est un choix '
                                                                                   'de prudence, à '
                                                                                   'ajuster.',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours', 'name'): 'Trousse de premiers '
                                                                            'secours et '
                                                                            'défibrillateur : '
                                                                            'vérification '
                                                                            'mensuelle',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours', 'reference'): 'Règlement sur '
                                                                                 'les normes '
                                                                                 'minimales de '
                                                                                 'premiers secours '
                                                                                 'et de premiers '
                                                                                 'soins (A-3.001, '
                                                                                 'r. 10, art. 6) : '
                                                                                 'trousse tenue '
                                                                                 'propre, complète '
                                                                                 'et en bon état',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours_01', 'name'): 'Trousse complète',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours_02', 'name'): 'Aucun produit '
                                                                               'périmé',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours_03', 'anomaly_options'): 'Non',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours_03', 'name'): 'Défibrillateur : '
                                                                               'voyant prêt',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours_03', 'options'): 'Oui;Non;Pas de '
                                                                                  'défibrillateur',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours_04', 'anomaly_options'): 'Non',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours_04', 'name'): 'Électrodes et pile '
                                                                               'dans leur date',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours_04', 'options'): 'Oui;Non;Pas de '
                                                                                  'défibrillateur',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_premiers_secours_05', 'name'): 'Remarque',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_sortie', 'name'): 'Enseigne de sortie sur batterie '
                                                                  ': vérification mensuelle',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_sortie', 'reference'): 'Code de sécurité du '
                                                                       'Québec, chapitre Bâtiment '
                                                                       '(CNPI, 6.5.1.8)',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_sortie_01', 'name'): 'Enseigne allumée et lisible',
 ('data/bf_nfc_inspection_grilles.xml', 'grille_sortie_02', 'name'): "S'allume sur batterie à "
                                                                     "l'essai",
 ('data/bf_nfc_inspection_grilles.xml', 'grille_sortie_03', 'name'): 'Remarque'}
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
