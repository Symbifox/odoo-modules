"""Couper l'historique cité d'un message avant d'y lire une signature.

🔴 **Ce que ça a coûté .** Le 2026-11-11, sur une base réelle, deux fiches sur 23 ont reçu la signature du propriétaire de la
boîte — titre, mobile, site, société — parce que l'expéditeur n'avait pas signé
sa réponse : la SEULE signature du texte envoyé au pont était celle citée en
historique, et le modèle a rendu celle qu'il voyait, avec 88 et 90 de
confiance. Le prompt disait déjà « IGNORE quoted/forwarded history » ; une
consigne ne peut pas sauver un texte dont on a soi-même laissé entrer le piège.
Exposition mesurée sur le même corpus : 39 des 528 expéditeurs (7,4 %) n'ont
QUE des réponses et portent la signature d'un tiers dans leur citation.

⚠️ **On ne coupe pas AU premier ``<blockquote>``.** Une réponse Outlook met
parfois la signature de l'expéditeur SOUS la citation. Chaque bloc cité est
donc retiré de l'arbre (``drop_tree``, qui garde le texte de queue), et ce qui
l'entoure — avant comme après — reste.

Deux étages, parce que les deux formes existent dans ``bf.email`` : le HTML
(``body_html``, le cas courant) et le texte brut (``body_preview``, ou le HTML
déjà aplati). Le HTML se coupe par structure, le texte par marqueurs.

⚠️ **Le texte brut, lui, se coupe jusqu'à la fin.** En texte, l'historique cité
court jusqu'au bas du message : rien ne permet de distinguer une signature
posée sous la citation d'une ligne de cette citation. Le cas Outlook
« signature sous la citation » est donc rattrapé par l'étage HTML, qui est la
forme qu'Outlook envoie.

Sans Odoo (``lxml`` seulement, comme le reste de ``tools/``) : les essais de
``test_citation.py`` tournent sans base.
"""
import re

from lxml import etree
from lxml import html as lhtml

#: Les conteneurs que les clients de messagerie posent autour de l'historique.
#: Un seul XPath par forme, pour ne dépendre ni de ``cssselect`` ni de l'ordre.
_BLOCS_CITES = (
    # Apple Mail, Odoo, la plupart des clients : la citation est un blockquote.
    "//blockquote",
    # ⛔ PAS `//*[@data-o-mail-quote]`. 🔴 `odoo.tools.mail.tag_quote` — qui
    # tourne sur tout champ `fields.Html`, donc sur `mail.message.body`, donc
    # sur les 19 % de lignes `bf.email` nées du chatter ou de la passerelle —
    # pose cet attribut sur les SIGNATURES autant que sur les citations :
    # `el_id == "Signature"` (Outlook), `gmail_signature`,
    # `odoo_signature_wrapper`, le motif `-- <br/>`, et tous les frères qui
    # suivent. Mesuré sur un corpus réel : 149 des 212 lignes marquées
    # perdaient du texte hors de toute citation, et 143 des 201 lignes de
    # passerelle perdaient un numéro de téléphone que la version d'avant
    # gardait. C'était une RÉGRESSION, pas une garde. Les conteneurs réels
    # qu'Odoo marque sont déjà couverts par les sélecteurs ci-dessous : le
    # retrait de celui-ci ne laisse passer aucune citation de plus.
    # Gmail.
    "//div[contains(concat(' ', normalize-space(@class), ' '), ' gmail_quote ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '),"
    " ' gmail_quote_container ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '), ' gmail_extra ')]",
    # (Outlook et Thunderbird : voir `_ENTETES_DE_REPONSE`, plus bas.)
    # Yahoo, Proton, Zimbra.
    "//div[contains(concat(' ', normalize-space(@class), ' '), ' yahoo_quoted ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '),"
    " ' protonmail_quote ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '), ' zmail_extra ')]",
)

#: Les nœuds qui ANNONCENT une réponse citée sans la contenir : l'en-tête
#: « De / Envoyé / À / Objet » d'Outlook, le « Le … a écrit : » de
#: Thunderbird, le trait d'Outlook. 🔴 Les retirer seuls ne servait à rien : le
#: corps cité est leur FRÈRE, il restait, et l'étage texte n'avait plus le
#: moindre marqueur à voir puisqu'on venait de l'effacer. Ces nœuds emportent
#: donc tout ce qui les suit dans leur parent — c'est ce que fait Odoo lui-même
#: pour le trait d'Outlook.
#: ⚠️ `appendonsend`, pas `appendonly` : c'est l'identifiant que pose Outlook
#: (et que cherche `odoo.tools.mail.tag_quote`). Mesuré sur un corpus réel :
#: `appendonly` apparaît 0 fois, `appendonsend` 25 fois.
_ENTETES_DE_REPONSE = (
    "//div[@id='divRplyFwdMsg']",
    "//div[@id='appendonsend']",
    "//div[@id='x_appendonsend']",
    "//div[@id='mail-editor-reference-message-container']",
    "//div[contains(concat(' ', normalize-space(@class), ' '),"
    " ' OutlookMessageHeader ')]",
    "//div[contains(concat(' ', normalize-space(@class), ' '), ' moz-cite-prefix ')]",
    "//hr[@id='stopSpelling']",
)

#: Marqueurs qui ouvrent l'historique cité en texte brut, sans ambiguïté.
_MARQUEUR_CITATION = re.compile(
    r"""^\s*(?:
        -{2,}\s*(?:message\s+d['’]origine|message\s+original|original\s+message
                  |forwarded\s+message|message\s+transf[eé]r[eé])\s*-{2,}
      | (?:d[eé]but\s+du\s+message\s+transf[eé]r[eé]|begin\s+forwarded\s+message)\s*:
      | le\b.{0,200}?\ba\s+[eé]crit\s*:
      | on\b.{0,200}?\bwrote\s*:
      | [eé]crit\s*:\s*$
      | wrote\s*:\s*$
    )""",
    re.IGNORECASE | re.VERBOSE,
)

#: Lignes d'en-tête d'un message cité. Une SEULE ne prouve rien (« Objet : » se
#: voit dans un corps ordinaire).
#: ⚠️ `html2plaintext` rend `<b>De :</b>` en « *De :* » — c'est la forme
#: d'Outlook bureau, qui n'a ni identifiant ni classe reconnaissable. Sans
#: tolérer ces astérisques, la citation passait entière : mesuré sur un corpus
#: réel, les tolérer coupe neuf citations de plus et n'en perd aucune.
_ENTETE_CITEE = re.compile(
    r"^\s*[*_]{0,2}\s*(?:de|from|exp[eé]diteur|envoy[eé](?:\s+le)?|sent|date"
    r"|[aà]|to|cc|copie|objet|sujet|subject|r[eé]pondre\s+[aà]|reply-?to)"
    r"\s*[*_]{0,2}\s*:",
    re.IGNORECASE,
)

#: ⚠️ L'en-tête qui DÉSIGNE une citation : celui qui nomme l'expéditeur. Sans
#: cette exigence, un corps légitime qui aligne « Date : … » puis « Objet : … »
#: — un accusé de formulaire, un ordre du jour — se faisait couper, et c'est la
#: signature de l'expéditeur qu'on perdait. Deux lignes d'en-tête de suite ne
#: prouvent rien ; deux dont une nomme l'expéditeur, oui.
_ENTETE_EXPEDITEUR = re.compile(
    r"^\s*[*_]{0,2}\s*(?:de|from|exp[eé]diteur)\s*[*_]{0,2}\s*:",
    re.IGNORECASE)

#: Combien de lignes d'en-tête consécutives ouvrent la citation.
_ENTETES_POUR_COUPER = 2

#: ⚠️ `html2plaintext` recolle parfois l'en-tête d'une citation à la fin de la
#: ligne précédente (webmails qui citent par simple indentation, sans
#: `blockquote` ni classe connue) : « Merci. De : Jean Proprio ». La ligne est
#: alors recoupée devant l'en-tête, pour que la règle ci-dessus s'applique.
#: ⚠️ Et seulement devant ce qui RESSEMBLE à un expéditeur : un nom suivi
#: d'une adresse (`<`, `@`). Sans cette exigence, « La facture vient de : Acme »
#: ou « Je vous reviens. De : mon côté tout est prêt » devenait une ligne
#: d'en-tête, la ligne suivante complétait la paire, et tout le bas du message
#: — signature comprise — était coupé. Mesuré : zéro ligne du corpus réel
#: perdait quoi que ce soit par là, mais le mode de panne, lui, était total.
_ENTETE_EN_LIGNE = re.compile(
    r"(?<=[\.\!\?:;\s])"
    r"(?=[*_]{0,2}\s*(?:de|from|exp[eé]diteur|envoy[eé]|sent)\s*[*_]{0,2}\s*:"
    r"[^\n]{0,160}[<@])",
    re.IGNORECASE)

_LIGNE_CITEE = re.compile(r"^\s*>")
_TROIS_VIDES = re.compile(r"\n{3,}")


def sans_citation_html(body_html):
    """Le HTML d'un message, ses blocs cités retirés.

    Rend ``""`` si le fragment est vide ou illisible. ``drop_tree`` garde le
    texte de queue du bloc retiré : ce qui SUIT la citation survit, et c'est
    tout l'intérêt (signature d'Outlook posée sous l'historique).
    """
    if not body_html or not body_html.strip():
        return ""
    try:
        racine = lhtml.fromstring(body_html)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return body_html
    # 🔴 Le document, pas l'élément de tête. `fromstring` sur un fragment qui
    # n'a qu'UN élément de tête rend CET élément ; `drop_tree` le détache de
    # son `<body>` de synthèse, et sérialiser le même objet rendait la citation
    # ENTIÈRE, enfants intacts. La garde A était donc inopérante sur la forme
    # exacte du défaut : une réponse sans texte propre, dont le seul élément de
    # tête est la citation. Et c'est la forme NORMALE de ce qui est stocké :
    # `extract_body` retire l'enveloppe `<body>` avant d'écrire `body_html`.
    arbre = racine.getroottree()
    for chemin in _BLOCS_CITES:
        for bloc in racine.xpath(chemin):
            if bloc.getparent() is not None:
                # `drop_tree` et non `remove` : le texte de QUEUE du bloc
                # survit, et c'est lui qui porte parfois la signature de
                # l'expéditeur posée juste après la citation.
                bloc.drop_tree()
    for chemin in _ENTETES_DE_REPONSE:
        for bloc in racine.xpath(chemin):
            parent = bloc.getparent()
            if parent is None:
                continue
            # Les frères qui suivent SONT le corps cité : `remove` emporte
            # aussi leur texte de queue, ce que `drop_tree` garderait.
            for frere in list(bloc.itersiblings()):
                parent.remove(frere)
            bloc.drop_tree()
    try:
        return lhtml.tostring(arbre.getroot(), encoding="unicode")
    except (ValueError, TypeError):  # pragma: no cover — défensif
        return body_html


def sans_citation_texte(texte):
    """Le texte d'un message, son historique cité retiré.

    Les lignes préfixées ``>`` sautent une à une ; un marqueur d'historique ou
    deux lignes d'en-tête de suite coupent tout ce qui suit.
    """
    if not texte:
        return ""
    lignes = []
    for brute in texte.splitlines():
        # Une ligne qui porte un en-tête de citation ailleurs qu'au début est
        # recoupée devant lui : sinon l'en-tête est invisible à la règle.
        morceaux = _ENTETE_EN_LIGNE.split(brute, maxsplit=1)
        lignes.extend(m.rstrip() for m in morceaux)
    gardees = []
    i = 0
    total = len(lignes)
    while i < total:
        ligne = lignes[i]
        if _MARQUEUR_CITATION.match(ligne):
            break
        if _ENTETE_CITEE.match(ligne):
            suite = i
            nomme_l_expediteur = False
            while suite < total and _ENTETE_CITEE.match(lignes[suite]):
                nomme_l_expediteur = (nomme_l_expediteur
                                      or bool(_ENTETE_EXPEDITEUR.match(lignes[suite])))
                suite += 1
            if suite - i >= _ENTETES_POUR_COUPER and nomme_l_expediteur:
                break
        if _LIGNE_CITEE.match(ligne):
            i += 1
            continue
        gardees.append(ligne)
        i += 1
    return _TROIS_VIDES.sub("\n\n", "\n".join(gardees)).strip()
