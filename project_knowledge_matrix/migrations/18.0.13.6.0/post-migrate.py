"""Dater les versions publiées sans date de publication.

Le calcul de la version courante d'un document triait ses versions publiées
sur ``release_date`` sans valeur de repli. Une version publiée sans date y
faisait comparer un booléen à une date : l'erreur ne tombait pas à l'écriture
mais au vidage de la transaction, donc l'écriture était refusée en entier et la
fiche du document devenait illisible, le champ se recalculant à chaque lecture.

La 18.0.13.6.0 pose le repli au tri et une contrainte qui refuse désormais
l'état publié sans date. Cette passe ferme le passé : elle donne une date de
publication aux versions déjà en base qui ont été publiées — ou remplacées —
sans en porter une, pour que le mélange d'une version datée et d'une version
qui ne l'est pas ne se produise plus. La date prise est la date d'entrée en
vigueur de la version à 05:00 UTC, sinon son approbation, sinon le gel de son
corps, sinon sa création, et à défaut de tout cela l'instant de la passe.

Ni l'état, ni le corps figé, ni les distributions ne bougent ; rien ne part par
courriel. Une ligne de journal par version datée. Rejouée, la passe ne trouve
plus rien.

Second geste : le rang de publication (``sequence_index``) est remis dans
l'ordre des dates, pour les versions publiées et remplacées. La migration
18.0.10.0.0 numérotait déjà sur ``COALESCE(release_date, create_date), id`` —
sa règle était juste, mais elle a tourné AVANT que les versions publiées par
script aient une date, et celles créées publiées après elle n'ont jamais reçu
de rang. Trois lecteurs s'y fiaient : la version précédente d'une publication,
l'historique imprimé des versions, et l'assistant de publication. Ils lisent
désormais la clé de publication ; le rang, lui, redit la même chose qu'elle.

Troisième geste, qui ne vient pas des données mais du code : le calcul de la
version courante dépend maintenant aussi de la date d'entrée en vigueur. Odoo
ne recalcule PAS les champs stockés dont les dépendances changent — seuls les
documents dont cette passe a daté une version se recalculent tout seuls. Les
autres garderaient leur valeur d'avant indéfiniment, donc la passe force le
recalcul sur tous les documents, archivés compris.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['project.document.version']._dater_les_publiees_sans_date()

    # Le rang de publication, remis d'aplomb maintenant que toutes les versions
    # publiées ont une date.
    env['project.document.version']._renumeroter_les_rangs_de_publication()

    documents = env['project.document'].with_context(active_test=False).search([])
    for nom in ('latest_version_id', 'current_version'):
        env.add_to_compute(documents._fields[nom], documents)
    env.flush_all()
