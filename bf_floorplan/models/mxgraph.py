# -*- coding: utf-8 -*-
"""Export diagrams.net (`.drawio`) : le plan, avec les formes de la
bibliothèque « Floorplans » de diagrams.net et un calque par nature.

Un centimètre du plan devient un pixel du dessin. Les murs, portes et
fenêtres se finissent là-bas ; ce qui a une fiche vient d'ici.
"""
import base64
from xml.sax.saxutils import quoteattr

from odoo import _, fields, models

from .genres import couleur_zone

INK, GRIS = "#2D3031", "#73787A"
AMBRE, ROUGE = "#D69921", "#C0392B"

# Préfixe de la bibliothèque, tel que le panneau latéral de diagrams.net
# l'écrit (Sidebar-Floorplan.js) : l'étiquette sous la forme, le glyphe
# au-dessus.
_FP = ("verticalLabelPosition=bottom;html=1;verticalAlign=top;align=center;"
       "fontFamily=Lexend;fontColor=#2D3031;shape=mxgraph.floorplan.")
_RECT = ("rounded=0;whiteSpace=wrap;html=1;fontFamily=Lexend;fontColor=#2D3031;"
         "strokeColor=#2D3031;fillColor=%s;")
_ROND = ("ellipse;whiteSpace=wrap;html=1;aspect=fixed;fontFamily=Lexend;"
         "fontColor=#2D3031;strokeColor=#2D3031;fillColor=%s;")

STYLES_ELEMENT = {
    "poste": _FP + "workstation;",
    "table": _FP + "table;",
    "imprimante": _FP + "printer;",
    "ecran": _FP + "flat_tv;",
    "commutateur": _RECT % "#FFFFFF",
    "borne": _ROND % "#EAF7FD",
    "serveur": _RECT % "#ECEFF1",
    "baie": _RECT % "#DDE3E6",
    "prise": _RECT % "#FFFFFF",
    "telephone": _ROND % "#FFFFFF",
    "camera": _ROND % "#FFFFFF",
    "autre": _RECT % "#F4F4F4",
}
COULEUR_LIEN = {"reseau": GRIS, "fibre": AMBRE, "electrique": ROUGE, "autre": INK}


def _mimetype(octets):
    if octets[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if octets[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"


def _cellule(ident, valeur, style, x, y, w, h, parent, rotation=0):
    if rotation:
        style += f"rotation={rotation};"
    return (f'        <mxCell id={quoteattr(ident)} value={quoteattr(valeur)} '
            f'style={quoteattr(style)} vertex="1" parent={quoteattr(parent)}>\n'
            f'          <mxGeometry x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" '
            f'as="geometry"/>\n        </mxCell>')


def _arete(ident, valeur, style, src, tgt, parent):
    return (f'        <mxCell id={quoteattr(ident)} value={quoteattr(valeur)} '
            f'style={quoteattr(style)} edge="1" parent={quoteattr(parent)} '
            f'source={quoteattr(src)} target={quoteattr(tgt)}>\n'
            f'          <mxGeometry relative="1" as="geometry"/>\n        </mxCell>')


def to_mxgraph(d, fond=None):
    """`d` est `to_dict()` du plan ; `fond` les octets de l'image, ou None."""
    cellules = ['        <mxCell id="0"/>',
                '        <mxCell id="1" value="Fond" parent="0"/>',
                '        <mxCell id="zones" value="Zones" parent="0"/>',
                '        <mxCell id="elements" value="Éléments" parent="0"/>',
                '        <mxCell id="liens" value="Liens" parent="0"/>']
    if fond:
        b64 = base64.b64encode(fond).decode("ascii")
        style = ("shape=image;imageAspect=0;verticalLabelPosition=bottom;"
                 "verticalAlign=top;movable=0;resizable=0;rotatable=0;"
                 "connectable=0;editable=0;"
                 f"image=data:{_mimetype(fond)},{b64};")
        cellules.append(_cellule("fond", "", style, 0, 0, d["largeur"], d["hauteur"], "1"))
    for z in d["zones"]:
        style = ("rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;align=left;"
                 "spacingLeft=6;spacingTop=2;opacity=80;fontFamily=Lexend;"
                 f"fontColor=#2D3031;strokeColor=#2D3031;fillColor={couleur_zone(z['genre'])};")
        valeur = z["nom"] + (f" ({z['occupes']}/{z['capacite']})" if z["capacite"] else "")
        cellules.append(_cellule(f"zone-{z['id']}", valeur, style,
                                 z["x"], z["y"], z["w"], z["h"], "zones"))
    for e in d["elements"]:
        style = STYLES_ELEMENT.get(e["genre"], STYLES_ELEMENT["autre"])
        cellules.append(_cellule(f"element-{e['id']}", e["nom"], style,
                                 e["x"], e["y"], e["w"], e["h"], "elements",
                                 rotation=e["rot"]))
    for li in d["liens"]:
        style = ("edgeStyle=none;endArrow=none;dashed=1;html=1;fontFamily=Lexend;"
                 f"fontColor=#2D3031;strokeColor={COULEUR_LIEN.get(li['genre'], GRIS)};")
        cellules.append(_arete(f"lien-{li['id']}", li["etiquette"], style,
                               f"element-{li['src']}", f"element-{li['dst']}", "liens"))
    corps = "\n".join(cellules)
    return (
        '<mxfile host="Blue Fox — plans d\'étage" type="device" version="1.0">\n'
        f'  <diagram id={quoteattr("plan-%s" % d["plan_id"])} name={quoteattr(d["titre"])}>\n'
        f'    <mxGraphModel dx="{d["largeur"]:g}" dy="{d["hauteur"]:g}" grid="1" '
        f'gridSize="{d["pas"]:g}" guides="1" tooltips="1" connect="1" arrows="1" '
        f'fold="1" page="1" pageScale="1" pageWidth="{d["largeur"]:g}" '
        f'pageHeight="{d["hauteur"]:g}" math="0" shadow="0">\n'
        '      <root>\n' + corps + '\n      </root>\n'
        '    </mxGraphModel>\n  </diagram>\n</mxfile>\n')


class BfFloorplanExport(models.Model):
    _inherit = "bf.floorplan"

    def exporter_mxgraph(self):
        self.ensure_one()
        fond = base64.b64decode(self.fond) if self.fond else None
        return to_mxgraph(self.to_dict(), fond)

    def _telecharger(self, contenu, extension, mimetype):
        """Le fichier reste en pièce jointe du plan : l'export d'un jour se
        retrouve, et il se partage depuis le fil comme n'importe quel
        document."""
        self.ensure_one()
        nom = "%s-%s.%s" % (
            "".join(c if c.isalnum() or c in " -_" else "" for c in self.display_name).strip()
            or "plan", fields.Date.context_today(self).isoformat(), extension)
        # en sudo : Odoo exige le droit d'ÉCRIRE sur le plan pour y joindre un
        # fichier, et l'export est offert à qui peut le lire. La lecture de la
        # pièce, elle, reste gardée par le droit de lire le plan.
        octets = contenu.encode("utf-8") if isinstance(contenu, str) else contenu
        Piece = self.env["ir.attachment"].sudo()
        # l'export du jour remplace celui du jour : sinon chaque clic empile
        # un fichier sur le plan, sans borne
        piece = Piece.search([("res_model", "=", self._name), ("res_id", "=", self.id),
                              ("name", "=", nom)], limit=1)
        if piece:
            piece.write({"raw": octets, "mimetype": mimetype})
        else:
            piece = Piece.create({"name": nom, "raw": octets, "mimetype": mimetype,
                                  "res_model": self._name, "res_id": self.id})
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{piece.id}?download=true",
            "target": "self",
        }

    def action_telecharger_mxgraph(self):
        self.ensure_one()
        return self._telecharger(self.exporter_mxgraph(), "drawio", "application/xml")

    def action_imprimer(self):
        self.ensure_one()
        return self.env.ref("bf_floorplan.action_report_plan").report_action(self)
