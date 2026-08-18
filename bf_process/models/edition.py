# -*- coding: utf-8 -*-
"""Écriture depuis le tracé : le visualiseur devient un éditeur.

Jusqu'ici le composant OWL ne faisait qu'un appel, `rendu()`, et ne rendait
rien. Ce fichier ouvre le chemin inverse — déplacer, créer, supprimer — avec
trois précautions qui sont tout le sujet.

**Le modèle ne stocke pas des pixels.** Un nœud connaît sa colonne
(l'avancement) et sa rangée (le décalage dans son couloir) ; sa position en
points se calcule. Un glisser-déposer arrive donc en coordonnées du tracé et
doit être reconverti en pas de grille, puis arrondi. Sans l'arrondi, chaque
déplacement laisserait un flottant sale dans `col`, la carte dériverait, et les
deux exports s'écarteraient du PDF de référence.

**L'inversion se fait sur la disposition que le client a sous les yeux.** Le
serveur refait `geo.plan()` sur les enregistrements tels quels — donc la même
disposition — avant d'écrire quoi que ce soit. C'est ce qui évite d'avoir à
faire confiance au client sur les décalages.

**Le couloir se rattrape par la bande.** Un nœud lâché dans une autre bande
change de couloir, et sa rangée est recalculée pour ce couloir-là.

⚠️ Une conséquence du modèle, à connaître avant de s'en étonner : la hauteur
d'un couloir est celle de son contenu, et son premier nœud est toujours collé
à sa marge haute. Déplacer verticalement le nœud le plus haut d'un couloir ne
le fait donc pas descendre : ce sont les autres qui remontent. Ce n'est pas un
défaut de l'éditeur, c'est la géométrie du générateur, et l'éditeur la montre
telle quelle plutôt que de la maquiller.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .erreurs import refus_lisible

from ..generateur import geometrie as geo
from .structure import ACTIVITES, KINDS

# Un nœud posé à la main hérite d'un nom qui se voit : mieux vaut une carte qui
# affiche « À nommer » qu'une forme muette qu'on croit finie.
NOMS_PAR_DEFAUT = {
    "task": "À nommer",
    "send": "À nommer",
    "receive": "À nommer",
    "user": "À nommer",
    "sub": "À nommer",
    "note": "À écrire",
}


class BfProcessEdition(models.Model):
    _inherit = "bf.process.diagram"

    pas_grille = fields.Float(
        string="Pas de la grille", default=0.05,
        help="Sur quoi un déplacement se cale, en fraction de colonne et de"
             " rangée. 0,05 est le pas des cartes produites par le générateur.")

    # ------------------------------------------------------------------ garde
    #: modèles réellement écrits par le tracé. Le contrôle porte sur celui que
    #: l'opération touche : sous les droits livrés les deux vont ensemble, mais
    #: une personnalisation qui les sépare ne doit pas laisser passer une
    #: écriture parce qu'un AUTRE modèle, lui, était accessible.
    MODELES_EDITABLES = ("bf.process.node", "bf.process.flow")

    def _modifiable(self):
        """Ni gelée, ni en lecture seule. Lu AVANT d'offrir une poignée.

        Exige les deux modèles : la barre d'outils est tout ou rien, et offrir
        un outil que le serveur refusera au relâchement serait pire que de ne
        pas l'offrir.
        """
        self.ensure_one()
        if self.process_id.state == "valide":
            return False
        return all(self.env[m].has_access("write")
                   for m in self.MODELES_EDITABLES)

    def _exiger_modifiable(self, modele="bf.process.node"):
        """`modele` est celui que l'opération va écrire."""
        self.ensure_one()
        if self.process_id.state == "valide":
            raise UserError(_(
                "« %s v%s » est une version validée : son tracé ne se modifie "
                "plus. Créez la version suivante, ou rouvrez celle-ci."
            ) % (self.process_id.name, self.process_id.version))
        if not self.env[modele].has_access("write"):
            raise UserError(_(
                "Modifier un tracé demande le droit de gestion des cartographies."))

    # ------------------------------------------------- pixels vers la grille
    def _caler(self, valeur):
        """Ramène une valeur sur le pas de la grille."""
        pas = self.pas_grille or 0.05
        return round(round(valeur / pas) * pas, 4)

    def _reperes(self):
        """La disposition actuelle, celle que le client a sous les yeux."""
        self.ensure_one()
        d = self.to_dict()
        pos, lane_geo, pool, ext, noeuds, dx, dy = geo.plan(d)
        return d, pos, lane_geo, pool, dx, dy

    def _couloir_a(self, y, lane_geo, pool, dy):
        """Le couloir dont la bande contient y, le plus proche sinon."""
        bandes = [(code, pool["y0"] + g["y0"] + dy,
                   pool["y0"] + g["y0"] + g["h"] + dy)
                  for code, g in lane_geo.items()]
        if not bandes or bandes[0][0] == "_":
            return None
        for code, haut, bas in bandes:
            if haut <= y <= bas:
                return code
        # lâché hors du pool : le couloir dont le bord est le plus proche
        return min(bandes, key=lambda b: min(abs(y - b[1]), abs(y - b[2])))[0]

    def _inverser(self, cx, cy):
        """Un centre en coordonnées du tracé, vers (colonne, rangée, couloir)."""
        d, pos, lane_geo, pool, dx, dy = self._reperes()
        col_w = d.get("col_w") or 168.0
        row_h = d.get("row_h") or 100.0
        lane_pad = d.get("lane_pad") or 50.0

        code_couloir = self._couloir_a(cy, lane_geo, pool, dy)
        g = lane_geo.get(code_couloir) if code_couloir else None
        if g is None:
            g = list(lane_geo.values())[0]
        haut_bande = pool["y0"] + g["y0"] + lane_pad + dy
        return (self._caler((cx - dx) / col_w),
                self._caler(g["base"] + (cy - haut_bande) / row_h),
                code_couloir)

    # -------------------------------------------------------------- séquences
    def _resequencer(self, enregistrements):
        """Renumérote 10, 20, 30… sans changer l'ordre.

        La séquence n'est pas décorative : l'ordre des enregistrements EST
        l'ordre d'émission dans le XML exporté. Une suppression laisse un trou,
        et un trou finit par se refermer sur une collision.
        """
        for rang, rec in enumerate(enregistrements, 1):
            if rec.sequence != rang * 10:
                rec.sequence = rang * 10

    def _code_libre(self, prefixe, pris):
        n = 1
        while f"{prefixe}{n}" in pris:
            n += 1
        return f"{prefixe}{n}"

    # ------------------------------------------------------------ opérations
    @refus_lisible
    def deplacer_noeud(self, code, cx, cy):
        """Repose un nœud sur la grille. Surface RPC volontaire.

        `cx`, `cy` : le nouveau centre, dans les coordonnées que `rendu()` a
        données au client.
        """
        self.ensure_one()
        self._exiger_modifiable()
        noeud = self.node_ids.filtered(lambda n: n.code == code)
        if not noeud:
            raise UserError(_("Aucun nœud « %s » sur ce niveau.") % code)
        col, row, couloir = self._inverser(cx, cy)
        vals = {"col": col, "row": row}
        if couloir:
            cible = self.lane_ids.filtered(lambda l: l.code == couloir)
            if cible and cible != noeud.lane_id:
                vals["lane_id"] = cible.id
        noeud.write(vals)
        return self.rendu()

    @refus_lisible
    def creer_noeud(self, genre, cx, cy, nom=None):
        """Pose un nœud là où on a cliqué. Surface RPC volontaire.

        Rend le tracé complet, comme les autres opérations. Le client retrouve
        le nouveau nœud par différence avec ce qu'il affichait — c'est une
        ligne de son côté, et ça garde ici un contrat unique.
        """
        self.ensure_one()
        self._exiger_modifiable()
        if genre not in dict(KINDS):
            raise UserError(_("« %s » n'est pas un type de nœud.") % genre)
        col, row, couloir = self._inverser(cx, cy)
        pris = set(self.node_ids.mapped("code"))
        code = self._code_libre("n", pris)
        cible = self.lane_ids.filtered(lambda l: l.code == couloir)
        libelle = nom or NOMS_PAR_DEFAUT.get(genre)
        if not libelle and genre in ACTIVITES:
            libelle = NOMS_PAR_DEFAUT["task"]
        noeud = self.env["bf.process.node"].create({
            "diagram_id": self.id,
            "sequence": (max(self.node_ids.mapped("sequence"), default=0) + 10),
            "lane_id": cible.id if cible else False,
            "code": code,
            "bpmn_id": f"{self.bpmn_id}_{code}",
            "kind": genre,
            "name": libelle or False,
            "col": col, "row": row,
        })
        self._resequencer(self.node_ids.sorted(lambda n: (n.sequence, n.id)))
        self.process_id.message_post(body=Markup(_(
            "Nœud <b>%s</b> ajouté au niveau « %s » depuis le tracé."))
            % (noeud.display_name, self.display_name))
        return self.rendu()

    @refus_lisible
    def supprimer_noeud(self, code):
        """Retire un nœud, et ce qui n'a plus de sens sans lui.

        Surface RPC volontaire. Les flux et les messages qui touchent le nœud
        partent avec lui — c'est la cascade du modèle, pas un ménage improvisé.
        """
        self.ensure_one()
        self._exiger_modifiable()
        noeud = self.node_ids.filtered(lambda n: n.code == code)
        if not noeud:
            raise UserError(_("Aucun nœud « %s » sur ce niveau.") % code)
        if noeud.child_diagram_id:
            raise UserError(_(
                "« %s » ouvre le niveau « %s ». Détachez la page avant de "
                "retirer le sous-processus qui y mène, sinon elle devient "
                "inatteignable sans que rien ne le dise."
            ) % (noeud.display_name, noeud.child_diagram_id.display_name))
        nom = noeud.display_name
        noeud.unlink()
        self._resequencer(self.node_ids.sorted(lambda n: (n.sequence, n.id)))
        self._resequencer(self.flow_ids.sorted(lambda f: (f.sequence, f.id)))
        self.process_id.message_post(body=Markup(_(
            "Nœud <b>%s</b> retiré du niveau « %s » depuis le tracé."))
            % (nom, self.display_name))
        return self.rendu()

    @refus_lisible
    def creer_flux(self, code_source, code_cible):
        """Relie deux nœuds. Surface RPC volontaire."""
        self.ensure_one()
        self._exiger_modifiable("bf.process.flow")
        par_code = {n.code: n for n in self.node_ids}
        source, cible = par_code.get(code_source), par_code.get(code_cible)
        if not source or not cible:
            raise UserError(_("Il faut deux nœuds de ce niveau pour un flux."))
        if source == cible:
            raise UserError(_("Un flux ne peut pas boucler sur lui-même."))
        existant = self.flow_ids.filtered(
            lambda f: f.source_id == source and f.target_id == cible)
        if existant:
            raise UserError(_("Ces deux nœuds sont déjà reliés."))
        # une annotation se relie par une association, jamais par une séquence
        vers_note = "note" in (source.kind, cible.kind)
        pris = {f.bpmn_id for f in self.process_id.mapped("diagram_ids.flow_ids")}
        n = 1
        while f"{self.bpmn_id}_flow{n}" in pris:
            n += 1
        self.env["bf.process.flow"].create({
            "diagram_id": self.id,
            "sequence": (max(self.flow_ids.mapped("sequence"), default=0) + 10),
            "bpmn_id": f"{self.bpmn_id}_flow{n}",
            "source_id": source.id, "target_id": cible.id,
            "routing": "assoc" if vers_note else "auto",
            "anchor_source": "B" if vers_note else False,
            "anchor_target": "T" if vers_note else False,
        })
        self._resequencer(self.flow_ids.sorted(lambda f: (f.sequence, f.id)))
        return self.rendu()

    @refus_lisible
    def supprimer_flux(self, code_source, code_cible):
        """Coupe le lien entre deux nœuds. Surface RPC volontaire."""
        self.ensure_one()
        self._exiger_modifiable("bf.process.flow")
        flux = self.flow_ids.filtered(
            lambda f: f.source_id.code == code_source
            and f.target_id.code == code_cible)
        if not flux:
            raise UserError(_("Ces deux nœuds ne sont pas reliés."))
        flux.unlink()
        self._resequencer(self.flow_ids.sorted(lambda f: (f.sequence, f.id)))
        return self.rendu()

    # ------------------------------------------------------------- palette
    @api.model
    def _palette(self):
        """Ce qu'on peut poser sur une carte, dans l'ordre où on y pense."""
        ordre = ["start", "task", "user", "send", "receive", "sub", "xor",
                 "and", "or", "msgCatch", "timerCatch", "end", "note", "store"]
        libelles = dict(KINDS)
        return [{"code": g, "nom": libelles[g]} for g in ordre if g in libelles]
