# -*- coding: utf-8 -*-
"""L'édition depuis le tracé : chaque geste est une écriture ORM, et le
serveur renvoie le plan entier ensuite. Le composant ne rejoue rien."""
from odoo import _, models
from odoo.exceptions import UserError

from .genres import (GENRES_LIEN, libelle_element, libelle_zone,
                     selection_elements, selection_zones, taille_element)

SORTES = {"zone": "bf.floorplan.zone", "element": "bf.floorplan.element"}


class BfFloorplanEdition(models.Model):
    _inherit = "bf.floorplan"

    # --- géométrie ----------------------------------------------------------

    def _caler(self, valeur):
        """Recale sur la grille du plan."""
        self.ensure_one()
        pas = self.pas or 25.0
        return float(round(round(valeur / pas) * pas, 2))

    def _borner(self, x, y, w, h):
        """Garde la forme dans le plan, en gardant sa taille si elle y tient."""
        self.ensure_one()
        pas = self.pas or 25.0
        w = min(max(w, pas), self.largeur)
        h = min(max(h, pas), self.profondeur)
        x = min(max(x, 0.0), self.largeur - w)
        y = min(max(y, 0.0), self.profondeur - h)
        return x, y, w, h

    def _enregistrement(self, sorte, rid):
        modele = SORTES.get(sorte)
        if not modele:
            raise UserError(_("Sorte de forme inconnue : %s") % sorte)
        rec = self.env[modele].browse(self._entier(rid)).exists()
        if not rec or rec.plan_id != self:
            raise UserError(_("Cette forme n'est pas sur ce plan."))
        return rec

    @staticmethod
    def _entier(valeur):
        try:
            return int(valeur)
        except (TypeError, ValueError):
            raise UserError(_("Identifiant de forme invalide : %s") % (valeur,))

    # --- gestes -------------------------------------------------------------

    def deplacer(self, sorte, rid, x, y):
        """Nouveau coin haut-gauche, en centimètres du plan."""
        self.ensure_one()
        self._exiger_modifiable()
        rec = self._enregistrement(sorte, rid)
        x, y, _w, _h = self._borner(self._caler(x), self._caler(y), rec.w, rec.h)
        rec.write({"x": x, "y": y})
        return self.rendu()

    def redimensionner(self, sorte, rid, w, h):
        self.ensure_one()
        self._exiger_modifiable()
        rec = self._enregistrement(sorte, rid)
        w, h = self._caler(w), self._caler(h)
        pas = self.pas or 25.0
        w = min(max(w, pas), self.largeur - rec.x)
        h = min(max(h, pas), self.profondeur - rec.y)
        rec.write({"w": w, "h": h})
        return self.rendu()

    def tourner(self, rid):
        self.ensure_one()
        self._exiger_modifiable()
        self._enregistrement("element", rid).action_tourner()
        return self.rendu()

    def poser(self, sorte, genre, cx, cy):
        """Pose une forme centrée sur le point cliqué, puis l'annonce."""
        self.ensure_one()
        self._exiger_modifiable()
        if sorte == "zone":
            if genre not in dict(selection_zones()):
                raise UserError(_("Nature de zone inconnue : %s") % genre)
            w, h = 400.0, 300.0
            nom = _("%(nature)s %(n)s") % {
                "nature": libelle_zone(genre),
                "n": len(self.zone_ids.filtered(lambda z: z.genre == genre)) + 1}
        elif sorte == "element":
            if genre not in dict(selection_elements()):
                raise UserError(_("Nature d'élément inconnue : %s") % genre)
            w, h = taille_element(genre)
            nom = _("%(nature)s %(n)s") % {
                "nature": libelle_element(genre),
                "n": len(self.element_ids.filtered(lambda e: e.genre == genre)) + 1}
        else:
            raise UserError(_("Sorte de forme inconnue : %s") % sorte)
        x, y, w, h = self._borner(self._caler(cx - w / 2.0),
                                  self._caler(cy - h / 2.0), w, h)
        vals = {"plan_id": self.id, "name": nom, "genre": genre,
                "x": x, "y": y, "w": w, "h": h}
        rec = self.env[SORTES[sorte]].create(vals)
        d = self.rendu()
        d["neuf"] = {"sorte": sorte, "id": rec.id}
        return d

    def retirer(self, sorte, rid):
        self.ensure_one()
        self._exiger_modifiable()
        rec = self._enregistrement(sorte, rid)
        nom = rec.display_name
        rec.unlink()
        self._message_log(body=_("« %s » retiré du plan.") % nom)
        return self.rendu()

    def lier(self, src, dst, genre="reseau"):
        self.ensure_one()
        self._exiger_modifiable()
        if genre not in dict(GENRES_LIEN):
            raise UserError(_("Nature de lien inconnue : %s") % genre)
        a = self._enregistrement("element", src)
        b = self._enregistrement("element", dst)
        self.env["bf.floorplan.lien"].create({
            "plan_id": self.id, "src_id": a.id, "dst_id": b.id, "genre": genre})
        return self.rendu()

    def delier(self, lien_id):
        self.ensure_one()
        self._exiger_modifiable()
        lien = self.env["bf.floorplan.lien"].browse(self._entier(lien_id)).exists()
        if not lien or lien.plan_id != self:
            raise UserError(_("Ce lien n'est pas sur ce plan."))
        lien.unlink()
        return self.rendu()
