# -*- coding: utf-8 -*-
"""Les contrôles déterministes de la QA éditoriale.

Aucune intelligence artificielle n'intervient ici : ce sont des expressions
régulières et des comptages. C'est délibéré — ces contrôles doivent tourner
même sans fournisseur configuré, et ils doivent rendre le même verdict deux
fois de suite.

Les règles sont paramétrables par ``ir.config_parameter`` pour qu'un locataire
puisse durcir ou assouplir sans redéployer.
"""

import re

from odoo import _, api, models

from .version import IGNORED_SLOTS, text_from_html

# --- formules bannies -------------------------------------------------------
# Les variantes comptent autant que la forme canonique : c'est en variante
# qu'elles reviennent.
BANNED = {
    "fr_": [
        "on va se le dire", "soyons honnêtes", "soyons honnete",
        "il faut être honnête", "il faut etre honnete",
        "la bonne nouvelle", "avouons-le", "force est de constater",
        "il faut le dire", "soyons clairs", "ce que ça veut dire pour vous",
        "point d'honnêteté",
    ],
    "en_": [
        "let's be honest", "lets be honest", "let's face it", "lets face it",
        "to be honest", "have to be honest", "the good news",
        "the bottom line",
    ],
}

EMDASH = "—"

MARKERS = ("<!-- IMAGE", "<!-- FACT-CHECK", "<!-- NE PAS PUBLIER", "<!-- TODO")

# Le bleu de marque échoue le contraste AA sur fond blanc. Posé en ligne dans
# le contenu, il échappe à toute correction de feuille de style.
LOW_CONTRAST = ("#27aae2", "#29abe2", "#27AAE2", "#29ABE2")

_H2_EMPTY_RE = re.compile(r"<h2[^>]*>\s*(<br\s*/?>)?\s*</h2>", re.I)
# ⚠️ Limite de mot après « th » impérative : sans elle, le motif prend
# « <thead> » pour un « <th> » sans portée — « th » en est un préfixe littéral,
# et une balise <thead> par tableau, dans le gabarit maison, gonflait le
# constat de deux faux positifs par article dès qu'un tableau existait.
# ⚠️ Limite de mot après « th » impérative : sans elle, le motif prend
# « <thead> » pour un « <th> » sans portée — « th » en est un préfixe littéral,
# et une balise <thead> par tableau, dans le gabarit maison, gonflait le
# constat de deux faux positifs par article dès qu'un tableau existait.
_TH_RE = re.compile(r"<th\b(?![^>]*\bscope=)[^>]*>", re.I)
_IMG_NO_ALT_RE = re.compile(r"<img(?![^>]*\balt=)[^>]*>", re.I)
_ROOT_TAG_RE = re.compile(r"<(h[1-6]|p|ul|ol|table|div|blockquote)\b", re.I)
_H2_RE = re.compile(r"<h2\b", re.I)
_HREF_RE = re.compile(r'href="(/[^"]*)"', re.I)
# Espace fine française avant un deux-points, à ne pas laisser passer côté EN.
_FR_THIN_COLON_RE = re.compile(r"</a>\s+:\s")


class EditorialQA(models.AbstractModel):
    _name = "bf.editorial.qa"
    _description = "Contrôles éditoriaux déterministes"

    # --- point d'entrée ---------------------------------------------------
    @api.model
    def run(self, entry):
        """Rendre la liste des constats. Liste vide = rien à signaler."""
        findings = []
        versions = entry.version_ids.filtered(
            lambda v: v.lang_code not in IGNORED_SLOTS
        )
        if not versions and not entry.post_id:
            return [_("Aucun créneau de langue à contrôler.")]

        per_version = {}
        for version in versions:
            content = version._post_content()
            single = self._check_content(content, version.lang_code)
            per_version[version.id] = (version, content, single)
            version.write({
                "qa_findings": "\n".join(single) if single else False,
                "qa_state": "findings" if single else "clean",
            })
            findings += ["[%s] %s" % (version.lang_id.name, f) for f in single]

        findings += self._check_across(per_version)
        return findings

    # --- contrôles sur un créneau ----------------------------------------
    @api.model
    def _check_content(self, content, lang_code):
        """Les contrôles qui ne regardent qu'une langue."""
        findings = []
        if not content:
            return [_("Créneau vide.")]

        lowered = content.lower()
        text = text_from_html(content)

        if EMDASH in content:
            findings.append(_(
                "%s tiret(s) cadratin. À remplacer par deux-points, virgule,"
                " parenthèses ou point.", content.count(EMDASH),
            ))

        for prefix, phrases in BANNED.items():
            if not (lang_code or "").startswith(prefix):
                continue
            for phrase in phrases:
                if phrase in lowered:
                    findings.append(_("Formule bannie : « %s ».", phrase))

        empty = len(_H2_EMPTY_RE.findall(content))
        if empty:
            findings.append(_("%s titre(s) H2 vide(s).", empty))

        no_scope = len(_TH_RE.findall(content))
        if no_scope:
            findings.append(_(
                "%s en-tête(s) de tableau sans attribut « scope ».", no_scope,
            ))

        no_alt = len(_IMG_NO_ALT_RE.findall(content))
        if no_alt:
            findings.append(_("%s image(s) sans texte alternatif.", no_alt))

        for color in LOW_CONTRAST:
            if color.lower() in lowered:
                findings.append(_(
                    "Couleur en ligne à faible contraste : %s.", color,
                ))
                break

        for marker in MARKERS:
            if marker.lower() in lowered:
                findings.append(_("Marqueur de rédaction resté : %s.", marker))

        if (lang_code or "").startswith("en_") and _FR_THIN_COLON_RE.search(content):
            findings.append(_(
                "Ponctuation française (espace avant deux-points) dans un"
                " créneau anglais."
            ))

        return findings

    # --- contrôles entre créneaux ----------------------------------------
    @api.model
    def _check_across(self, per_version):
        """Comparer les créneaux entre eux.

        Un écart de structure entre deux langues signale une traduction
        partielle bien avant qu'une relecture ne le voie.
        """
        findings = []
        entries = [
            (version, content)
            for version, content, _f in per_version.values()
            if content
        ]
        if len(entries) < 2:
            return findings

        source = next(
            (v for v, _c in entries if v.is_source), entries[0][0],
        )
        source_content = dict(
            (v.id, c) for v, c in entries
        ).get(source.id, "")
        source_roots = len(_ROOT_TAG_RE.findall(source_content))
        source_h2 = len(_H2_RE.findall(source_content))

        source_slugs = set()
        if source.slug:
            source_slugs.add(source.slug)

        for version, content in entries:
            if version == source:
                continue
            roots = len(_ROOT_TAG_RE.findall(content))
            h2 = len(_H2_RE.findall(content))
            if roots != source_roots:
                findings.append(_(
                    "[%(lang)s] Structure différente de la source :"
                    " %(a)s éléments contre %(b)s. Traduction probablement"
                    " partielle.",
                    lang=version.lang_id.name, a=roots, b=source_roots,
                ))
            if h2 != source_h2:
                findings.append(_(
                    "[%(lang)s] %(a)s titres H2 contre %(b)s dans la source.",
                    lang=version.lang_id.name, a=h2, b=source_h2,
                ))

            # Un texte traduit qui pointe encore les slugs de la source
            # renvoie le lecteur dans l'autre langue.
            if version.lang_code and version.lang_code.startswith("en_"):
                for href in _HREF_RE.findall(content):
                    if "articles-de-blue-fox" in href:
                        findings.append(_(
                            "[%(lang)s] Lien interne vers un slug français :"
                            " %(href)s",
                            lang=version.lang_id.name, href=href,
                        ))
                        break
        return findings
