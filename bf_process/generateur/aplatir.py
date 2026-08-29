#!/usr/bin/env python3
"""Déplie les niveaux en une seule carte continue.

Chaque sous-processus est remplacé par le corps de sa page enfant :
  - le flux entrant du sous-processus est redirigé vers l'entrée de l'enfant ;
  - les fins de l'enfant sont raccordées à la suite du parent ;
  - si l'enfant a plusieurs fins, la passerelle qui suit le sous-processus est
    dissoute et chaque fin part vers la cible de la porte qui porte son nom.

Les colonnes sont ensuite recalculées par plus long chemin, les rangées
héritent de la page d'origine, et les collisions sont écartées à la verticale.
"""
from collections import defaultdict


class Abandon(Exception):
    pass


def _index(diagrams):
    return {d['title'].strip().lower(): d for d in diagrams[1:]}


def flatten(diagrams, lane_order, parent_lane, title, level, pool, ext,
            col_w=176, row_h=104, lane_pad=58):
    children = _index(diagrams)
    nodes, flows, msgs = [], [], []
    seq = [0]

    def uid(prefix, i):
        return f'{prefix}{i}'

    def expand(d, prefix, lane_map, depth=0):
        """Retourne (nodes, flows, msgs, entree, sorties_par_nom_de_fin)."""
        if depth > 4:
            raise Abandon('imbrication trop profonde')
        ns, fs, ms = [], [], []
        ids = {}
        for n in d['nodes']:
            ids[n['id']] = uid(prefix, n['id'])
        lane_of = {n['id']: lane_map.get(n.get('lane', '_'), n.get('lane', '_'))
                   for n in d['nodes']}
        starts = [n for n in d['nodes'] if n['kind'] in ('start', 'msgStart')]
        ends = [n for n in d['nodes'] if n['kind'] == 'end']
        if len(starts) != 1:
            raise Abandon(f'{d["title"]} : {len(starts)} départs')
        seqflows = [f for f in d.get('flows', []) if f.get('r') != 'assoc']
        succ = defaultdict(list)
        pred = defaultdict(list)
        for f in seqflows:
            succ[f['src']].append(f)
            pred[f['tgt']].append(f)

        entry = succ[starts[0]['id']]
        if len(entry) != 1:
            raise Abandon(f'{d["title"]} : départ à {len(entry)} sorties')
        entry_id = entry[0]['tgt']

        skip = {starts[0]['id']} | {e['id'] for e in ends}
        sub_map = {}

        for n in d['nodes']:
            if n['id'] in skip:
                continue
            if n['kind'] == 'sub' and n['name'].strip().lower() in children:
                child = children[n['name'].strip().lower()]
                cn, cf, cm, cent, cexit = expand(
                    child, f'{prefix}{n["id"]}_', lane_map, depth + 1)
                ns += cn
                fs += cf
                ms += cm
                sub_map[n['id']] = (cent, cexit)
                continue
            node = dict(n)
            node['id'] = ids[n['id']]
            node['lane'] = lane_of[n['id']]
            ns.append(node)

        def resolve_src(nid):
            """Sorties réelles d'un nœud (un sous-processus rend ses fins)."""
            if nid in sub_map:
                return sub_map[nid][1]      # {nom de fin: [ids reels]}
            return {None: [ids[nid]]}

        def resolve_tgt(nid):
            if nid in sub_map:
                return sub_map[nid][0]
            return ids[nid]

        dissolved = set()
        extra_exits = {}
        for f in seqflows:
            if f['src'] in skip and f['src'] != starts[0]['id']:
                continue
            if f['src'] == starts[0]['id'] or f['tgt'] in {e['id'] for e in ends}:
                continue
            src_exits = resolve_src(f['src'])
            # sous-processus à plusieurs fins suivi d'une passerelle : on dissout
            if len(src_exits) > 1 and None not in src_exits:
                gw = next((x for x in d['nodes'] if x['id'] == f['tgt']), None)
                if gw is None or gw['kind'] not in ('xor', 'or'):
                    raise Abandon(f'{d["title"]} : sortie multiple sans passerelle')
                gates = {g.get('label'): g for g in succ[gw['id']]}
                for end_name, srcs in src_exits.items():
                    g = gates.get(end_name)
                    if g is None:
                        raise Abandon(
                            f'{d["title"]} : porte « {end_name} » introuvable')
                    tgt = next(x for x in d['nodes'] if x['id'] == g['tgt'])
                    if tgt['kind'] == 'end':      # la porte mène à une fin
                        extra_exits.setdefault(tgt.get('name'), []).extend(srcs)
                    else:
                        for s in srcs:
                            fs.append(dict(src=s, tgt=resolve_tgt(g['tgt'])))
                dissolved.add(gw['id'])
                continue
            for end_name, srcs in src_exits.items():
                for s in srcs:
                    nf = {k: v for k, v in f.items()
                          if k not in ('src', 'tgt', 'r', 'lp', 'd', 'lw')}
                    nf['src'], nf['tgt'] = s, resolve_tgt(f['tgt'])
                    fs.append(nf)

        fs = [f for f in fs
              if not any(f.get('src') == uid(prefix, g) or f.get('tgt') == uid(prefix, g)
                         for g in dissolved)]
        ns = [n for n in ns if n['id'] not in {uid(prefix, g) for g in dissolved}]

        # annotations : une note accrochée à un sous-processus suit l'entrée
        # de la page enfant, sinon elle se retrouverait sans hôte, tout à
        # gauche de la carte dépliée, par-dessus le nom du couloir.
        def anchor_id(nid):
            return sub_map[nid][0] if nid in sub_map else ids[nid]

        for f in d.get('flows', []):
            if f.get('r') != 'assoc':
                continue
            if f['src'] in skip or f['tgt'] in skip:
                continue
            fs.append(dict(f, src=anchor_id(f['src']), tgt=anchor_id(f['tgt'])))
        for m in d.get('msgs', []):
            if m['node'] in sub_map or m['node'] in skip:
                continue
            ms.append(dict(m, node=ids[m['node']]))

        exits = {k: list(v) for k, v in extra_exits.items()}
        for e in ends:
            for f in pred[e['id']]:
                if f['src'] in dissolved:   # déjà porté par extra_exits
                    continue
                ex = resolve_src(f['src'])
                if list(ex) == [None]:
                    exits.setdefault(e.get('name'), []).extend(ex[None])
                else:                     # les fins de l'enfant priment
                    for k, v in ex.items():
                        exits.setdefault(k, []).extend(v)
        return ns, fs, ms, resolve_tgt(entry_id), exits

    parent = diagrams[0]
    lane_map = {}
    ns, fs, ms, entry, exits = expand(parent, '', lane_map)

    # début et fins du parent, réintroduits
    p_start = next(n for n in parent['nodes'] if n['kind'] in ('start', 'msgStart'))
    start_node = dict(p_start, id='START', lane=parent_lane.get(p_start['id'], lane_order[0]['id']))
    ns.insert(0, start_node)
    fs.append(dict(src='START', tgt=entry))
    for i, (name, srcs) in enumerate(exits.items()):
        eid = f'END{i}'
        lane = parent_lane.get('END', lane_order[-1]['id'])
        ns.append(dict(kind='end', name=name, id=eid, lane=lane, row=0, col=0))
        for s in srcs:
            fs.append(dict(src=s, tgt=eid))
    for m in parent.get('msgs', []):
        if m['node'] == p_start['id']:
            ms.append(dict(m, node='START'))

    for n in ns:
        if n.get('lane') in (None, '_'):
            n['lane'] = parent_lane.get(n['id'], lane_order[0]['id'])
        n['lane'] = parent_lane.get(n['id'], n['lane'])

    _place(ns, fs, lane_order)
    return dict(title=title, level=level, pool=pool, lanes=lane_order,
                ext=ext, col_w=col_w, row_h=row_h, lane_pad=lane_pad,
                nodes=ns, flows=fs, msgs=ms, flat=True)


def _place(ns, fs, lane_order):
    """Colonnes par plus long chemin, rangées par couloir sans collision."""
    by_id = {n['id']: n for n in ns}
    seq = [f for f in fs if f.get('r') != 'assoc'
           and by_id.get(f['src'], {}).get('kind') != 'note'
           and by_id.get(f['tgt'], {}).get('kind') != 'note']
    succ = defaultdict(list)
    indeg = defaultdict(int)
    for f in seq:
        succ[f['src']].append(f['tgt'])
        indeg[f['tgt']] += 1
    col = {n['id']: 0 for n in ns}
    order, queue = [], [n['id'] for n in ns
                        if n['kind'] != 'note' and indeg[n['id']] == 0]
    deg = dict(indeg)
    while queue:
        x = queue.pop(0)
        order.append(x)
        for y in succ[x]:
            deg[y] -= 1
            if deg[y] == 0:
                queue.append(y)
    for x in order:                       # arêtes arrière ignorées
        for y in succ[x]:
            col[y] = max(col.get(y, 0), col[x] + 1)
    unranked = [n['id'] for n in ns if n['kind'] != 'note' and n['id'] not in order]
    for x in unranked:                    # nœuds sur un cycle : au plus tard
        col[x] = max([col[n['id']] + 1 for n in ns if x in succ[n['id']]] or [0])

    # rangées : une pile par (couloir, colonne)
    lanes = [l['id'] for l in lane_order]
    used = defaultdict(list)
    for n in sorted((n for n in ns if n['kind'] != 'note'),
                    key=lambda n: (col[n['id']], lanes.index(n['lane']))):
        c = col[n['id']]
        taken = used[(n['lane'], c)]
        row, k = 0.0, 0
        while any(abs(row - t) < 0.85 for t in taken) and k < 40:
            k += 1
            row = 0.9 * ((k + 1) // 2) * (1 if k % 2 else -1)
        taken.append(row)
        n['col'], n['row'] = c, row
    # étiquettes de portes : décalées selon la rangée d'arrivée
    lane_i = {l['id']: i for i, l in enumerate(lane_order)}
    for f in fs:
        if not f.get('label') or f.get('r') == 'assoc':
            continue
        a, b = by_id.get(f['src']), by_id.get(f['tgt'])
        if not a or not b:
            continue
        dy = ((lane_i.get(b['lane'], 0) - lane_i.get(a['lane'], 0)) * 2
              + b.get('row', 0) - a.get('row', 0))
        f['lp'] = (0, max(-30, min(30, dy * 22)) - 5)
        f['lw'] = 104

    # annotations : bande réservée sous le dernier rang de leur couloir
    anchor = {}
    for f in fs:
        if f.get('r') == 'assoc':
            a, b = f['src'], f['tgt']
            note = a if by_id.get(a, {}).get('kind') == 'note' else b
            other = b if note == a else a
            anchor[note] = other
    maxrow = {}
    for n in ns:
        if n['kind'] == 'note':
            continue
        maxrow[n['lane']] = max(maxrow.get(n['lane'], 0), n['row'])
    notes = [n for n in ns if n['kind'] == 'note']
    for n in notes:
        host = by_id.get(anchor.get(n['id']))
        n['lane'] = host['lane'] if host else lane_order[0]['id']
        n['_c'] = host.get('col', 0) if host else 0.9   # jamais sous l'en-tête
    placed = defaultdict(list)
    for n in sorted(notes, key=lambda n: n['_c']):
        base = maxrow.get(n['lane'], 0) + 1.25
        row, k = base, 0
        while any(abs(n['_c'] - c) < 1.6 for c, r in placed[n['lane']]
                  if abs(r - row) < 0.6) and k < 12:
            k += 1
            row = base + 1.05 * k
        placed[n['lane']].append((n['_c'], row))
        n['col'], n['row'] = n['_c'] + 0.1, row
        del n['_c']
