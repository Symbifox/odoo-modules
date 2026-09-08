# -*- coding: utf-8 -*-
"""Le tracé, en données : ce que l'écran, le PDF et l'export lisent.

Le composant client ne recalcule rien : il reçoit des centimètres et les
dessine. Trois rendus, une seule géométrie, celle des enregistrements.
"""
from odoo import _, models

from .genres import (GENRES_ELEMENT, GENRES_LIEN, GENRES_ZONE, couleur_zone,
                     libelle_element, libelle_zone)


class BfFloorplanRendu(models.Model):
    _inherit = "bf.floorplan"

    def _url_fond(self):
        """L'image de fond, par une URL : le navigateur la met en cache, et
        elle ne voyage pas en base64 à chaque écriture."""
        self.ensure_one()
        if not self.fond:
            return False
        marque = int(self.write_date.timestamp()) if self.write_date else 0
        return f"/web/image/bf.floorplan/{self.id}/fond?unique={marque}"

    def to_dict(self):
        """La géométrie et les faits, sans rien qui dépende de l'écran."""
        self.ensure_one()
        liens_par_element = {}
        for lien in self.lien_ids:
            for el in (lien.src_id, lien.dst_id):
                liens_par_element[el.id] = liens_par_element.get(el.id, 0) + 1

        zones = [dict(
            id=z.id, nom=z.name, code=z.code or "", genre=z.genre,
            genre_nom=libelle_zone(z.genre), x=z.x, y=z.y, w=z.w, h=z.h,
            couleur=couleur_zone(z.genre), capacite=z.capacite, occupes=z.occupes,
            elements=len(z.element_ids),
        ) for z in self.zone_ids]

        elements = []
        for el in self.element_ids:
            cible = el._cible()
            elements.append(dict(
                id=el.id, nom=el.display_name, genre=el.genre,
                genre_nom=libelle_element(el.genre),
                x=el.x, y=el.y, w=el.w, h=el.h, rot=int(el.rotation or 0),
                zone=el.zone_id.name if el.zone_id else "",
                zone_id=el.zone_id.id if el.zone_id else False,
                occupant=el.employee_id.name if el.employee_id else "",
                cible=cible, teinte=el._teinte(),
                info=" · ".join(el._infos()),
                liens=liens_par_element.get(el.id, 0),
            ))

        par_id = {e["id"]: e for e in elements}
        liens = []
        for lien in self.lien_ids:
            a, b = par_id.get(lien.src_id.id), par_id.get(lien.dst_id.id)
            if not a or not b:
                continue
            liens.append(dict(
                id=lien.id, src=lien.src_id.id, dst=lien.dst_id.id,
                genre=lien.genre, genre_nom=dict(GENRES_LIEN).get(lien.genre, ""),
                etiquette=lien.name or "",
                points=[[a["x"] + a["w"] / 2.0, a["y"] + a["h"] / 2.0],
                        [b["x"] + b["w"] / 2.0, b["y"] + b["h"] / 2.0]],
            ))

        genres_z = {z["genre"] for z in zones}
        genres_e = {e["genre"] for e in elements}
        genres_l = {li["genre"] for li in liens}
        legende = dict(
            zones=[dict(code=c, nom=n, couleur=f) for c, n, f in GENRES_ZONE
                   if c in genres_z],
            elements=[dict(code=c, nom=n) for c, n, _w, _h in GENRES_ELEMENT
                      if c in genres_e],
            liens=[dict(code=c, nom=n) for c, n in GENRES_LIEN if c in genres_l],
        )
        return dict(
            titre=self.display_name, plan_id=self.id,
            largeur=self.largeur, hauteur=self.profondeur, pas=self.pas,
            places=self.places, occupes=self.occupes,
            zones=zones, elements=elements, liens=liens, legende=legende,
        )

    def rendu(self):
        """Surface RPC volontaire : c'est ce que le composant OWL appelle."""
        self.ensure_one()
        d = self.to_dict()
        surligne = self.env.context.get("bf_floorplan_surligne")
        d.update(
            # le composant lit le gel AVANT d'offrir une poignée
            modifiable=self._modifiable(),
            fige=self.verrouille,
            fond=self._url_fond(),
            surligne=int(surligne) if surligne else False,
            palette=dict(
                zones=[dict(code=c, nom=n) for c, n, _f in GENRES_ZONE],
                elements=[dict(code=c, nom=n, w=w, h=h) for c, n, w, h in GENRES_ELEMENT],
                liens=[dict(code=c, nom=n) for c, n in GENRES_LIEN],
            ),
            vide=_("Téléversez un fond de plan (PNG ou JPEG) dans l'onglet "
                   "« Fond et dimensions », ou posez une première zone.")
            if not (self.zone_ids or self.element_ids or self.fond) else "",
        )
        return d
