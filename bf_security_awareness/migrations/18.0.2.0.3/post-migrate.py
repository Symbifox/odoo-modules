"""Les corps de courriel des leurres écrits avant le nettoyage à l'écriture sont
nettoyés une fois, à la montée. Pages d'atterrissage non touchées.
"""
import json
import logging

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, html_normalize, html_sanitize

_logger = logging.getLogger(__name__)


def _nettoyer(valeur, champ):
    """(nettoyé, retiré ?) — « retiré » seulement si le nettoyage enlève quelque chose,
    pas pour une simple remise en forme du HTML."""
    options = {
        "silent": True,
        "sanitize_tags": champ.sanitize_tags,
        "sanitize_attributes": champ.sanitize_attributes,
        "sanitize_style": champ.sanitize_style,
        "sanitize_form": champ.sanitize_form,
        "sanitize_conditional_comments": champ.sanitize_conditional_comments,
        "output_method": champ.sanitize_output_method,
        "strip_style": champ.strip_style,
        "strip_classes": champ.strip_classes,
    }
    propre = html_sanitize(valeur, **options)
    normal = html_normalize(valeur, output_method=champ.sanitize_output_method)
    return propre, propre != normal


def nettoyer_les_corps(cr, cibles):
    """Nettoie à la montée les corps HTML écrits avant que leur champ soit nettoyé.

    Écrit en SQL (ni suivi, ni courriel, ni date de modification), une fiche seulement
    si le nettoyage y retire quelque chose, et journalise chaque fiche touchée.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    for modele, nom in cibles:
        Modele = env[modele]
        champ = Modele._fields[nom]
        table, colonne = Modele._table, nom
        cr.execute(SQL("SELECT id, %s FROM %s WHERE %s IS NOT NULL",
                       SQL.identifier(colonne), SQL.identifier(table), SQL.identifier(colonne)))
        touchees = 0
        for rid, brut in cr.fetchall():
            if champ.translate:
                valeurs = brut if isinstance(brut, dict) else json.loads(brut)
                neuves, retire = {}, False
                for langue, v in valeurs.items():
                    neuves[langue], r = _nettoyer(v, champ) if v else (v, False)
                    retire = retire or r
                if not retire:
                    continue
                cr.execute(SQL("UPDATE %s SET %s = %s::jsonb WHERE id = %s", SQL.identifier(table),
                               SQL.identifier(colonne), json.dumps(neuves), rid))
            else:
                neuve, retire = _nettoyer(brut, champ)
                if not retire:
                    continue
                cr.execute(SQL("UPDATE %s SET %s = %s WHERE id = %s", SQL.identifier(table),
                               SQL.identifier(colonne), neuve, rid))
            touchees += 1
            _logger.warning("Corps nettoyé à la montée, modèle=%s id=%s champ=%s",
                            modele, rid, nom)
        _logger.info("%s fiche(s) %s nettoyée(s) (%s)", touchees, modele, nom)


def migrate(cr, version):
    nettoyer_les_corps(cr, [("bf.phishing.template", "email_body")])
