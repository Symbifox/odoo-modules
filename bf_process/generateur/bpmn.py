# -*- coding: utf-8 -*-
"""Export BPMN 2.0 depuis les enregistrements.

La géométrie vient de `geometrie.py`, qui ne dépend d'aucun moteur de rendu :
c'est ce qui permet de calculer les mêmes coordonnées que celles montrées à
l'écran sans embarquer de bibliothèque PDF/typographie dans l'image Odoo.
Toute modification de ce fichier doit rester couverte par
`tests/test_aller_retour.py`, qui vérifie que l'aller-retour enregistrements
→ fichier → enregistrements reproduit exactement les mêmes nœuds et flux.
"""
from . import geometrie
from .geometrie import node_box, plan, points_flux, points_message, MARGE

from xml.sax.saxutils import escape, quoteattr
import re

NS = (
    'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
    'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
    'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'targetNamespace="http://bluefoxconsultant.com/bpmn"'
)


ELEMENT = {
    'start':      ('startEvent', None),
    'msgStart':   ('startEvent', 'messageEventDefinition'),
    'end':        ('endEvent', None),
    'timerCatch': ('intermediateCatchEvent', 'timerEventDefinition'),
    'msgCatch':   ('intermediateCatchEvent', 'messageEventDefinition'),
    'xor':        ('exclusiveGateway', None),
    'and':        ('parallelGateway', None),
    'or':         ('inclusiveGateway', None),
    'task':       ('task', None),
    'send':       ('sendTask', None),
    'receive':    ('receiveTask', None),
    'user':       ('userTask', None),
    'sub':        ('subProcess', None),
}


def _nc(s):
    """Identifiant NCName sur (prefixe, id du modele)."""
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(s))


def _bounds(x, y, w, h, dx, dy):
    return (f'<dc:Bounds x="{x + dx:.1f}" y="{y + dy:.1f}" '
            f'width="{w:.1f}" height="{h:.1f}"/>')


def diagramme_xml(d, px):
    """Rend (fragments de definitions, fragment de BPMNDiagram) pour un niveau."""
    pos, lane_geo, pool, ext, nodes, dx, dy = plan(d)
    lanes = d.get('lanes') or [{'id': '_', 'name': ''}]
    mono = len(lanes) == 1 and not lanes[0]['name']

    def nid(i):
        return f'{px}_{_nc(i)}'

    proc_id, colab_id = f'{px}_process', f'{px}_collab'
    flow_el, artefacts, shapes, edges = [], [], [], []

    # --- pools -------------------------------------------------------------
    parts = [f'    <bpmn:participant id={quoteattr(px + "_pool")} '
             f'name={quoteattr(pool["name"])} processRef={quoteattr(proc_id)}/>']
    shapes.append(
        f'    <bpmndi:BPMNShape id="{px}_pool_di" bpmnElement="{px}_pool" '
        f'isHorizontal="true">\n      '
        + _bounds(pool['x0'], pool['y0'], pool['x1'] - pool['x0'],
                  pool['y1'] - pool['y0'], dx, dy)
        + '\n    </bpmndi:BPMNShape>')
    for eid, e in ext.items():
        parts.append(f'    <bpmn:participant id={quoteattr(nid("pool_" + eid))} '
                     f'name={quoteattr(e["name"])}/>')
        shapes.append(
            f'    <bpmndi:BPMNShape id="{nid("pool_" + eid)}_di" '
            f'bpmnElement="{nid("pool_" + eid)}" isHorizontal="true">\n      '
            + _bounds(e['x0'], e['y0'], e['x1'] - e['x0'], e['y1'] - e['y0'], dx, dy)
            + '\n    </bpmndi:BPMNShape>')

    # --- couloirs ----------------------------------------------------------
    lane_xml = []
    if not mono:
        membres = {ln['id']: [] for ln in lanes}
        for n in d['nodes']:
            if n['kind'] in ELEMENT:
                membres[n.get('lane', lanes[0]['id'])].append(n['id'])
        for ln in lanes:
            refs = ''.join(f'\n        <bpmn:flowNodeRef>{nid(i)}</bpmn:flowNodeRef>'
                           for i in membres[ln['id']])
            lane_xml.append(f'      <bpmn:lane id={quoteattr(nid("lane_" + ln["id"]))} '
                            f'name={quoteattr(ln["name"])}>{refs}\n      </bpmn:lane>')
            g = lane_geo[ln['id']]
            shapes.append(
                f'    <bpmndi:BPMNShape id="{nid("lane_" + ln["id"])}_di" '
                f'bpmnElement="{nid("lane_" + ln["id"])}" isHorizontal="true">\n      '
                + _bounds(pool['x_lane'], pool['y0'] + g['y0'],
                          pool['x1'] - pool['x_lane'], g['h'], dx, dy)
                + '\n    </bpmndi:BPMNShape>')

    # --- noeuds ------------------------------------------------------------
    entrants = {n['id']: [] for n in d['nodes']}
    sortants = {n['id']: [] for n in d['nodes']}
    seq, assoc = [], []
    for i, f in enumerate(d.get('flows', []), 1):
        fid = nid(f'flow{i}')
        if f.get('r') == 'assoc':
            assoc.append((fid, f))
        else:
            seq.append((fid, f))
            sortants[f['src']].append(fid)
            entrants[f['tgt']].append(fid)

    for n in d['nodes']:
        k, name = n['kind'], n.get('name', '')
        cx, cy = pos[n['id']]
        w, h = node_box(n)
        extra = ''
        if k == 'note':
            artefacts.append(
                f'    <bpmn:textAnnotation id={quoteattr(nid(n["id"]))}>\n'
                f'      <bpmn:text>{escape(name)}</bpmn:text>\n'
                f'    </bpmn:textAnnotation>')
        elif k == 'store':
            flow_el.append(f'    <bpmn:dataStoreReference id={quoteattr(nid(n["id"]))} '
                           f'name={quoteattr(name)}/>')
        else:
            tag, evdef = ELEMENT[k]
            kids = ''.join(f'\n      <bpmn:incoming>{i}</bpmn:incoming>'
                           for i in entrants[n['id']])
            kids += ''.join(f'\n      <bpmn:outgoing>{i}</bpmn:outgoing>'
                            for i in sortants[n['id']])
            if evdef:
                kids += f'\n      <bpmn:{evdef} id={quoteattr(nid(n["id"] + "_ed"))}/>'
            flow_el.append(
                f'    <bpmn:{tag} id={quoteattr(nid(n["id"]))} '
                f'name={quoteattr(name)}>{kids}\n    </bpmn:{tag}>')
            if k == 'sub':
                extra = ' isExpanded="false"'
        shapes.append(
            f'    <bpmndi:BPMNShape id="{nid(n["id"])}_di" '
            f'bpmnElement="{nid(n["id"])}"{extra}>\n      '
            + _bounds(cx - w / 2, cy - h / 2, w, h, dx, dy)
            + '\n    </bpmndi:BPMNShape>')

    # --- flux de sequence et associations -----------------------------------
    def arete(eid, pts):
        wps = ''.join(f'\n      <di:waypoint x="{x + dx:.1f}" y="{y + dy:.1f}"/>'
                      for x, y in pts)
        return (f'    <bpmndi:BPMNEdge id="{eid}_di" bpmnElement="{eid}">'
                f'{wps}\n    </bpmndi:BPMNEdge>')

    for fid, f in seq:
        nom = f' name={quoteattr(f["label"])}' if f.get('label') else ''
        flow_el.append(f'    <bpmn:sequenceFlow id={quoteattr(fid)}{nom} '
                       f'sourceRef={quoteattr(nid(f["src"]))} '
                       f'targetRef={quoteattr(nid(f["tgt"]))}/>')
        edges.append(arete(fid, points_flux(f, nodes, pos)))
    for fid, f in assoc:
        artefacts.append(f'    <bpmn:association id={quoteattr(fid)} '
                         f'associationDirection="None" '
                         f'sourceRef={quoteattr(nid(f["src"]))} '
                         f'targetRef={quoteattr(nid(f["tgt"]))}/>')
        edges.append(arete(fid, points_flux(f, nodes, pos)))

    # --- flux de message ----------------------------------------------------
    for i, m in enumerate(d.get('msgs', []), 1):
        mid = nid(f'msg{i}')
        noeud, poolp = nid(m['node']), nid('pool_' + m['pool'])
        src, tgt = (poolp, noeud) if m.get('dir') == 'in' else (noeud, poolp)
        parts.append(f'    <bpmn:messageFlow id={quoteattr(mid)} '
                     f'name={quoteattr(m.get("label", ""))} '
                     f'sourceRef={quoteattr(src)} targetRef={quoteattr(tgt)}/>')
        edges.append(arete(mid, points_message(m, nodes, pos, ext)))

    definitions = (
        f'  <bpmn:collaboration id={quoteattr(colab_id)}>\n'
        + '\n'.join(parts) + f'\n  </bpmn:collaboration>\n'
        f'  <bpmn:process id={quoteattr(proc_id)} isExecutable="false">\n'
        + (f'    <bpmn:laneSet id={quoteattr(px + "_laneset")}>\n'
           + '\n'.join(lane_xml) + '\n    </bpmn:laneSet>\n' if lane_xml else '')
        + '\n'.join(flow_el) + '\n'
        + ('\n'.join(artefacts) + '\n' if artefacts else '')
        + '  </bpmn:process>'
    )
    titre = d.get('title', '')
    if d.get('level'):
        titre = f'{d["level"]} — {titre}'
    diagram = (
        f'  <bpmndi:BPMNDiagram id="{px}_diagram" name={quoteattr(titre)}>\n'
        f'    <bpmndi:BPMNPlane id="{px}_plane" bpmnElement={quoteattr(colab_id)}>\n'
        + '\n'.join(shapes + edges) + '\n'
        f'    </bpmndi:BPMNPlane>\n  </bpmndi:BPMNDiagram>'
    )
    return definitions, diagram


def to_bpmn(diagrammes, prefixes=None):
    """Rend le XML BPMN 2.0 complet pour une liste de niveaux.

    `prefixes` fixe le prefixe d'identifiant de chaque niveau. Il vaut `d1`,
    `d2`... par defaut : un niveau garde ainsi les memes identifiants dans son
    fichier propre et dans le fichier rassemble.
    """
    if isinstance(diagrammes, dict):
        diagrammes = [diagrammes]
    prefixes = prefixes or [f'd{i}' for i in range(1, len(diagrammes) + 1)]
    defs, diags = [], []
    for d, px in zip(diagrammes, prefixes):
        a, b = diagramme_xml(d, px)
        defs.append(a)
        diags.append(b)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<bpmn:definitions {NS} id="Definitions_1" exporter="Blue Fox — '
            f'bf_process" exporterVersion="1.0">\n'
            + '\n'.join(defs) + '\n' + '\n'.join(diags)
            + '\n</bpmn:definitions>\n')
