# -*- coding: utf-8 -*-
"""Export mxGraph (.drawio) depuis les enregistrements.

La géométrie vient de `geometrie.py`, qui ne dépend d'aucun moteur de rendu.
Les styles sont ceux de la palette BPMN de draw.io lui-même
(`js/diagramly/sidebar/Sidebar-BPMN.js`, dépôt jgraph/drawio), rassemblés dans
`CATALOGUE` ci-dessous — c'est ce qui garantit que les formes sont celles que
l'outil connaît déjà.
"""
from . import geometrie
from .geometrie import node_box, plan, points_flux, points_message, MARGE

from xml.sax.saxutils import quoteattr

from .bpmn import _nc

INK, GREY = '#2D3031', '#73787A'


BLEU, BLEU_DOUX = '#29ABE1', '#EAF7FD'


AMBRE, AMBRE_DOUX = '#D69921', '#FEF6E6'


POOL_HDR, LANE_BG = '#ECEFF1', '#F9FAFB'


_PTS_RECT = ('points=[[0.25,0,0],[0.5,0,0],[0.75,0,0],[1,0.25,0],[1,0.5,0],'
             '[1,0.75,0],[0.75,1,0],[0.5,1,0],[0.25,1,0],[0,0.75,0],[0,0.5,0],'
             '[0,0.25,0]];')


_TACHE = (_PTS_RECT + 'shape=mxgraph.bpmn.task2;whiteSpace=wrap;rectStyle=rounded;'
          'size=10;html=1;container=0;expand=0;collapsible=0;taskMarker=')


_EVT = ('points=[[0.145,0.145,0],[0.5,0,0],[0.855,0.145,0],[1,0.5,0],'
        '[0.855,0.855,0],[0.5,1,0],[0.145,0.855,0],[0,0.5,0]];'
        'shape=mxgraph.bpmn.event;html=1;verticalLabelPosition=bottom;'
        'labelBackgroundColor=none;verticalAlign=top;align=center;'
        'perimeter=ellipsePerimeter;outlineConnect=0;aspect=fixed;outline=')


_PORTE = ('points=[[0.25,0.25,0],[0.5,0,0],[0.75,0.25,0],[1,0.5,0],[0.75,0.75,0],'
          '[0.5,1,0],[0.25,0.75,0],[0,0.5,0]];shape=mxgraph.bpmn.gateway2;html=1;'
          'verticalLabelPosition=bottom;labelBackgroundColor=none;'
          'verticalAlign=top;align=center;perimeter=rhombusPerimeter;'
          'outlineConnect=0;outline=')


_COULOIR = ('swimlane;html=1;fontStyle=1;collapsible=0;horizontal=0;swimlaneLine=1;'
            'strokeWidth=1;whiteSpace=wrap;')


CATALOGUE = {
    'start':      _EVT + 'standard;symbol=general;',
    'msgStart':   _EVT + 'standard;symbol=message;',
    'end':        _EVT + 'end;symbol=general;',
    'timerCatch': _EVT + 'catching;symbol=timer;',
    'msgCatch':   _EVT + 'catching;symbol=message;',
    'xor':        _PORTE + 'none;symbol=none;gwType=exclusive;',
    'and':        _PORTE + 'none;symbol=none;gwType=parallel;',
    'or':         _PORTE + 'end;symbol=general;',
    'task':       _TACHE + 'abstract;',
    'send':       _TACHE + 'send;',
    'receive':    _TACHE + 'receive;',
    'user':       _TACHE + 'user;',
    'sub':        _TACHE + 'abstract;isLoopSub=1;',
    'store':      ('shape=datastore;html=1;labelPosition=center;'
                   'verticalLabelPosition=bottom;align=center;verticalAlign=top;'),
    'note':       ('shape=mxgraph.flowchart.annotation_2;html=1;align=left;'
                   'verticalAlign=middle;spacingLeft=10;spacingRight=6;'
                   'whiteSpace=wrap;fontSize=9;'),
    # aretes
    'flux':       ('edgeStyle=none;rounded=0;html=1;endArrow=blockThin;endFill=1;'
                   'endSize=6;fontSize=9;labelBackgroundColor=none;'),
    'assoc':      ('edgeStyle=none;rounded=0;html=1;endArrow=none;startArrow=none;'
                   'dashed=1;dashPattern=1 4;'),
    'msg':        ('edgeStyle=none;rounded=0;html=1;dashed=1;dashPattern=8 4;'
                   'endArrow=blockThin;endFill=0;startArrow=oval;startFill=0;'
                   'endSize=6;startSize=4;fontSize=9;labelBackgroundColor=none;'),
    # cadres
    'pool':       _COULOIR + f'startSize={geometrie.POOL_HDR_W:.0f};',
    'couloir':    _COULOIR + f'startSize={geometrie.LANE_HDR:.0f};',
}


TONS = {
    'ai':   f'strokeColor={BLEU};fontColor={BLEU};fillColor={BLEU_DOUX};',
    'risk': f'strokeColor={AMBRE};fontColor={AMBRE};fillColor={AMBRE_DOUX};',
    None:   f'strokeColor={GREY};fontColor={GREY};fillColor=none;',
}


ENCRE = f'strokeColor={INK};fontColor={INK};fillColor=#FFFFFF;'


def style(n):
    """Style complet d'un noeud du modele.

    Les annotations gardent leur ton — c'est le seul endroit ou le `.drawio`
    porte plus que le `.bpmn`, ou le bleu et l'ambre n'ont pas d'equivalent.
    """
    if n['kind'] == 'note':
        return CATALOGUE['note'] + TONS[n.get('tone')]
    return CATALOGUE[n['kind']] + ENCRE + 'fontSize=9;'


def _fraction(p, x, y, w, h):
    """Point d'accroche en fraction des bornes d'une cellule."""
    return ((p[0] - x) / w if w else 0.5, (p[1] - y) / h if h else 0.5)


def diagramme_xml(d, px, titre):
    pos, lane_geo, pool, ext, nodes, dx, dy = plan(d)
    lanes = d.get('lanes') or [{'id': '_', 'name': ''}]
    mono = len(lanes) == 1 and not lanes[0]['name']
    cells = ['        <mxCell id="0"/>',
             '        <mxCell id="1" parent="0"/>']

    def cid(i):
        return f'{px}_{_nc(i)}'

    def cellule(ident, valeur, sty, x, y, w, h, parent):
        return (f'        <mxCell id={quoteattr(ident)} value={quoteattr(valeur)} '
                f'style={quoteattr(sty)} vertex="1" parent={quoteattr(parent)}>\n'
                f'          <mxGeometry x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                f'height="{h:.1f}" as="geometry"/>\n        </mxCell>')

    # --- pool principal, couloirs, pools externes ---------------------------
    px0, py0 = pool['x0'] + dx, pool['y0'] + dy
    pw, ph = pool['x1'] - pool['x0'], pool['y1'] - pool['y0']
    cells.append(cellule(cid('pool'), pool['name'],
                         CATALOGUE['pool'] + f'fillColor={POOL_HDR};'
                         f'swimlaneFillColor=#FFFFFF;strokeColor={INK};'
                         f'fontColor={INK};', px0, py0, pw, ph, '1'))
    for p in d.get('ext', []):
        e = ext[p['id']]
        cells.append(cellule(cid('pool_' + p['id']), e['name'],
                             CATALOGUE['pool'] + f'fillColor={POOL_HDR};'
                             f'swimlaneFillColor=#FFFFFF;strokeColor={INK};'
                             f'fontColor={INK};',
                             e['x0'] + dx, e['y0'] + dy,
                             e['x1'] - e['x0'], e['y1'] - e['y0'], '1'))
    parent_de = {}
    if mono:
        for n in d['nodes']:
            parent_de[n['id']] = (cid('pool'), px0, py0)
    else:
        for ln in lanes:
            g = lane_geo[ln['id']]
            lx, ly = pool['x_lane'] + dx, pool['y0'] + g['y0'] + dy
            cells.append(cellule(
                cid('lane_' + ln['id']), ln['name'],
                CATALOGUE['couloir'] + f'fillColor={LANE_BG};'
                f'swimlaneFillColor=#FFFFFF;strokeColor={INK};fontColor={INK};',
                geometrie.POOL_HDR_W, g['y0'], pool['x1'] - pool['x_lane'], g['h'],
                cid('pool')))
            for n in d['nodes']:
                if n.get('lane', lanes[0]['id']) == ln['id']:
                    parent_de[n['id']] = (cid('lane_' + ln['id']), lx, ly)

    # --- noeuds -------------------------------------------------------------
    for n in d['nodes']:
        cx, cy = pos[n['id']]
        w, h = node_box(n)
        parent, ox, oy = parent_de[n['id']]
        cells.append(cellule(cid(n['id']), n.get('name', ''), style(n),
                             cx - w / 2 + dx - ox, cy - h / 2 + dy - oy,
                             w, h, parent))

    # --- aretes -------------------------------------------------------------
    def arete(ident, valeur, sty, src, tgt, pts):
        """Une arete mxGraph : accroches figees, points intermediaires absolus.

        `src` et `tgt` sont des couples (identifiant modele, bornes absolues).
        Figer les accroches est ce qui empeche draw.io de re-router l'arete a
        sa guise : sans elles, le trace du PDF ne survit pas a l'ouverture.
        """
        acc = ''
        for role, (_, bornes), p in (('exit', src, pts[0]), ('entry', tgt, pts[-1])):
            bxx, byy, bw, bh = bornes
            fx, fy = _fraction((p[0] + dx, p[1] + dy), bxx, byy, bw, bh)
            acc += f'{role}X={fx:.4f};{role}Y={fy:.4f};{role}Dx=0;{role}Dy=0;'
        inter = ''.join(f'\n            <mxPoint x="{a + dx:.1f}" y="{b + dy:.1f}"/>'
                        for a, b in pts[1:-1])
        tableau = (f'\n          <Array as="points">{inter}\n          </Array>'
                   if inter else '')
        return (f'        <mxCell id={quoteattr(ident)} value={quoteattr(valeur)} '
                f'style={quoteattr(sty + acc)} edge="1" parent="1" '
                f'source={quoteattr(cid(src[0]))} target={quoteattr(cid(tgt[0]))}>\n'
                f'          <mxGeometry relative="1" as="geometry">{tableau}\n'
                f'          </mxGeometry>\n        </mxCell>')

    def bornes_de(nid_modele):
        cx, cy = pos[nid_modele]
        w, h = node_box(nodes[nid_modele])
        return (cx - w / 2 + dx, cy - h / 2 + dy, w, h)

    for i, f in enumerate(d.get('flows', []), 1):
        pts = points_flux(f, nodes, pos)
        assoc = f.get('r') == 'assoc'
        cells.append(arete(cid(f'flow{i}'), '' if assoc else f.get('label', ''),
                           CATALOGUE['assoc' if assoc else 'flux'],
                           (f['src'], bornes_de(f['src'])),
                           (f['tgt'], bornes_de(f['tgt'])), pts))
    for i, m in enumerate(d.get('msgs', []), 1):
        pts = points_message(m, nodes, pos, ext)
        e = ext[m['pool']]
        bout_pool = ('pool_' + m['pool'], (e['x0'] + dx, e['y0'] + dy,
                                           e['x1'] - e['x0'], e['y1'] - e['y0']))
        bout_noeud = (m['node'], bornes_de(m['node']))
        entrant = m.get('dir') == 'in'
        cells.append(arete(cid(f'msg{i}'), m.get('label', ''), CATALOGUE['msg'],
                           bout_pool if entrant else bout_noeud,
                           bout_noeud if entrant else bout_pool, pts))

    largeur = int(pool['x1'] + dx + MARGE)
    hauteur = int(max([pool['y1']] + [e['y1'] for e in ext.values()]) + dy + MARGE)
    return (f'  <diagram id={quoteattr(px)} name={quoteattr(titre)}>\n'
            f'    <mxGraphModel dx="{largeur}" dy="{hauteur}" grid="0" gridSize="10" '
            f'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" '
            f'pageScale="1" pageWidth="1100" pageHeight="850" math="0" shadow="0">\n'
            f'      <root>\n' + '\n'.join(cells) +
            f'\n      </root>\n    </mxGraphModel>\n  </diagram>')


def to_mxgraph(diagrammes, prefixes=None):
    if isinstance(diagrammes, dict):
        diagrammes = [diagrammes]
    prefixes = prefixes or [f'd{i}' for i in range(1, len(diagrammes) + 1)]
    pages = []
    for d, px in zip(diagrammes, prefixes):
        titre = d.get('title', '')
        if d.get('level'):
            titre = f'{d["level"]} — {titre}'
        pages.append(diagramme_xml(d, px, titre))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<mxfile host="Blue Fox — bf_process" type="device" version="1.0">\n'
            + '\n'.join(pages) + '\n</mxfile>\n')
