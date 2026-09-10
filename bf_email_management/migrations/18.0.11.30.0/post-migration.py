"""Recalcule `is_late_night` dans le fuseau du propriétaire.

`rec.date` est un datetime NAÏF en UTC ; le calcul en lisait le `.hour` comme
s'il s'agissait d'une heure de bureau. À Montréal, 08–18 UTC valent 04–14
locales : tout courriel reçu après 14 h était marqué « hors heures ». Mesuré
sur une boîte réelle avant correction : **trois entrants sur quatre**.

Le drapeau n'est affiché que sur le formulaire — il ne pondère aucun tri, aucune
recherche, aucun score. Ce qui se répare ici est donc un affichage, pas une
décision qui aurait été prise de travers.

⚠️ En SQL et non par un recalcul ORM : `is_late_night` sort de
`_compute_signals`, qui relit pour chaque ligne les adresses du propriétaire et
les en-têtes brutes pour reconstruire une dizaine d'autres signaux. Recalculer
le lot entier coûterait des minutes de `-u` pour un booléen dérivé d'une seule
colonne.

⚠️ Un `UPDATE` par fuseau distinct, avec le nom du fuseau passé en paramètre
plutôt que collé dans la requête : `AT TIME ZONE <chaîne inconnue>` lève une
erreur Postgres qui ferait échouer la mise à jour du module au complet. Les
fuseaux sont donc validés en Python d'abord, et un fuseau illisible retombe
sur le défaut du module au lieu d'arrêter le `-u`.
"""

import logging

import pytz

_logger = logging.getLogger(__name__)

DEFAUT = "America/Montreal"


def migrate(cr, version):
    cr.execute("""
        SELECT coalesce(nullif(p.tz, ''), %s) AS tz, count(*)
          FROM bf_email e
          LEFT JOIN res_users u ON u.id = e.user_id
          LEFT JOIN res_partner p ON p.id = u.partner_id
         WHERE e.date IS NOT NULL
      GROUP BY 1
    """, (DEFAUT,))
    groupes = cr.fetchall()
    if not groupes:
        return

    total = 0
    for cle, compte in groupes:
        # ⚠️ Deux valeurs distinctes, et les confondre sélectionnerait les
        # lignes d'un AUTRE fuseau : `cle` désigne le groupe à mettre à jour,
        # `tz` sert seulement à la conversion.
        tz = cle
        if tz not in pytz.all_timezones_set:
            _logger.warning(
                "bf_email : fuseau « %s » inconnu sur %s lignes, "
                "repli sur %s", cle, compte, DEFAUT)
            tz = DEFAUT
        cr.execute("""
            UPDATE bf_email e
               SET is_late_night = (
                       extract(hour from loc.d) < 8
                    OR extract(hour from loc.d) >= 18
                    OR extract(isodow from loc.d) >= 6)
              FROM (
                    SELECT e2.id,
                           (e2.date AT TIME ZONE 'UTC' AT TIME ZONE %s) AS d
                      FROM bf_email e2
                      LEFT JOIN res_users u ON u.id = e2.user_id
                      LEFT JOIN res_partner p ON p.id = u.partner_id
                     WHERE e2.date IS NOT NULL
                       AND coalesce(nullif(p.tz, ''), %s) = %s
                   ) loc
             WHERE loc.id = e.id
               AND e.is_late_night IS DISTINCT FROM (
                       extract(hour from loc.d) < 8
                    OR extract(hour from loc.d) >= 18
                    OR extract(isodow from loc.d) >= 6)
        """, (tz, DEFAUT, cle))
        total += cr.rowcount

    _logger.info(
        "bf_email : « hors heures » recalculé, %s lignes corrigées "
        "sur %s fuseau(x)", total, len(groupes))
