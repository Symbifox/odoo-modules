"""Les portées du verrou de Gen déclarées par Healthy Fox.

Un simple attribut de classe sur chaque modèle (``_gen_scope``), lu par le
verrou générique de ``bf_claude_chat`` s'il est installé : Healthy Fox ne
dépend pas de Gen. Sans Gen, ces attributs ne font rien.

Trois portées, fermées par défaut :

* ``bf_health`` : la santé (médicaments, signes vitaux, analyses, examens,
  conditions, symptômes, consommation, réduction, entraînement, alimentation) ;
* ``bf_health.mood`` : le journal d'humeur ;
* ``bf_health.saisie`` : les assistants de saisie, qui mêlent les deux avant
  enregistrement. Aucun écran n'offre d'y consentir : Gen ne les lit jamais.
"""
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

PORTEE_SANTE = "bf_health"
LIBELLE_SANTE = _lt("Healthy Fox : données de santé")

PORTEE_HUMEUR = "bf_health.mood"
LIBELLE_HUMEUR = _lt("Healthy Fox : journal d'humeur")

PORTEE_SAISIE = "bf_health.saisie"
LIBELLE_SAISIE = _lt("Healthy Fox : saisies en cours (jamais ouvertes à Gen)")
