"""18.0.3.4.0 vers 18.0.3.5.0 : la politique de conservation s'était vidée.

Mesuré sur des bases réelles : COR-BFMAIL-1 et COR-BFMAIL-2 y
portaient 0 an, et le cron de proposition était éteint, les trois écritures
datant de la même seconde. La cause vit dans l'écran de réglages, corrigé par
cette même version : les champs de façade sont des calculés nus, la page
Paramètres est bâtie par `onchange`, et la couche web sème à `False` tout champ
que `default_get` ne rend pas. Le formulaire affichait donc 0 quoi que porte le
calendrier, et un seul enregistrement, depuis n'importe quel onglet d'un autre
module, écrivait ce 0 sur les deux règles et éteignait le cron.

⚠️ Le fichier de données ne pouvait pas rattraper la valeur : il est
`noupdate="1"`, donc aucune montée ne réécrit un enregistrement déjà posé. Le
`noupdate` n'a pas créé le défaut, il l'a rendu définitif.

⚠️ On ne recharge pas le fichier par `convert_file(mode="init")`, la seule voie
qui traverse un `noupdate`. Ce serait écraser du même coup la durée qu'un
administrateur a délibérément posée, ce que le `noupdate` est là pour protéger.
On ne touche donc QUE la règle qui n'a jamais reçu de durée : celle dont le sort
final est la destruction et dont la durée totale vaut ZÉRO. Une règle réglée à
2 ans reste à 2 ans, quel que soit le plancher du jour, et une règle réglée sur
l'anonymisation ou la conservation permanente n'est pas touchée, son 0 ne
voulant pas dire la même chose.

⚠️ Écriture par l'ORM, jamais en SQL : `total_retention_days` est un calculé
STOCKÉ. Un `UPDATE` des années laisserait le total inchangé, et la règle
resterait inopérante sans un mot.

Le cron n'est PAS rallumé : son état est une décision d'exploitation, et une
file de destruction qui se remplit toute seule en est une aussi.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Ce que `data/privacy_retention_data.xml` déclare pour chaque règle.
DUREES_DECLAREES = {
    "bf_email_privacy.retention_bf_email_unfiled": 3,
    "bf_email_privacy.retention_bf_email_filed": 5,
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    plancher = env["bf.email"]._privacy_reconcile_floor_days()
    for xmlid, annees in DUREES_DECLAREES.items():
        regle = env.ref(xmlid, raise_if_not_found=False)
        if not regle:
            _logger.warning(
                "bf_email_privacy: règle %s introuvable, rien à rattraper", xmlid
            )
            continue
        if regle.final_disposition != "destroy":
            _logger.info(
                "bf_email_privacy: %s a pour sort final « %s », laissée telle "
                "quelle : sa durée ne pilote aucun balayage",
                regle.code, regle.final_disposition,
            )
            continue
        # ⚠️ La porte est le ZÉRO, pas le plancher. Avec un
        # `bf_email.reconcile_days` relevé, le plancher peut dépasser la durée
        # déclarée, et une porte « sous le plancher » écraserait alors une durée
        # délibérément choisie, ce que cette migration promet de ne pas faire.
        # Le zéro, lui, n'est atteignable que par le défaut qu'on répare :
        # l'écran s'exprime en années, donc toute valeur voulue est non nulle.
        if regle.total_retention_days != 0:
            _logger.info(
                "bf_email_privacy: %s porte %s j, laissée telle quelle : c'est "
                "un réglage, pas un défaut",
                regle.code, regle.total_retention_days,
            )
            continue
        avant = regle.total_retention_days
        regle.write({
            "active_retention_years": annees,
            "semi_active_retention_years": 0,
        })
        # ⚠️ Relire le résultat plutôt que de l'annoncer, et le relire par le
        # MÊME contrôle que l'écran et le cron : une durée peut repasser le
        # plancher alors que la règle reste inerte pour une autre raison, un
        # archivage par exemple. Le seul journal que personne ne relira ne doit
        # pas annoncer une réparation qui n'a pas eu lieu.
        reste = env["bf.email"]._privacy_rule_problem(regle, plancher)
        if reste:
            _logger.error(
                "bf_email_privacy: %s portée à %s j, mais le régime reste hors "
                "d'état : %s. Rien ne sera proposé tant que ce motif tient.",
                regle.code, regle.total_retention_days, reste,
            )
            continue
        _logger.warning(
            "bf_email_privacy: %s était INOPÉRANTE (%s j, sous le plancher de "
            "%s j), durée déclarée reposée : %s an(s), soit %s j",
            regle.code, avant, plancher, annees, regle.total_retention_days,
        )
