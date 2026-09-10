"""Repère de l'ordre du jour tel qu'il est PARTI, et écart avec ce qu'il est devenu.

Deux fonctions pures, sans ORM, pour qu'elles se lisent et se mutent seules.

Pourquoi un repère de contenu, et pas une comparaison d'horodatages : mesuré sur
un parc réel de 53 ordres du jour réellement envoyés avant leur rencontre, un
drapeau « la fiche a été écrite après l'envoi » s'allume sur **51** d'entre eux,
et un drapeau « un sujet a été écrit après l'envoi » sur 18, alors que 11
seulement ont vu leur contenu changer. L'écart vient d'un seul geste :
« Démarrer la rencontre » écrit `live_notes_html` sur chaque sujet, ce qui
touche 66 lignes de sujet sans rien changer à ce que les destinataires ont reçu.
Un contrôle qui s'allume 51 fois sur 53 ne contrôle rien.
"""

import hashlib

SNAPSHOT_VERSION = 1

# Les intitulés des champs d'en-tête, dans l'ordre où le courriel et le PDF les
# montrent. Une entrée ici = un champ que le destinataire a sous les yeux.
HEAD_LABELS = (
    ('name', 'Titre'),
    ('date', 'Date'),
    ('duration', 'Durée prévue'),
    ('location', 'Lieu'),
    ('participants', 'Participants'),
    ('objectives', 'Objectifs'),
    ('preparation', 'Préparation'),
    ('context', 'Contexte'),
)

# Les champs d'en-tête dont la valeur est une EMPREINTE : on sait qu'ils ont
# changé, on ne sait pas en quoi, et c'est voulu.
DIGESTED_HEAD = ('objectives', 'preparation', 'context')


def digest(value):
    """Empreinte courte d'un corps de texte libre.

    Le repère ne garde jamais le texte lui-même. Un repère qui recopierait les
    corps ressusciterait une phrase retirée à la main de la fiche. Le module a
    déjà payé ce piège une fois, entre le courriel du compte rendu et son PDF,
    où un lien de parenté retiré d'un côté est parti dans l'autre. Une empreinte
    répond « ça a changé » sans rien pouvoir rendre.
    """
    if not value:
        return ''
    return hashlib.sha256(str(value).strip().encode('utf-8')).hexdigest()[:16]


def build_snapshot(agenda):
    """Geler la surface COMMUNIQUÉE d'un ordre du jour.

    La source est ``_get_report_data()``, celle-là même que le PDF consomme :
    le repère et l'artefact envoyé ne peuvent donc pas raconter deux histoires
    différentes. Ce que le destinataire ne voit pas (notes en direct, décisions
    saisies pendant la rencontre, état du pré-remplissage, pièces jointes hors
    fenêtre) n'entre pas dans le repère, donc ne peut pas produire d'écart.
    """
    data = agenda._get_report_data()
    return {
        'v': SNAPSHOT_VERSION,
        'head': {
            'name': agenda.name or '',
            'date': str(agenda.date or ''),
            'duration': agenda.duration_planned or 0,
            'location': agenda.location or '',
            'participants': sorted(agenda.participant_ids.mapped('name')),
            'objectives': digest(agenda.objectives),
            'preparation': digest(agenda.preparation_html),
            'context': digest(agenda.context_html),
        },
        'topics': [
            {
                'id': t['id'],
                'n': t['name'] or '',
                'd': t['duration'] or 0,
                'p': t['presenter'] or '',
                'x': digest(t['description']),
            }
            for t in data['topics']
        ],
        'tasks': [{'id': t['id'], 'n': t['name'] or ''} for t in data['tagged_tasks']],
    }


def _index(rows):
    """Lignes par identifiant, en gardant l'ordre d'origine à côté."""
    by_id = {}
    order = []
    for row in rows or ():
        rid = row.get('id')
        if rid is None:
            continue
        by_id[rid] = row
        order.append(rid)
    return by_id, order


def diff_snapshots(before, after, include_tasks=True):
    """Écart entre deux repères. Rend toujours la même forme, jamais None.

    ``include_tasks`` est faux quand la liste des éléments d'action n'est plus
    résolue par le module : un ordre du jour dont la date est passée, ou qui
    n'est plus actif, rend une liste VIDE par construction. Comparer cette liste
    vide au repère annoncerait « tous les éléments d'action ont été retirés » à
    l'instant même où la rencontre commence. L'appelant tranche, avec le même
    prédicat que celui qui résout les tâches.
    """
    out = {
        'head': [],
        'topics_added': [],
        'topics_removed': [],
        'topics_renamed': [],
        'topics_changed': [],
        'topics_reordered': False,
        'tasks_added': [],
        'tasks_removed': [],
        'ids': {'added': [], 'changed': []},
        'count': 0,
    }
    if not isinstance(before, dict) or not isinstance(after, dict):
        return out

    b_head = before.get('head') or {}
    a_head = after.get('head') or {}
    for key, label in HEAD_LABELS:
        old, new = b_head.get(key), a_head.get(key)
        if old == new:
            continue
        if key == 'participants':
            old_set, new_set = set(old or ()), set(new or ())
            out['head'].append({
                'key': key,
                'label': label,
                'added': sorted(new_set - old_set),
                'removed': sorted(old_set - new_set),
            })
            continue
        entry = {'key': key, 'label': label}
        if key not in DIGESTED_HEAD:
            entry['from'] = '' if old in (None, 0) else str(old)
            entry['to'] = '' if new in (None, 0) else str(new)
        out['head'].append(entry)

    b_top, b_order = _index(before.get('topics'))
    a_top, a_order = _index(after.get('topics'))
    for tid in a_order:
        if tid not in b_top:
            out['topics_added'].append(a_top[tid].get('n') or '')
            out['ids']['added'].append(tid)
            continue
        old, new = b_top[tid], a_top[tid]
        if (old.get('n') or '') != (new.get('n') or ''):
            out['topics_renamed'].append({
                'from': old.get('n') or '', 'to': new.get('n') or ''})
            out['ids']['changed'].append(tid)
        what = []
        if (old.get('d') or 0) != (new.get('d') or 0):
            what.append('durée')
        if (old.get('p') or '') != (new.get('p') or ''):
            what.append('intervenant')
        if (old.get('x') or '') != (new.get('x') or ''):
            what.append('contexte')
        if what:
            out['topics_changed'].append({'name': new.get('n') or '', 'what': what})
            if tid not in out['ids']['changed']:
                out['ids']['changed'].append(tid)
    for tid in b_order:
        if tid not in a_top:
            out['topics_removed'].append(b_top[tid].get('n') or '')

    # L'ordre ne compte QUE sur les sujets présents des deux côtés : une
    # insertion décale tout ce qui la suit, et signaler ce décalage comme un
    # réordonnancement transformerait un ajout en avalanche.
    common_before = [t for t in b_order if t in a_top]
    common_after = [t for t in a_order if t in b_top]
    out['topics_reordered'] = common_before != common_after

    if include_tasks:
        b_task, b_torder = _index(before.get('tasks'))
        a_task, a_torder = _index(after.get('tasks'))
        out['tasks_added'] = [a_task[i].get('n') or '' for i in a_torder if i not in b_task]
        out['tasks_removed'] = [b_task[i].get('n') or '' for i in b_torder if i not in a_task]

    out['count'] = (
        len(out['head'])
        + len(out['topics_added'])
        + len(out['topics_removed'])
        + len(out['topics_renamed'])
        + len(out['topics_changed'])
        + (1 if out['topics_reordered'] else 0)
        + len(out['tasks_added'])
        + len(out['tasks_removed'])
    )
    return out
