"""Corrige les motifs `noreply` semés, que la conversion avait repris tels quels.

La règle `noreply` livrée s'ancrait au début de la chaîne (`^(noreply|…)@`)
alors que `email_from` porte l'en-tête `From` brut — presque toujours
`"Acme" <noreply@acme.com>`. Elle ne s'est jamais déclenchée.

La correction est arrivée par les fichiers de données du module, donc elle n'a
touché que les quatre règles qui portent un xmlid. Les copies semées par
`_seed_defaults_for_user` pour les autres personnes n'en ont pas : la migration
18.0.9.11.0 a fidèlement recopié le motif cassé, et leurs règles restent
inertes.

On ne réécrit que les conditions dont la valeur est **exactement** l'ancien
motif livré. Un motif que quelqu'un a retouché, même d'un caractère, n'est pas
touché : c'est sa décision, pas un défaut du module.
"""

import logging

_logger = logging.getLogger(__name__)

MOTIF_CASSE = (
    r"^(noreply|no-reply|notification|mailer-daemon|postmaster"
    r"|bounce|do-not-reply)@"
)
MOTIF_CORRIGE = (
    r"(?:^|[<\s:,;])(noreply|no-reply|notification|mailer-daemon"
    r"|postmaster|bounce|do-not-reply)@"
)


def migrate(cr, version):
    cr.execute(
        """
        UPDATE bf_email_rule_condition
           SET value = %s
         WHERE field_name = 'email_from'
           AND operator = 'regex'
           AND value = %s
        """,
        (MOTIF_CORRIGE, MOTIF_CASSE),
    )
    if cr.rowcount:
        _logger.info(
            "bf.email.rule.condition : %s motif(s) noreply réancré(s) sur une "
            "frontière d'adresse — ces règles ne se déclenchaient sur rien.",
            cr.rowcount,
        )
    else:
        _logger.info(
            "bf.email.rule.condition : aucun motif noreply livré à corriger.")
