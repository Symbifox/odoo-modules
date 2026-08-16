# -*- coding: utf-8 -*-
"""Rapatrier un `.bpmn` retouché sur une cartographie qui existe déjà.

L'import crée une cartographie neuve : c'est ce qu'il faut la première fois, et
c'est exactement ce qu'il ne faut pas la deuxième. Un fichier repassé par un
éditeur BPMN tiers doit revenir **dans** la carte d'origine, sinon les
discussions tenues sur les étapes, le registre de validation et la lignée des
versions restent derrière, sur un enregistrement que plus personne ne regarde.

Trois choix portent tout le fichier.

**On fusionne par identifiant, jamais par nom.** Un niveau se reconnaît à son
`bpmn_id`, un nœud au sien. C'est la raison d'être de ces champs, et c'est ce
qui permet de dire « cette étape a été renommée » là où un appariement par nom
dirait « une étape a disparu, une autre est apparue ».

**Chaque écart s'arbitre.** Une fusion n'est pas un écrasement : elle propose
une liste d'écarts, chacun avec sa décision. Les retraits arrivent à
« Ignorer » par défaut, et ce n'est pas de la timidité — un éditeur tiers qui
ne réexporte qu'un diagramme sur quinze produit un fichier où quatorze niveaux
« manquent ». Appliquer ces retraits sans les regarder détruirait la carte au
motif qu'un outil n'a pas tout relu.

**Les coordonnées lues sont relatives** (voir `importation`). Elles se
reportent ici sur le repère de la carte visée, par le décalage qui laisse le
plus grand nombre de nœuds là où ils sont. Anticiper sur le minimum aurait
suffi tant que rien ne bouge, et aurait fait glisser toute la page dès que le
nœud le plus à gauche, lui, bouge.
"""
import base64
import hashlib
from collections import Counter

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .erreurs import refus_lisible
from .structure import ACTIVITES, KINDS

from ..generateur import geometrie as geo

PORTEES = [
    ("niveau", "Niveau"),
    ("couloir", "Couloir"),
    ("pool", "Participant externe"),
    ("noeud", "Nœud"),
    ("flux", "Flux"),
    ("message", "Flux de message"),
]
OPERATIONS = [
    ("ajout", "Ajout"),
    ("retrait", "Retrait"),
    ("renommage", "Libellé"),
    ("nature", "Type"),
    ("couloir", "Couloir"),
    ("position", "Position"),
    ("taille", "Taille"),
    ("sens", "Sens"),
]
# ce qui, faute d'être regardé, ne doit surtout pas s'appliquer tout seul
DEFAUT_IGNORE = ("retrait",)
SEP = " › "


def _deux_morceaux(cle):
    """Rouvre une clé composée, sans supposer qu'elle est bien formée.

    Les décisions arbitrées sont des enregistrements que le client peut
    écrire : une clé bricolée ne doit pas sortir en trace de 500, juste ne
    désigner personne.
    """
    morceaux = (cle or "").split(SEP)
    return (morceaux[0], morceaux[1]) if len(morceaux) == 2 else ("", "")


def _cle_flux(src, tgt):
    return f"{src}{SEP}{tgt}"


def _cle_message(noeud, pool):
    return f"{noeud}{SEP}{pool}"


class BfProcessMergeLine(models.TransientModel):
    """Un écart, et ce qu'on a décidé d'en faire."""

    _name = "bf.process.merge.line"
    _description = "Écart proposé par une fusion"
    _order = "wizard_id, sequence, id"

    wizard_id = fields.Many2one(
        "bf.process.merge.wizard", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(string="Ordre", default=10)
    portee = fields.Selection(PORTEES, string="Portée", required=True)
    operation = fields.Selection(OPERATIONS, string="Écart", required=True)
    niveau = fields.Char(string="Niveau")
    niveau_titre = fields.Char(string="Page")
    cle = fields.Char(string="Clé")
    libelle = fields.Char(string="Ce qui change")
    avant = fields.Char(string="Dans la carte")
    apres = fields.Char(string="Dans le fichier")
    decision = fields.Selection(
        [("appliquer", "Appliquer"), ("ignorer", "Ignorer")],
        string="Décision", required=True, default="appliquer")
    remarque = fields.Char(string="Remarque", readonly=True)


class BfProcessMergeWizard(models.TransientModel):
    """Analyse un `.bpmn` contre une cartographie, puis applique ce qu'on retient."""

    _name = "bf.process.merge.wizard"
    _inherit = ["bf.process.lecture"]
    _description = "Fusionner un BPMN 2.0"

    process_id = fields.Many2one(
        "bf.process", string="Cartographie", required=True,
        help="La carte dans laquelle le fichier revient.")
    fichier = fields.Binary(string="Fichier .bpmn", required=True)
    nom_fichier = fields.Char(string="Nom du fichier")
    etat = fields.Selection(
        [("depot", "Dépôt"), ("arbitrage", "Arbitrage")],
        default="depot", required=True)
    line_ids = fields.One2many(
        "bf.process.merge.line", "wizard_id", string="Écarts")
    # `sanitize` reste actif, et chaque valeur interpolée est échappée : les
    # libellés viennent d'un fichier tiers, ils ne sont pas sûrs.
    resume_html = fields.Html(string="Ce que dit le fichier", readonly=True)
    empreinte = fields.Char(readonly=True)
    ecart_count = fields.Integer(compute="_compute_compte")
    retenu_count = fields.Integer(compute="_compute_compte")

    @api.depends("line_ids", "line_ids.decision")
    def _compute_compte(self):
        for rec in self:
            rec.ecart_count = len(rec.line_ids)
            rec.retenu_count = len(rec.line_ids.filtered(
                lambda l: l.decision == "appliquer"))

    # ================================================================ analyse
    @refus_lisible
    def action_analyser(self):
        """Lit le fichier, le confronte à la carte, et propose les écarts."""
        self.ensure_one()
        cible = self.process_id
        self._exiger_modifiable(cible)
        diagrammes = self._lire_fichier(
            base64.b64decode(self.fichier or b""), grilles=self._grilles(cible))

        connus = {d.bpmn_id for d in cible.diagram_ids}
        lus = {d.get("bpmn_id") for d in diagrammes if d.get("bpmn_id")}
        if not (connus & lus):
            raise UserError(_(
                "Aucun niveau de ce fichier ne correspond à un niveau de "
                "« %(carte)s ». Les identifiants ont probablement été "
                "renommés par l'éditeur qui l'a produit, ou le fichier vient "
                "d'une autre cartographie. Fusionner ici retirerait tout pour "
                "tout remettre, sans conserver ni les discussions ni le "
                "registre de validation : importez-le plutôt comme une "
                "nouvelle cartographie.",
                carte=cible.display_name))

        self.line_ids.unlink()
        lignes = self._ecarts(cible, diagrammes)
        self.write({
            "line_ids": [(0, 0, v) for v in lignes],
            "empreinte": self._empreinte(cible),
            "resume_html": self._resume(cible, diagrammes, lignes),
            "etat": "arbitrage",
        })
        return self._revenir()

    #: tout ce qu'une fusion peut écrire. Le contrôle porte sur l'ensemble, et
    #: pas seulement sur les nœuds : une fusion qui appliquerait ses ajouts de
    #: nœuds pour buter ensuite sur les couloirs laisserait la carte à moitié
    #: écrite, ce qui est pire que de l'avoir refusée d'entrée.
    MODELES_ECRITS = ("bf.process.diagram", "bf.process.lane", "bf.process.pool",
                      "bf.process.node", "bf.process.flow", "bf.process.message")

    def _exiger_modifiable(self, cible):
        if cible.state == "valide":
            raise UserError(_(
                "« %s » est une version validée : elle ne se modifie plus. "
                "Créez la version suivante, ou rouvrez celle-ci, avant de "
                "fusionner un fichier dedans.") % cible.display_name)
        if not all(self.env[m].has_access("write") for m in self.MODELES_ECRITS):
            raise UserError(_(
                "Fusionner un fichier demande le droit de gestion des "
                "cartographies."))

    @staticmethod
    def _grilles(cible):
        """Le pas connu de chaque niveau, pour ne pas le redéduire faux."""
        return {d.bpmn_id: (d.col_w or 168.0, d.row_h or 100.0,
                            d.lane_pad or 50.0)
                for d in cible.diagram_ids}

    # ---------------------------------------------------------------- index
    @staticmethod
    def _index_carte(cible):
        """Aplati la carte stockée, indexée par identifiant BPMN de niveau.

        La comparaison de versions (`comparaison`) indexe par `code` de niveau,
        parce qu'elle rapproche deux versions issues l'une de l'autre, où les
        deux coïncident. Ici la clé vient d'un fichier, et ce que le fichier
        porte, c'est le `bpmn_id`.
        """
        niveaux, couloirs, pools, noeuds, flux, msgs = {}, {}, {}, {}, {}, {}
        for d in cible.diagram_ids:
            niveaux[d.bpmn_id] = d
            for ln in d.lane_ids:
                couloirs[(d.bpmn_id, ln.code)] = ln
            for p in d.pool_ids:
                pools[(d.bpmn_id, p.code)] = p
            for n in d.node_ids:
                noeuds[(d.bpmn_id, n.code)] = n
            for f in d.flow_ids:
                flux[(d.bpmn_id, _cle_flux(f.source_id.code, f.target_id.code))] = f
            for m in d.message_ids:
                msgs[(d.bpmn_id, _cle_message(m.node_id.code, m.pool_id.code))] = m
        return niveaux, couloirs, pools, noeuds, flux, msgs

    @staticmethod
    def _index_fichier(diagrammes):
        """Le même aplatissement, côté fichier."""
        niveaux, couloirs, pools, noeuds, flux, msgs = {}, {}, {}, {}, {}, {}
        for d in diagrammes:
            code = d.get("bpmn_id")
            if not code:
                continue
            niveaux[code] = d
            for ln in d.get("lanes") or []:
                couloirs[(code, ln["id"])] = ln
            for p in d.get("ext") or []:
                pools[(code, p["id"])] = p
            for n in d.get("nodes") or []:
                noeuds[(code, n["id"])] = n
            for f in d.get("flows") or []:
                flux[(code, _cle_flux(f["src"], f["tgt"]))] = f
            for m in d.get("msgs") or []:
                msgs[(code, _cle_message(m["node"], m["pool"]))] = m
        return niveaux, couloirs, pools, noeuds, flux, msgs

    # ------------------------------------------------------------- ancrage
    @staticmethod
    def _mode(paires):
        """Le décalage qui laisse le plus de nœuds là où ils sont.

        Départager les ex æquo par la valeur, et pas par l'ordre d'arrivée :
        une fusion doit proposer deux fois la même chose sur le même fichier.
        """
        if not paires:
            return None
        compte = Counter(round(stocke - lu, 3) for stocke, lu in paires)
        return min(compte.items(), key=lambda kv: (-kv[1], kv[0]))[0]

    @staticmethod
    def _premiers_couloirs(niveau, d_fichier):
        """Le premier couloir de chaque côté — celui qui sert de défaut."""
        lanes = d_fichier.get("lanes") or []
        return (niveau.lane_ids[:1].code or "",
                lanes[0]["id"] if lanes else "")

    @staticmethod
    def _couloir_effectif(code, premier):
        """Le couloir dans lequel le tracé range réellement ce nœud.

        Un nœud sans couloir n'est pas hors des couloirs : le moteur de
        géométrie comme l'exportateur le rangent dans le premier. Un `lane_id`
        vide et « premier couloir » dessinent donc rigoureusement la même
        chose. Les distinguer faisait lire cent un changements de couloir sur
        une carte où pas un nœud n'avait bougé — et, pire, privait le calage
        des rangées de ses points de repère.
        """
        return code or premier

    def _ancrage(self, niveau, d_fichier, noeuds_carte):
        """Reporte les coordonnées relatives du fichier sur le repère du niveau.

        Rend `(dcol, drow_par_couloir, pas, premier_couloir_lu)`. Sans nœud
        commun — un niveau entier ajouté — il n'y a rien à recaler : le fichier
        fait foi tel quel.
        """
        rec_premier, f_premier = self._premiers_couloirs(niveau, d_fichier)
        communs = []
        par_couloir = {}
        for n in d_fichier.get("nodes") or []:
            rec = noeuds_carte.get((niveau.bpmn_id, n["id"]))
            if not rec:
                continue
            communs.append((rec.col, n["col"]))
            ici = self._couloir_effectif(rec.lane_id.code or "", rec_premier)
            la = self._couloir_effectif(n.get("lane") or "", f_premier)
            # une rangée se compte dans son couloir : un nœud qui en change
            # ne dit rien du calage, ni de l'ancien ni du nouveau
            if ici == la:
                par_couloir.setdefault(la, []).append((rec.row, n["row"]))
        return (self._mode(communs) or 0.0,
                {k: (self._mode(v) or 0.0) for k, v in par_couloir.items()},
                niveau.pas_grille or 0.05, f_premier)

    @staticmethod
    def _caler(valeur, pas):
        return round(round(valeur / pas) * pas, 4)

    @staticmethod
    def _dire_boite(boite):
        return "%g × %g" % boite

    @classmethod
    def _boites_differentes(cls, rec, n):
        """La forme dessinée a-t-elle changé de taille ?

        Le fichier ne porte pas la distinction entre « taille par défaut » et
        « surcharge qui vaut la taille par défaut » : les deux y sortent avec
        les mêmes bornes. C'est donc la boîte calculée qu'il faut comparer.
        """
        ici, la = geo.node_box(rec.to_dict()), geo.node_box(n)
        return abs(ici[0] - la[0]) > 0.5 or abs(ici[1] - la[1]) > 0.5

    def _pose(self, n, ancrage):
        """La colonne et la rangée que ce nœud du fichier prendrait."""
        dcol, drows, pas, f_premier = ancrage
        drow = drows.get(self._couloir_effectif(n.get("lane") or "", f_premier), 0.0)
        return (self._caler(n["col"] + dcol, pas),
                self._caler(n["row"] + drow, pas))

    # ------------------------------------------------------------- écarts
    def _ecarts(self, cible, diagrammes):
        """La liste des écarts, dans l'ordre où on veut les lire."""
        c_niv, c_lane, c_pool, c_noeud, c_flux, c_msg = self._index_carte(cible)
        f_niv, f_lane, f_pool, f_noeud, f_flux, f_msg = self._index_fichier(diagrammes)
        lignes = []

        def ajoute(portee, operation, niveau, cle, libelle,
                   avant="", apres="", remarque=""):
            lignes.append({
                "sequence": (len(lignes) + 1) * 10,
                "portee": portee, "operation": operation,
                "niveau": niveau,
                "niveau_titre": (c_niv[niveau].display_name if niveau in c_niv
                                 else (f_niv.get(niveau) or {}).get("title", "")),
                "cle": cle, "libelle": libelle,
                "avant": avant, "apres": apres, "remarque": remarque,
                "decision": ("ignorer" if operation in DEFAUT_IGNORE
                             else "appliquer"),
            })

        genres = dict(KINDS)

        # --- niveaux ---------------------------------------------------------
        for code in sorted(set(f_niv) - set(c_niv)):
            d = f_niv[code]
            ajoute("niveau", "ajout", code, "",
                   _("Nouvelle page « %(titre)s » (%(n)d nœud(s))",
                     titre=d["title"], n=len(d.get("nodes") or [])),
                   apres=d["title"])
        for code in sorted(set(c_niv) - set(f_niv)):
            d = c_niv[code]
            ajoute("niveau", "retrait", code, "",
                   _("La page « %(titre)s » n'est pas dans le fichier",
                     titre=d.title),
                   avant=d.title,
                   remarque=_("Retirer une page retire ses nœuds et leurs "
                              "discussions."))
        for code in sorted(set(c_niv) & set(f_niv)):
            # On compare le nom du diagramme, pas le titre seul : c'est ce que
            # l'export écrit, des deux côtés, sans découpage à deviner. Un
            # titre qui contient lui-même un tiret cadratin — il y en a — ne se
            # relit pas de façon fiable une fois recollé au libellé de niveau.
            ici = " — ".join(filter(None, (c_niv[code].level, c_niv[code].title)))
            la = f_niv[code].get("nom_diagramme") or f_niv[code]["title"]
            if ici != la:
                ajoute("niveau", "renommage", code, "",
                       _("Titre de la page"), avant=ici, apres=la)

        communs = sorted(set(c_niv) & set(f_niv))

        # --- couloirs et participants externes --------------------------------
        for famille, dedans, dehors, nom_rec, nom_dict in (
                ("couloir", c_lane, f_lane, "name", "name"),
                ("pool", c_pool, f_pool, "name", "name")):
            for niv, code in sorted(k for k in set(dehors) - set(dedans)
                                    if k[0] in communs):
                ajoute(famille, "ajout", niv, code,
                       _("Nouveau « %s »") % dehors[(niv, code)][nom_dict],
                       apres=dehors[(niv, code)][nom_dict])
            for niv, code in sorted(k for k in set(dedans) - set(dehors)
                                    if k[0] in communs):
                ajoute(famille, "retrait", niv, code,
                       _("« %s » n'est pas dans le fichier")
                       % dedans[(niv, code)][nom_rec],
                       avant=dedans[(niv, code)][nom_rec])
            for niv, code in sorted(k for k in set(dedans) & set(dehors)
                                    if k[0] in communs):
                a, b = dedans[(niv, code)], dehors[(niv, code)]
                if (a[nom_rec] or "") != (b[nom_dict] or ""):
                    ajoute(famille, "renommage", niv, code, _("Libellé"),
                           avant=a[nom_rec], apres=b[nom_dict])
        for niv, code in sorted(k for k in set(c_pool) & set(f_pool)
                                if k[0] in communs):
            a, b = c_pool[(niv, code)], f_pool[(niv, code)]
            if a.position != b.get("pos", "top"):
                ajoute("pool", "sens", niv, code, _("Côté du participant"),
                       avant=a.position, apres=b.get("pos", "top"))

        # --- nœuds -----------------------------------------------------------
        ancrages = {code: self._ancrage(c_niv[code], f_niv[code], c_noeud)
                    for code in communs}

        for niv, cle in sorted(k for k in set(f_noeud) - set(c_noeud)
                               if k[0] in communs):
            n = f_noeud[(niv, cle)]
            ajoute("noeud", "ajout", niv, cle,
                   _("Nouveau %(genre)s « %(nom)s »",
                     genre=genres.get(n["kind"], n["kind"]).lower(),
                     nom=n.get("name") or cle),
                   apres=n.get("name") or cle)
        for niv, cle in sorted(k for k in set(c_noeud) - set(f_noeud)
                               if k[0] in communs):
            n = c_noeud[(niv, cle)]
            remarque = ""
            if n.child_diagram_id:
                remarque = _("Ce sous-processus ouvre « %s » : la page "
                             "deviendrait inatteignable.") \
                    % n.child_diagram_id.display_name
            elif n.message_ids.filtered(lambda m: m.message_type == "comment"):
                remarque = _("Cette étape porte une discussion.")
            ajoute("noeud", "retrait", niv, cle,
                   _("« %s » n'est pas dans le fichier") % (n.name or cle),
                   avant=n.name or cle, remarque=remarque)

        for niv, cle in sorted(k for k in set(c_noeud) & set(f_noeud)
                               if k[0] in communs):
            rec, n = c_noeud[(niv, cle)], f_noeud[(niv, cle)]
            ancrage = ancrages[niv]
            pas, f_premier = ancrage[2], ancrage[3]
            rec_premier = c_niv[niv].lane_ids[:1].code or ""
            if (rec.name or "") != (n.get("name") or ""):
                remarque = ""
                if rec.child_diagram_id:
                    remarque = _("Le lien vers « %s » est conservé malgré le "
                                 "changement de nom.") \
                        % rec.child_diagram_id.display_name
                ajoute("noeud", "renommage", niv, cle, _("Libellé"),
                       avant=rec.name or "", apres=n.get("name") or "",
                       remarque=remarque)
            if rec.kind != n["kind"]:
                ajoute("noeud", "nature", niv, cle, _("Type"),
                       avant=genres.get(rec.kind, rec.kind),
                       apres=genres.get(n["kind"], n["kind"]))
            ici = self._couloir_effectif(rec.lane_id.code or "", rec_premier)
            la = self._couloir_effectif(n.get("lane") or "", f_premier)
            if ici != la:
                ajoute("noeud", "couloir", niv, cle, _("Couloir"),
                       avant=rec.lane_id.name or "—",
                       apres=(f_lane.get((niv, la)) or {}).get("name") or "—")
            col, row = self._pose(n, ancrage)
            if abs(col - rec.col) > pas / 2 or abs(row - rec.row) > pas / 2:
                ajoute("noeud", "position", niv, cle, _("Position"),
                       avant=f"{rec.col:g} / {rec.row:g}",
                       apres=f"{col:g} / {row:g}")
            # La taille se compare sur la boîte DESSINÉE, pas sur le champ de
            # surcharge. Une surcharge qui vaut exactement la taille naturelle
            # ne ressort pas du fichier — elle n'y a pas d'existence séparée —
            # et la comparer à un champ rempli faisait passer dix-neuf formes
            # pour redimensionnées alors qu'elles occupaient le même espace.
            if self._boites_differentes(rec, n):
                ajoute("noeud", "taille", niv, cle, _("Taille"),
                       avant=self._dire_boite(geo.node_box(rec.to_dict())),
                       apres=self._dire_boite(geo.node_box(n)))

        # --- flux et messages -------------------------------------------------
        for niv, cle in sorted(k for k in set(f_flux) - set(c_flux)
                               if k[0] in communs):
            ajoute("flux", "ajout", niv, cle, _("Nouveau lien %s") % cle,
                   apres=f_flux[(niv, cle)].get("label") or "")
        for niv, cle in sorted(k for k in set(c_flux) - set(f_flux)
                               if k[0] in communs):
            ajoute("flux", "retrait", niv, cle,
                   _("Le lien %s n'est pas dans le fichier") % cle,
                   avant=c_flux[(niv, cle)].label or "")
        for niv, cle in sorted(k for k in set(c_flux) & set(f_flux)
                               if k[0] in communs):
            rec, f = c_flux[(niv, cle)], f_flux[(niv, cle)]
            if (rec.label or "") != (f.get("label") or ""):
                ajoute("flux", "renommage", niv, cle, _("Porte de %s") % cle,
                       avant=rec.label or "—", apres=f.get("label") or "—")

        for niv, cle in sorted(k for k in set(f_msg) - set(c_msg)
                               if k[0] in communs):
            ajoute("message", "ajout", niv, cle,
                   _("Nouveau message « %s »") % (f_msg[(niv, cle)].get("label") or ""),
                   apres=f_msg[(niv, cle)].get("label") or "")
        for niv, cle in sorted(k for k in set(c_msg) - set(f_msg)
                               if k[0] in communs):
            ajoute("message", "retrait", niv, cle,
                   _("Le message « %s » n'est pas dans le fichier")
                   % (c_msg[(niv, cle)].label or ""),
                   avant=c_msg[(niv, cle)].label or "")
        for niv, cle in sorted(k for k in set(c_msg) & set(f_msg)
                               if k[0] in communs):
            rec, m = c_msg[(niv, cle)], f_msg[(niv, cle)]
            if (rec.label or "") != (m.get("label") or ""):
                ajoute("message", "renommage", niv, cle, _("Libellé du message"),
                       avant=rec.label or "", apres=m.get("label") or "")
            if rec.direction != m.get("dir", "out"):
                ajoute("message", "sens", niv, cle, _("Sens du message"),
                       avant=rec.direction, apres=m.get("dir", "out"))
        return lignes

    # ------------------------------------------------------------- résumé
    def _resume(self, cible, diagrammes, lignes):
        """Ce que le fichier dit avant même qu'on regarde les écarts."""
        connus = {d.bpmn_id for d in cible.diagram_ids}
        lus = {d.get("bpmn_id") for d in diagrammes if d.get("bpmn_id")}
        blocs = [Markup("<p>Fichier <b>%s</b> — %s page(s) lue(s), "
                        "%s en commun avec <b>%s</b>.</p>") % (
            self.nom_fichier or _("sans nom"), len(diagrammes),
            len(connus & lus), cible.display_name)]
        manquantes = connus - lus
        if manquantes:
            titres = ", ".join(sorted(
                d.title for d in cible.diagram_ids if d.bpmn_id in manquantes))
            blocs.append(Markup(
                "<div class='alert alert-warning' role='alert'>"
                "<b>%s page(s) de la carte ne sont pas dans le fichier</b> :"
                " %s.<br/>Plusieurs éditeurs ne réexportent que le diagramme"
                " ouvert. Les retraits sont donc proposés à « Ignorer » :"
                " ne les appliquez que si la page a vraiment été supprimée."
                "</div>") % (len(manquantes), titres))
        if not lignes:
            blocs.append(Markup("<p>%s</p>") % _(
                "Aucun écart : le fichier dit exactement ce que la carte dit "
                "déjà."))
        return Markup("").join(blocs)

    @staticmethod
    def _empreinte(cible):
        """De quoi refuser d'appliquer une analyse devenue fausse.

        Entre l'analyse et l'application, la carte a pu bouger — une autre
        session, ou l'éditeur dans un second onglet. Appliquer alors des écarts
        calculés contre un état qui n'existe plus écrirait n'importe quoi.
        """
        graine = []
        for d in cible.diagram_ids.sorted(lambda x: x.bpmn_id):
            graine.append(f"{d.bpmn_id}|{d.title}")
            for n in d.node_ids.sorted(lambda x: x.code):
                graine.append(f" n{n.code}|{n.kind}|{n.name or ''}|"
                              f"{n.col:g}|{n.row:g}|{n.lane_id.code or ''}")
            for f in d.flow_ids.sorted(lambda x: (x.source_id.code, x.target_id.code)):
                graine.append(f" f{f.source_id.code}>{f.target_id.code}|{f.label or ''}")
        return hashlib.sha256("\n".join(graine).encode("utf-8")).hexdigest()

    # ============================================================ application
    def action_tout_appliquer(self):
        self.ensure_one()
        self.line_ids.decision = "appliquer"
        return self._revenir()

    def action_tout_ignorer(self):
        self.ensure_one()
        self.line_ids.decision = "ignorer"
        return self._revenir()

    def action_retraits_ignorer(self):
        self.ensure_one()
        self.line_ids.filtered(
            lambda l: l.operation == "retrait").decision = "ignorer"
        return self._revenir()

    @refus_lisible
    def action_appliquer(self):
        """Écrit ce qui a été retenu, et rien d'autre."""
        self.ensure_one()
        cible = self.process_id
        self._exiger_modifiable(cible)
        if self.empreinte and self.empreinte != self._empreinte(cible):
            raise UserError(_(
                "« %s » a changé depuis l'analyse. Relancez l'analyse : les "
                "écarts affichés ont été calculés contre un état qui n'existe "
                "plus.") % cible.display_name)
        retenues = self.line_ids.filtered(lambda l: l.decision == "appliquer")
        if not retenues:
            raise UserError(_("Aucun écart retenu : il n'y a rien à écrire."))

        diagrammes = self._lire_fichier(
            base64.b64decode(self.fichier or b""), grilles=self._grilles(cible))
        f_niv, f_lane, f_pool, f_noeud, f_flux, f_msg = self._index_fichier(diagrammes)
        fait, refuse = [], []

        # L'ancrage se fige AVANT la première écriture. Le recalculer en cours
        # de route le ferait dépendre de ce qui vient d'être écrit : un nœud
        # ajouté plus à gauche que tous les autres déplacerait le repère, et
        # les nœuds suivants atterriraient décalés du premier.
        ancrages = {
            niveau.bpmn_id: self._ancrage(
                niveau, f_niv[niveau.bpmn_id],
                {(niveau.bpmn_id, x.code): x for x in niveau.node_ids})
            for niveau in cible.diagram_ids if niveau.bpmn_id in f_niv}

        def par(portee, operation):
            return retenues.filtered(
                lambda l: l.portee == portee and l.operation == operation)

        # 1. les pages neuves d'abord : tout le reste peut s'y accrocher
        for ligne in par("niveau", "ajout"):
            d = f_niv.get(ligne.niveau)
            if d:
                self._creer_niveau(cible, d)
                fait.append(ligne)

        c_niv = {d.bpmn_id: d for d in cible.diagram_ids}

        # 2. couloirs et participants externes
        for ligne in par("couloir", "ajout"):
            niveau = c_niv.get(ligne.niveau)
            ln = f_lane.get((ligne.niveau, ligne.cle))
            if niveau and ln:
                self.env["bf.process.lane"].create({
                    "diagram_id": niveau.id, "code": ln["id"], "name": ln["name"],
                    "sequence": (max(niveau.lane_ids.mapped("sequence"),
                                     default=0) + 10)})
                fait.append(ligne)
        for ligne in par("pool", "ajout"):
            niveau = c_niv.get(ligne.niveau)
            p = f_pool.get((ligne.niveau, ligne.cle))
            if niveau and p:
                self.env["bf.process.pool"].create({
                    "diagram_id": niveau.id, "code": p["id"], "name": p["name"],
                    "position": p.get("pos", "top"),
                    "sequence": (max(niveau.pool_ids.mapped("sequence"),
                                     default=0) + 10)})
                fait.append(ligne)

        # 3. nœuds neufs, puis les modifications de nœuds
        for ligne in par("noeud", "ajout"):
            niveau = c_niv.get(ligne.niveau)
            n = f_noeud.get((ligne.niveau, ligne.cle))
            if niveau and n:
                self._creer_noeud(niveau, n, ancrages.get(niveau.bpmn_id))
                fait.append(ligne)

        for ligne in retenues.filtered(
                lambda l: l.portee == "noeud"
                and l.operation in ("renommage", "nature", "couloir",
                                    "position", "taille")):
            niveau = c_niv.get(ligne.niveau)
            n = f_noeud.get((ligne.niveau, ligne.cle))
            rec = niveau and niveau.node_ids.filtered(
                lambda x: x.code == ligne.cle)
            if not (niveau and n and rec):
                continue
            self._modifier_noeud(niveau, rec, n, ligne.operation,
                                 ancrages.get(niveau.bpmn_id))
            fait.append(ligne)

        # 4. liens et messages neufs
        for ligne in par("flux", "ajout"):
            niveau = c_niv.get(ligne.niveau)
            f = f_flux.get((ligne.niveau, ligne.cle))
            if niveau and f and not self._creer_flux(niveau, f):
                refuse.append((ligne, _("un des deux nœuds n'existe pas ici")))
            elif niveau and f:
                fait.append(ligne)
        for ligne in par("message", "ajout"):
            niveau = c_niv.get(ligne.niveau)
            m = f_msg.get((ligne.niveau, ligne.cle))
            if niveau and m and not self._creer_message(niveau, m):
                refuse.append((ligne, _("le nœud ou le participant manque ici")))
            elif niveau and m:
                fait.append(ligne)

        # 5. modifications de couloirs, participants, liens et messages
        for ligne in retenues.filtered(
                lambda l: l.portee in ("couloir", "pool", "flux", "message")
                and l.operation in ("renommage", "sens")):
            if self._modifier_annexe(c_niv, ligne, f_lane, f_pool, f_flux, f_msg):
                fait.append(ligne)

        for ligne in par("niveau", "renommage"):
            niveau = c_niv.get(ligne.niveau)
            if niveau:
                niveau.title = f_niv[ligne.niveau]["title"]
                fait.append(ligne)

        # 6. les retraits en dernier, du plus fin au plus gros
        for portee in ("message", "flux", "noeud", "couloir", "pool", "niveau"):
            for ligne in par(portee, "retrait"):
                motif = self._retirer(c_niv, portee, ligne)
                if motif is True:
                    fait.append(ligne)
                else:
                    refuse.append((ligne, motif))

        # 7. raccorder les sous-processus neufs à leur page, sans toucher aux liens
        #    existants : c'est le renommage dans un éditeur tiers qui casse ce
        #    lien, et le conserver tel quel est justement ce qu'on veut.
        orphelins = self._raccorder(cible)

        for niveau in cible.diagram_ids:
            niveau._resequencer(niveau.node_ids.sorted(lambda n: (n.sequence, n.id)))
            niveau._resequencer(niveau.flow_ids.sorted(lambda f: (f.sequence, f.id)))

        cible.message_post(body=self._compte_rendu(fait, refuse, orphelins))
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.process",
            "res_id": cible.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------- écritures
    def _creer_niveau(self, cible, d):
        niveau = self.env["bf.process.diagram"].create({
            "process_id": cible.id,
            "sequence": (max(cible.diagram_ids.mapped("sequence"), default=0) + 10),
            "title": d["title"], "level": d.get("level"),
            "code": self._code_libre_niveau(cible, d),
            "bpmn_id": d["bpmn_id"],
            "pool_name": d["pool"] if d["pool"] != cible.pool_name else False,
            "flat": bool(d.get("flat")),
            "col_w": d.get("col_w", 168.0), "row_h": d.get("row_h", 100.0),
            "lane_pad": d.get("lane_pad", 50.0),
            "ext_header": d.get("ext_header") or 0.0,
        })
        cible._charger_niveau(niveau, d)
        return niveau

    @staticmethod
    def _code_libre_niveau(cible, d):
        """Le code du fichier, sauf s'il est déjà pris dans cette carte."""
        pris = set(cible.diagram_ids.mapped("code"))
        base = d.get("code") or d["bpmn_id"]
        if base not in pris:
            return base
        n = 2
        while f"{base}-{n}" in pris:
            n += 1
        return f"{base}-{n}"

    def _ancrage_neutre(self, niveau):
        """Aucun nœud commun : le fichier se pose tel qu'il se lit."""
        return (0.0, {}, niveau.pas_grille or 0.05, "")

    def _creer_noeud(self, niveau, n, ancrage):
        couloir = niveau.lane_ids.filtered(lambda l: l.code == n.get("lane"))
        col, row = self._pose(n, ancrage or self._ancrage_neutre(niveau))
        nom = n.get("name") or ""
        if not nom and n["kind"] in ACTIVITES:
            # une activité doit être nommée : mieux vaut le dire sur la carte
            # que refuser la fusion entière pour un libellé vide
            nom = _("À nommer")
        if not nom and n["kind"] == "note":
            nom = _("À écrire")
        return self.env["bf.process.node"].create({
            "diagram_id": niveau.id,
            "sequence": (max(niveau.node_ids.mapped("sequence"), default=0) + 10),
            "lane_id": couloir.id if couloir else False,
            "code": n["id"], "bpmn_id": f'{niveau.bpmn_id}_{n["id"]}',
            "name": nom or False, "kind": n["kind"],
            "tone": n.get("tone") or False,
            "col": col, "row": row,
            "width": n.get("w", 0.0), "height": n.get("h", 0.0),
        })

    def _modifier_noeud(self, niveau, rec, n, operation, ancrage):
        if operation == "renommage":
            rec.name = n.get("name") or False
        elif operation == "nature":
            rec.kind = n["kind"]
        elif operation == "couloir":
            couloir = niveau.lane_ids.filtered(lambda l: l.code == n.get("lane"))
            rec.lane_id = couloir.id if couloir else False
        elif operation == "position":
            col, row = self._pose(n, ancrage or self._ancrage_neutre(niveau))
            rec.write({"col": col, "row": row})
        elif operation == "taille":
            rec.write({"width": n.get("w", 0.0), "height": n.get("h", 0.0)})

    def _creer_flux(self, niveau, f):
        par_code = {x.code: x for x in niveau.node_ids}
        src, tgt = par_code.get(f["src"]), par_code.get(f["tgt"])
        if not src or not tgt or src == tgt:
            return False
        pris = {x.bpmn_id for x in niveau.process_id.mapped("diagram_ids.flow_ids")}
        n = 1
        while f"{niveau.bpmn_id}_flow{n}" in pris:
            n += 1
        vers_note = "note" in (src.kind, tgt.kind)
        self.env["bf.process.flow"].create({
            "diagram_id": niveau.id,
            "sequence": (max(niveau.flow_ids.mapped("sequence"), default=0) + 10),
            "bpmn_id": f"{niveau.bpmn_id}_flow{n}",
            "source_id": src.id, "target_id": tgt.id,
            "label": f.get("label") or False,
            "routing": "assoc" if vers_note else (f.get("r") or "auto"),
            "anchor_source": "B" if vers_note else False,
            "anchor_target": "T" if vers_note else False,
        })
        return True

    def _creer_message(self, niveau, m):
        noeud = niveau.node_ids.filtered(lambda x: x.code == m["node"])
        pool = niveau.pool_ids.filtered(lambda x: x.code == m["pool"])
        if not noeud or not pool:
            return False
        pris = {x.bpmn_id for x in niveau.process_id.mapped("diagram_ids.message_ids")}
        n = 1
        while f"{niveau.bpmn_id}_msg{n}" in pris:
            n += 1
        self.env["bf.process.message"].create({
            "diagram_id": niveau.id,
            "sequence": (max(niveau.message_ids.mapped("sequence"), default=0) + 10),
            "bpmn_id": f"{niveau.bpmn_id}_msg{n}",
            "node_id": noeud.id, "pool_id": pool.id,
            "direction": m.get("dir", "out"),
            "label": m.get("label") or "?",
        })
        return True

    def _modifier_annexe(self, c_niv, ligne, f_lane, f_pool, f_flux, f_msg):
        niveau = c_niv.get(ligne.niveau)
        if not niveau:
            return False
        cle = (ligne.niveau, ligne.cle)
        if ligne.portee == "couloir":
            rec = niveau.lane_ids.filtered(lambda x: x.code == ligne.cle)
            if rec and cle in f_lane:
                rec.name = f_lane[cle]["name"]
                return True
        elif ligne.portee == "pool":
            rec = niveau.pool_ids.filtered(lambda x: x.code == ligne.cle)
            if rec and cle in f_pool:
                if ligne.operation == "sens":
                    rec.position = f_pool[cle].get("pos", "top")
                else:
                    rec.name = f_pool[cle]["name"]
                return True
        elif ligne.portee == "flux":
            src, tgt = _deux_morceaux(ligne.cle)
            rec = niveau.flow_ids.filtered(
                lambda x: x.source_id.code == src and x.target_id.code == tgt)
            if rec and cle in f_flux:
                rec.label = f_flux[cle].get("label") or False
                return True
        elif ligne.portee == "message":
            noeud, pool = _deux_morceaux(ligne.cle)
            rec = niveau.message_ids.filtered(
                lambda x: x.node_id.code == noeud and x.pool_id.code == pool)
            if rec and cle in f_msg:
                if ligne.operation == "sens":
                    rec.direction = f_msg[cle].get("dir", "out")
                else:
                    rec.label = f_msg[cle].get("label") or "?"
                return True
        return False

    def _retirer(self, c_niv, portee, ligne):
        """Rend True, ou le motif du refus."""
        niveau = c_niv.get(ligne.niveau)
        if not niveau:
            return _("la page n'existe plus")
        if portee == "niveau":
            if niveau.parent_node_ids:
                return _("cette page est ouverte depuis « %s »") \
                    % niveau.parent_node_ids[0].display_name
            niveau.unlink()
            return True
        if portee == "noeud":
            rec = niveau.node_ids.filtered(lambda x: x.code == ligne.cle)
            if not rec:
                return _("le nœud n'existe plus")
            if rec.child_diagram_id:
                return _("il ouvre la page « %s »") \
                    % rec.child_diagram_id.display_name
            rec.unlink()
            return True
        if portee == "couloir":
            rec = niveau.lane_ids.filtered(lambda x: x.code == ligne.cle)
            if rec and rec.node_ids:
                return _("ce couloir porte encore %d nœud(s)") % len(rec.node_ids)
            rec.unlink()
            return True
        if portee == "pool":
            rec = niveau.pool_ids.filtered(lambda x: x.code == ligne.cle)
            rec.unlink()
            return True
        if portee == "flux":
            src, tgt = _deux_morceaux(ligne.cle)
            niveau.flow_ids.filtered(
                lambda x: x.source_id.code == src
                and x.target_id.code == tgt).unlink()
            return True
        if portee == "message":
            noeud, pool = _deux_morceaux(ligne.cle)
            niveau.message_ids.filtered(
                lambda x: x.node_id.code == noeud
                and x.pool_id.code == pool).unlink()
            return True
        return _("portée inconnue")

    @staticmethod
    def _raccorder(cible):
        """Rattache les sous-processus qui n'ouvrent encore aucune page.

        Un lien déjà posé ne se refait pas : c'est précisément le renommage
        dans un éditeur tiers qui le casserait, puisque le raccord d'origine se
        fait par titre. Rend les sous-processus restés sans page.
        """
        par_titre = {d.title.strip().lower(): d for d in cible.diagram_ids}
        orphelins = []
        for noeud in cible.mapped("diagram_ids.node_ids").filtered(
                lambda n: n.kind == "sub"):
            if noeud.child_diagram_id:
                continue
            enfant = par_titre.get((noeud.name or "").strip().lower())
            if enfant and enfant != noeud.diagram_id:
                noeud.child_diagram_id = enfant.id
            else:
                orphelins.append(noeud)
        return orphelins

    # ------------------------------------------------------------ compte rendu
    def _compte_rendu(self, fait, refuse, orphelins):
        """Ce qui a été écrit, dit sur la carte elle-même.

        Chaque valeur passe par `Markup %`, qui échappe ses arguments : ces
        libellés viennent d'un fichier fourni par un tiers, et les concaténer
        dans du HTML rendu tel quel ferait exécuter un nom de nœud dans la
        session de quiconque ouvre la cartographie.
        """
        ignore = len(self.line_ids) - len(fait) - len(refuse)
        blocs = [Markup("<p>Fusion de <b>%s</b> : <b>%s</b> écart(s) appliqué(s), "
                        "%s ignoré(s).</p>") % (
            self.nom_fichier or _("fichier sans nom"), len(fait), ignore)]
        if fait:
            items = Markup("").join(
                Markup("<li>%s — %s%s</li>") % (
                    l.niveau_titre or l.niveau, l.libelle,
                    Markup(" (%s → %s)") % (l.avant, l.apres)
                    if l.avant or l.apres else Markup(""))
                for l in fait)
            blocs.append(Markup("<ul>%s</ul>") % items)
        if refuse:
            items = Markup("").join(
                Markup("<li>%s — %s : <b>%s</b></li>") % (
                    l.niveau_titre or l.niveau, l.libelle, motif)
                for l, motif in refuse)
            blocs.append(Markup(
                "<p><b>%s</b></p><ul>%s</ul>") % (
                _("Retenu mais non appliqué :"), items))
        if orphelins:
            items = Markup("").join(
                Markup("<li>%s — %s</li>") % (
                    n.diagram_id.display_name, n.name or n.code)
                for n in orphelins)
            blocs.append(Markup(
                "<p><b>%s</b></p><ul>%s</ul>") % (
                _("Sous-processus qui n'ouvrent aucune page :"), items))
        return Markup("").join(blocs)

    # ---------------------------------------------------------------- outils
    def _revenir(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
