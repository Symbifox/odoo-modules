# -*- coding: utf-8 -*-
"""Géométrie de la carte, sans moteur typographique.

Deux hauteurs dépendent de la largeur du texte : celle d'une annotation et
celle des bandeaux de pools externes. Le générateur hors serveur les mesure
avec la police ; le serveur, lui, les recalcule avec `mesure`, dont la table de
largeurs est étalonnée sur cette même police. Les deux tombent sur le même
nombre — c'est ce qui permet à une carte créée à la main dans Odoo d'être
tracée et exportée comme une carte produite par le générateur.

Une mesure conservée sur l'enregistrement (`bf.process.node.height`,
`bf.process.diagram.ext_header`) reste prioritaire : c'est une surcharge, et
un ajustement humain doit survivre au recalcul.

Ce module ne dépend de rien d'autre que la bibliothèque standard.
"""
from . import mesure

# --- constantes de tracé, identiques à celles du générateur ------------------
EV_R, GW_S = 19.0, 46.0
TASK_W, TASK_H = 152.0, 68.0
SUB_W, SUB_H = 168.0, 76.0
STORE_W, STORE_H = 46.0, 50.0
NOTE_W = 190.0
LANE_HDR, POOL_HDR_W = 26.0, 26.0
EXT_GAP = 44.0
MARGE = 40.0

EVENEMENTS = ("start", "msgStart", "end", "timerCatch", "msgCatch")
PASSERELLES = ("xor", "and", "or")


class GeometrieIncomplete(Exception):
    """Une mesure manque et ne se recalcule pas : deviner serait pire.

    Ne se lève plus pour les deux hauteurs de texte, que `mesure` sait refaire.
    Reste là pour ce qui, un jour, ne pourrait toujours pas se mesurer.
    """


def node_box(n):
    """Bornes d'un nœud. Une mesure conservée l'emporte sur la mesure refaite."""
    k = n["kind"]
    if k in EVENEMENTS:
        return 2 * EV_R, 2 * EV_R
    if k in PASSERELLES:
        return GW_S, GW_S
    if k == "sub":
        return n.get("w", SUB_W), n.get("h", SUB_H)
    if k == "store":
        return STORE_W, STORE_H
    if k == "note":
        w = n.get("w") or NOTE_W
        return w, n.get("h") or mesure.hauteur_annotation(n.get("name", ""), w)
    return n.get("w", TASK_W), n.get("h", TASK_H)


def geometrie(d):
    """Place couloirs, pools et nœuds, en coordonnées du modèle."""
    col_w = d.get("col_w", 168.0)
    row_h = d.get("row_h", 100.0)
    lane_pad = d.get("lane_pad", 50.0)

    lanes = d.get("lanes") or [{"id": "_", "name": ""}]
    rows_by_lane = {}
    for n in d["nodes"]:
        rows_by_lane.setdefault(n.get("lane", lanes[0]["id"]), []).append(n["row"])

    lane_geo, y = {}, 0.0
    for ln in lanes:
        rs = rows_by_lane.get(ln["id"], [0])
        h = (max(rs) - min(rs)) * row_h + 2 * lane_pad
        lane_geo[ln["id"]] = dict(y0=y, h=h, base=min(rs), name=ln["name"])
        y += h
    pool_h = y

    cols = [n["col"] for n in d["nodes"]]
    x_left = min(cols) * col_w - col_w * 0.55
    x_right = max(cols) * col_w + col_w * 0.55

    ext_top = [p for p in d.get("ext", []) if p["pos"] == "top"]
    ext_bot = [p for p in d.get("ext", []) if p["pos"] == "bottom"]
    ext_h = d.get("ext_header") or mesure.hauteur_pools_externes(
        [p["name"] for p in d.get("ext", [])])
    y_pool = len(ext_top) * (ext_h + EXT_GAP)

    ext = {}
    for i, p in enumerate(ext_top):
        yy = i * (ext_h + EXT_GAP)
        ext[p["id"]] = dict(name=p["name"], x0=x_left - POOL_HDR_W, y0=yy,
                            x1=x_right, y1=yy + ext_h, side="top")
    for i, p in enumerate(ext_bot):
        yy = y_pool + pool_h + EXT_GAP + i * (ext_h + EXT_GAP)
        ext[p["id"]] = dict(name=p["name"], x0=x_left - POOL_HDR_W, y0=yy,
                            x1=x_right, y1=yy + ext_h, side="bottom")

    pos = {}
    for n in d["nodes"]:
        g = lane_geo[n.get("lane", lanes[0]["id"])]
        pos[n["id"]] = (n["col"] * col_w,
                        y_pool + g["y0"] + lane_pad + (n["row"] - g["base"]) * row_h)

    pool = dict(name=d["pool"], x0=x_left - POOL_HDR_W, y0=y_pool,
                x1=x_right, y1=y_pool + pool_h, x_lane=x_left)
    return pos, lane_geo, pool, ext


def plan(d):
    """Géométrie du niveau, plus la translation qui la rend positive."""
    pos, lane_geo, pool, ext = geometrie(d)
    nodes = {n["id"]: n for n in d["nodes"]}
    xs = [pool["x0"], pool["x1"]] + [e["x0"] for e in ext.values()] + \
         [e["x1"] for e in ext.values()]
    ys = [pool["y0"], pool["y1"]] + [e["y0"] for e in ext.values()] + \
         [e["y1"] for e in ext.values()]
    for nid, (cx, cy) in pos.items():
        w, h = node_box(nodes[nid])
        xs += [cx - w / 2, cx + w / 2]
        ys += [cy - h / 2, cy + h / 2]
    return pos, lane_geo, pool, ext, nodes, MARGE - min(xs), MARGE - min(ys)


def _anchor(n, cx, cy, side):
    w, h = node_box(n)
    return {"R": (cx + w / 2, cy), "L": (cx - w / 2, cy),
            "T": (cx, cy - h / 2), "B": (cx, cy + h / 2)}[side]


def points_flux(f, nodes, pos):
    """Routage d'un flux, en points."""
    s, t = nodes[f["src"]], nodes[f["tgt"]]
    sx, sy = pos[f["src"]]
    tx, ty = pos[f["tgt"]]
    r = f.get("r", "auto")
    if r == "assoc":
        return [_anchor(s, sx, sy, f.get("a", "B")),
                _anchor(t, tx, ty, f.get("b", "T"))]
    if r == "auto":
        r = "h" if abs(sy - ty) < 3 else ("v" if abs(sx - tx) < 3 else "hv")
    if r == "h":
        a, b = ("R", "L") if tx > sx else ("L", "R")
        return [_anchor(s, sx, sy, a), _anchor(t, tx, ty, b)]
    if r == "v":
        a, b = ("B", "T") if ty > sy else ("T", "B")
        return [_anchor(s, sx, sy, a), _anchor(t, tx, ty, b)]
    if r == "hv":
        a = _anchor(s, sx, sy, "R" if tx > sx else "L")
        b = _anchor(t, tx, ty, "T" if ty > sy else "B")
        return [a, (b[0], a[1]), b]
    if r == "vh":
        a = _anchor(s, sx, sy, "B" if ty > sy else "T")
        b = _anchor(t, tx, ty, "L" if tx > sx else "R")
        return [a, (a[0], b[1]), b]
    if r in ("below", "above"):
        dd = f.get("d", 62)
        side = "B" if r == "below" else "T"
        a = _anchor(s, sx, sy, side)
        b = _anchor(t, tx, ty, side)
        yy = (max(a[1], b[1]) + dd) if r == "below" else (min(a[1], b[1]) - dd)
        return [a, (a[0], yy), (b[0], yy), b]
    return [_anchor(s, sx, sy, "R"), _anchor(t, tx, ty, "L")]


def points_message(m, nodes, pos, ext):
    """Routage d'un flux de message, en points."""
    n = nodes[m["node"]]
    cx, cy = pos[m["node"]]
    e = ext[m["pool"]]
    up = e["side"] == "top"
    dx = m.get("dx", 0)
    a = _anchor(n, cx, cy, "T" if up else "B")
    b = (cx + dx, e["y1"] if up else e["y0"])
    pts = [a, b]
    if dx:
        step = a[1] - 20 if up else a[1] + 20
        pts = [a, (a[0], step), (b[0], step), b]
    return pts[::-1] if m.get("dir") == "in" else pts
