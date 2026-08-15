#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Étalonne la table de largeurs de Lexend contre le moteur de rendu.

**Ce script ne tourne pas sur le serveur.** Il se lance à la main, hors Odoo,
sur une machine qui a PyMuPDF et les fichiers de police — c'est-à-dire là où le
PDF de référence est produit. Il écrit `generateur/lexend_metriques.py`, qui
lui n'a besoin de rien d'autre que la bibliothèque standard.

Pourquoi une table plutôt que la police : mesurer du texte demande un moteur
typographique, l'image Odoo n'en a pas, et en ajouter un pour deux mesures
ferait dépendre un module publié d'une bibliothèque binaire. La largeur d'un
caractère, elle, ne change pas. On la mesure une fois et on la fige.

Ce qui est figé, exactement : `Font.glyph_advance(cp)`, la largeur d'avance du
glyphe rapportée au corps de la police. PyMuPDF calcule
`text_length(s, corps) == sum(glyph_advance(c) for c in s) * corps`, et c'est
cette égalité que `mesure.text_length` reproduit — pas une approximation, la
même somme sur les mêmes nombres.

Deux tables en sortent :

* `AVANCES` — les caractères que Lexend porte vraiment, lus dans sa table de
  glyphes.
* `SUBSTITUTS` — les caractères que Lexend n'a PAS et que PyMuPDF résout par
  sa propre chaîne de polices de repli. L'espace fine insécable en fait partie,
  et du texte français en contient. Ces largeurs-là dépendent de la version de
  PyMuPDF : elles sont datées et versionnées dans le fichier produit, et c'est
  volontaire — un relevé, pas une vérité de la police.

Usage :
    python3 tools/etalonner_lexend.py [dossier_des_polices] [fichier_de_sortie]
"""
import sys
from datetime import date
from pathlib import Path

import pymupdf

# Les blocs où du texte d'affaires français ou anglais peut réellement piocher.
# Au-delà, `mesure` refuse et le dit : deviner une largeur serait pire.
BLOCS = [
    (0x0020, 0x024F),   # latin de base, supplément, étendu A et B
    (0x02B0, 0x02FF),   # lettres modificatives
    (0x0300, 0x036F),   # diacritiques combinants
    (0x1E00, 0x1EFF),   # latin étendu additionnel
    (0x2000, 0x206F),   # ponctuation générale : espaces, tirets, guillemets
    (0x20A0, 0x20BF),   # symboles monétaires
    (0x2100, 0x214F),   # symboles de type lettre : №, ™, ℃
    (0x2190, 0x21FF),   # flèches
    (0x2200, 0x22FF),   # opérateurs mathématiques
    (0x25A0, 0x25FF),   # formes géométriques
    (0x2610, 0x261F),   # cases à cocher et mains
    (0xFB00, 0xFB06),   # ligatures
]

POLICES = [("regulier", "Lexend-Regular.ttf"), ("gras", "Lexend-SemiBold.ttf")]


def _relever(chemin):
    """Avances de la police, et avances de repli hors de la police."""
    police = pymupdf.Font(fontfile=str(chemin))
    connus = set(police.valid_codepoints())
    propres, substituts = {}, {}
    for cp in sorted(connus):
        propres[cp] = police.glyph_advance(cp)
    for debut, fin in BLOCS:
        for cp in range(debut, fin + 1):
            if cp in connus:
                continue
            substituts[cp] = police.glyph_advance(cp)
    return propres, substituts


def _grouper(avances):
    """Regroupe par largeur : 685 caractères tiennent en 186 lignes.

    L'inverse d'un dict caractère → largeur, parce que les largeurs se
    répètent beaucoup et qu'une table lisible se relit.
    """
    par_largeur = {}
    for cp, a in avances.items():
        par_largeur.setdefault(a, []).append(cp)
    return sorted(par_largeur.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def _echapper(cps):
    """Les codes en une chaîne littérale, lisible quand elle peut l'être."""
    out = []
    for cp in sorted(cps):
        c = chr(cp)
        if cp < 0x20 or cp == 0x7F or c in ('"', "\\") or not c.isprintable():
            out.append("\\u%04x" % cp)
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def _bloc(nom, avances):
    lignes = [f"{nom} = {{"]
    for largeur, cps in _grouper(avances):
        lignes.append(f"    {largeur!r}: {_echapper(cps)},")
    lignes.append("}")
    return "\n".join(lignes)


def main(dossier, sortie):
    morceaux = []
    for cle, fichier in POLICES:
        propres, substituts = _relever(Path(dossier) / fichier)
        morceaux.append((cle, fichier, propres, substituts))

    version = pymupdf.version[0]
    entete = f'''# -*- coding: utf-8 -*-
"""Largeurs de Lexend, relevées une fois pour toutes. FICHIER PRODUIT.

Ne pas modifier à la main : régénérer avec `tools/etalonner_lexend.py`, hors
serveur, là où PyMuPDF et les fichiers de police sont disponibles.

Chaque table va d'une largeur d'avance à la suite des caractères qui la
partagent — les largeurs se répètent beaucoup, et une table groupée se relit.
`mesure` la retourne en caractère → largeur au chargement.

`SUB_*` relève les caractères que Lexend ne porte pas et que le moteur de rendu
résout par une police de repli — l'espace fine insécable d'abord, que la
typographie française sème partout. Ces largeurs dépendent du moteur, pas de
Lexend : elles sont datées, et c'est ce qui permet de les revoir.

Relevé le {date.today().isoformat()}, PyMuPDF {version}.
"""
'''
    corps = [entete]
    for cle, fichier, propres, substituts in morceaux:
        corps.append(f"# --- {fichier} " + "-" * (66 - len(fichier)))
        corps.append(_bloc(f"AVANCES_{cle.upper()}", propres))
        corps.append("")
        corps.append(_bloc(f"SUB_{cle.upper()}", substituts))
        corps.append("")
    Path(sortie).write_text("\n".join(corps), encoding="utf-8")

    for cle, fichier, propres, substituts in morceaux:
        print(f"{fichier}: {len(propres)} caractères, "
              f"{len(set(propres.values()))} largeurs distinctes, "
              f"{len(substituts)} substituts")
    print("écrit :", sortie)


if __name__ == "__main__":
    racine = Path(__file__).resolve().parent.parent
    main(sys.argv[1] if len(sys.argv) > 1 else racine / "static" / "fonts",
         sys.argv[2] if len(sys.argv) > 2 else racine / "generateur" / "lexend_metriques.py")
