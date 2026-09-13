# -*- coding: utf-8 -*-
"""Le contrat entre une source de données et les deux rendus.

Une source (un module satellite) ne dessine rien et ne calcule aucune
coordonnée : elle rend une `Carte`, qui est une liste de boîtes et une liste
d'arêtes. `disposition` en fait un `Plan` avec des coordonnées, et les deux
rendus se contentent de tracer ce plan.

Une seule géométrie, deux sorties. C'est la règle que `bf_process` s'est déjà
donnée pour ses quatre rendus, et c'est ce qui garantit que le PDF est
exactement ce que l'écran montrait.
"""
from dataclasses import dataclass, field


#: Teintes admises sur une boîte. Le rendu les traduit en couleurs; une teinte
#: inconnue retombe sur `neutre` plutôt que de lever, parce qu'une carte ne se
#: refuse pas pour une couleur.
TEINTES = ("neutre", "bleu", "ambre", "vert", "rouge")


@dataclass
class Boite:
    """Un nœud de l'organigramme.

    `cle` est l'identité stable de la boîte dans la carte (souvent
    `"res.partner,42"`). Les deux rendus s'en servent pour les ancres, et
    `disposition` pour rattacher les arêtes.
    """

    cle: str
    titre: str
    sous_titre: str = ""
    note: str = ""
    teinte: str = "neutre"
    #: Ce que l'interface doit ouvrir quand on clique la boîte, ou None.
    lien: str | None = None
    #: La boîte d'où l'on regarde. Sans elle, un lecteur ne sait pas laquelle
    #: des sociétés du dessin est celle dont il a ouvert la fiche.
    accent: bool = False

    def __post_init__(self):
        if self.teinte not in TEINTES:
            self.teinte = "neutre"


@dataclass
class Arete:
    """Un lien orienté entre deux boîtes, avec ce qu'il porte.

    `etiquette` est ce qui distingue une détention d'une hiérarchie : « 60 % »
    vit sur l'arête, jamais dans une boîte. C'est la raison pour laquelle la
    vue `hierarchy` d'Odoo ne peut pas dessiner une détention.
    """

    de: str
    vers: str
    etiquette: str = ""
    pointille: bool = False


@dataclass
class Carte:
    titre: str = ""
    sous_titre: str = ""
    boites: list = field(default_factory=list)
    aretes: list = field(default_factory=list)
    #: Paires (teinte, libellé) rendues en légende. Vide = pas de légende.
    legende: list = field(default_factory=list)
    #: Mention de bas de page (source, date d'extraction, avertissement).
    pied: str = ""

    def cles(self):
        return [b.cle for b in self.boites]

    def valider(self):
        """Rend la carte cohérente plutôt que de lever.

        Une arête qui pointe vers une boîte absente est écartée, et les
        doublons de clé sont fusionnés sur la première occurrence. Une carte
        arrive de données saisies à la main : elle se répare, elle ne casse
        pas l'écran de celui qui la regarde.
        """
        vues, boites = set(), []
        for b in self.boites:
            if b.cle in vues:
                continue
            vues.add(b.cle)
            boites.append(b)
        self.boites = boites
        self.aretes = [
            a for a in self.aretes
            if a.de in vues and a.vers in vues and a.de != a.vers
        ]
        return self
