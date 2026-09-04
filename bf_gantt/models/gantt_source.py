# -*- coding: utf-8 -*-
"""La source unique de l'échéancier.

Tout ce que ce module affiche, imprime ou exporte sort d'ici, sous une seule
forme. C'est la leçon reprise de `bf_process` : une géométrie, plusieurs
sorties, et aucune dérive possible entre l'écran, le portail et le fichier.

Deux origines seulement, et elles rendent le même dictionnaire :

* un ``project.project``, dont les tâches deviennent des barres ;
* un ``bf.gantt.plan``, qui ne crée aucune tâche et vit pour lui-même.

⚠️ Tout passe par l'ORM, jamais par du SQL direct. Le portail sert ces mêmes
données à des gens qui n'ont pas de compte : une requête qui court-circuite les
règles d'enregistrement fuirait chez le premier client multi-société.
"""
import logging
import re
from datetime import date, datetime, timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Durée de repli (jours) d'une barre dont on ne connaît qu'un bout.
DUREE_DEFAUT = 5
# Marge (jours) ajoutée de chaque côté de la plage calculée.
MARGE = 3
# Au-delà, on refuse de tracer : la page devient illisible et le PDF, énorme.
PLAFOND_BARRES = 400

CLOS = ("1_done", "1_canceled")

# Une couleur de marque part dans des documents servis à des visiteurs non
# connectés : elle doit ressembler à une couleur, pas seulement commencer par #.
COULEUR = re.compile(r"^#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{2})?)?$")

# Les regroupements d'un projet. `step` n'apparaît que si
# `bf_stepbystep_clients` est installé : on l'offre, on n'en dépend pas.
REGROUPEMENTS_PROJET = [
    ("stage", "Étape du projet"),
    ("milestone", "Jalon"),
    ("assignee", "Responsable"),
    ("company", "Société"),
    ("step", "Étape de progression"),
    ("none", "Aucun"),
]

# Un plan autonome n'a ni étape ni jalon de projet : ses couloirs sont les
# siens. Lui proposer « Étape du projet » afficherait un menu qui ne fait rien.
REGROUPEMENTS_PLAN = [
    ("lane", "Couloir"),
    ("assignee", "Responsable"),
    ("none", "Aucun"),
]


def abreger(nom):
    """« Tremblay, Jane Doe » devient « Jane D. »."""
    if not nom:
        return ""
    if ", " in nom:
        nom = nom.split(", ", 1)[-1]
    morceaux = nom.split()
    if len(morceaux) >= 2:
        return "%s %s." % (morceaux[0], morceaux[-1][0])
    return nom


def responsables(usagers):
    """« Jane D. » ou « Jane D. +2 ». Jamais deux noms complets côte à côte.

    ⚠️ La colonne des libellés fait 236 points et le responsable en a 58 : deux
    noms abrégés collés par une virgule les dépassent, et le nom de la tâche
    passe dessous. Couper à l'affichage ampute un texte aligné à droite par la
    gauche, ce qui se lit comme un défaut. On raccourcit donc à la source, pour
    les cinq rendus d'un coup.
    """
    noms = [abreger(u.display_name) for u in usagers if u.display_name]
    if not noms:
        return ""
    if len(noms) == 1:
        return noms[0]
    return "%s +%d" % (noms[0], len(noms) - 1)


def _jour(valeur):
    """Ramène un Date, un Datetime ou None à un `date` (ou None)."""
    if not valeur:
        return None
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, date):
        return valeur
    return None


class BfGanttSource(models.AbstractModel):
    _name = "bf.gantt.source"
    _description = "Source de données d'un échéancier"

    # `AbstractModel` : ni champ ni table, seulement le point d'entrée RPC du
    # composant OWL et des générateurs. Déclaré `models.Model` + `_auto = False`,
    # il entrerait dans `Registry.check_tables_exist()` et journaliserait un
    # « Model … has no table » à chaque passe du chargeur.

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    @api.model
    def get_portefeuille(self):
        """Ce que le sélecteur du composant OWL propose à l'ouverture."""
        projets = self.env["project.project"].search(
            ["|", ("date", "=", False), ("date", ">=", fields.Date.today())],
            order="name",
        )
        plans = self.env["bf.gantt.plan"].search(
            [("state", "!=", "cancel")], order="name"
        )
        return {
            "projects": [
                {
                    "id": p.id,
                    "name": p.display_name,
                    "partner_name": p.partner_id.display_name or "",
                }
                for p in projets
            ],
            "plans": [
                {
                    "id": p.id,
                    "name": p.name,
                    "partner_name": p.partner_id.display_name or "",
                }
                for p in plans
            ],
            "groupings": {
                "project": [
                    {"key": k, "label": _(lbl)}
                    for k, lbl in REGROUPEMENTS_PROJET
                    if k != "step" or self._etapes_de_progression_disponibles()
                ],
                "plan": [{"key": k, "label": _(lbl)}
                         for k, lbl in REGROUPEMENTS_PLAN],
            },
        }

    @api.model
    def get_echeancier(self, kind, res_id, grouping="stage"):
        """Rend l'échéancier normalisé d'un projet ou d'un plan.

        `kind` vaut 'project' ou 'plan'. Les droits sont ceux de l'appelant :
        cette méthode ne fait aucun `sudo`, et c'est voulu.
        """
        if kind == "plan":
            plan = self.env["bf.gantt.plan"].browse(int(res_id))
            plan.check_access("read")
            return self._depuis_plan(plan, grouping)
        projet = self.env["project.project"].browse(int(res_id))
        projet.check_access("read")
        return self._depuis_projet(projet, grouping)

    @api.model
    def get_geometrie(self, kind, res_id, grouping="stage", echelle="week"):
        """L'échéancier **déjà positionné**, prêt à tracer.

        Le composant OWL ne recalcule aucune coordonnée : il reçoit les mêmes
        que le PDF, le PNG et le SVG. C'est ce qui garantit que l'écran et le
        fichier montrent la même chose. Le prix est un aller-retour au serveur
        quand on change d'échelle ; le client les met en cache.
        """
        from ..generateur import geometrie as geo
        payload = self.get_echeancier(kind, res_id, grouping=grouping)
        geometrie = geo.construire(payload, echelle=echelle)
        # Le tracé n'a pas besoin des barres brutes, mais l'infobulle si.
        geometrie["details"] = {t["ref"]: t for t in payload["tasks"]}
        # 🔴 Le navigateur ne dessine pas le logo, et surtout : `boite_logo` rend
        # un dict qui contient les OCTETS BRUTS du fichier. JSON ne sait pas les
        # sérialiser, donc la réponse RPC mourait dans `json.dumps` et la vue
        # affichait « Connection … couldn't be established ». Le retirer n'est pas
        # une économie de bande passante, c'est ce qui rend la réponse valide.
        geometrie["societe"] = dict(geometrie["societe"], logo="")
        geometrie["logo"] = None
        geometrie["grouping"] = payload["grouping"]
        geometrie["source"] = payload["source"]
        return geometrie

    @api.model
    def action_ouvrir_tache(self, ref):
        """Le clic sur une barre. `ref` porte son origine, pas seulement un id."""
        modele, _sep, brut = (ref or "").partition("-")
        if not brut.isdigit():
            return False
        cible = {
            "task": "project.task",
            "item": "bf.gantt.item",
            "milestone": "project.milestone",
        }.get(modele)
        if not cible:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": cible,
            "res_id": int(brut),
            "views": [[False, "form"]],
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Projet
    # ------------------------------------------------------------------

    def _depuis_projet(self, projet, grouping):
        aujourdhui = date.today()
        taches = self.env["project.task"].search(
            [("project_id", "=", projet.id)],
            order="date_deadline asc, id asc",
            limit=PLAFOND_BARRES,
        )
        tronque = len(taches) == PLAFOND_BARRES

        barres = [self._barre_de_tache(t, aujourdhui, grouping) for t in taches]
        barres += self._barres_de_jalons(projet, aujourdhui, grouping)

        connus = {b["ref"] for b in barres}
        deps = []
        for t in taches:
            for amont in t.depend_on_ids:
                ref = "task-%s" % amont.id
                if ref in connus:
                    deps.append({"from": ref, "to": "task-%s" % t.id})

        return self._assembler(
            source={"kind": "project", "model": "project.project", "id": projet.id},
            titre=projet.display_name,
            sous_titre=projet.partner_id.display_name or "",
            societe=projet.company_id or self.env.company,
            grouping=grouping,
            barres=barres,
            deps=deps,
            aujourdhui=aujourdhui,
            bornes=(_jour(projet.date_start), _jour(projet.date)),
            tronque=tronque,
        )

    def _barre_de_tache(self, tache, aujourdhui, grouping):
        debut, origine = self._debut_de_tache(tache)
        fin = _jour(tache.date_deadline)
        if not fin:
            fin = (debut or aujourdhui) + timedelta(days=DUREE_DEFAUT)
        if not debut:
            debut = fin - timedelta(days=DUREE_DEFAUT)
            origine = "repli"
        if fin < debut:
            fin = debut

        cle, nom, seq = self._couloir_de_tache(tache, grouping)
        return {
            "ref": "task-%s" % tache.id,
            "id": tache.id,
            "name": tache.name or _("Tâche"),
            "lane": cle,
            "lane_name": nom,
            "lane_seq": seq,
            "start": str(debut),
            "end": str(fin),
            "start_origin": origine,
            "deadline": str(_jour(tache.date_deadline) or ""),
            "progress": self._avancement(tache),
            "status": self._statut(tache.state, _jour(tache.date_deadline), aujourdhui),
            "assignee": responsables(tache.user_ids),
            "allocated_hours": round(tache.allocated_hours or 0.0, 2),
            "effective_hours": round(tache.effective_hours or 0.0, 2),
            "is_milestone": False,
            "closed": tache.state in CLOS,
        }

    def _debut_de_tache(self, tache):
        """La date de début, et d'où elle vient. L'origine est affichée."""
        if tache.planned_date_begin:
            return _jour(tache.planned_date_begin), "planifie"
        if tache.date_assign:
            return _jour(tache.date_assign), "assignation"
        if tache.create_date:
            return _jour(tache.create_date), "creation"
        return None, "repli"

    def _barres_de_jalons(self, projet, aujourdhui, grouping):
        """Les jalons du projet, en losanges de durée nulle."""
        if "project.milestone" not in self.env:
            return []
        jalons = self.env["project.milestone"].search(
            [("project_id", "=", projet.id)], order="deadline asc, id asc"
        )
        sortie = []
        for j in jalons:
            quand = _jour(j.deadline) or aujourdhui
            atteint = bool(j.is_reached)
            sortie.append({
                "ref": "milestone-%s" % j.id,
                "id": j.id,
                "name": j.name or _("Jalon"),
                "lane": "milestone" if grouping != "milestone" else "milestone-%s" % j.id,
                "lane_name": _("Jalons"),
                "lane_seq": 9998,
                "start": str(quand),
                "end": str(quand),
                "start_origin": "planifie",
                "deadline": str(quand),
                "progress": 100 if atteint else 0,
                "status": "done" if atteint else (
                    "overdue" if quand < aujourdhui else "upcoming"),
                "assignee": "",
                "allocated_hours": 0.0,
                "effective_hours": 0.0,
                "is_milestone": True,
                "closed": atteint,
            })
        return sortie

    # ------------------------------------------------------------------
    # Plan autonome
    # ------------------------------------------------------------------

    def _depuis_plan(self, plan, grouping):
        aujourdhui = date.today()
        barres = []
        for item in plan.item_ids.sorted(key=lambda i: (i.sequence, i.id)):
            debut = _jour(item.date_start) or aujourdhui
            fin = _jour(item.date_end) or debut
            if item.is_milestone:
                fin = debut
            if fin < debut:
                fin = debut
            barres.append({
                "ref": "item-%s" % item.id,
                "id": item.id,
                "name": item.name,
                "lane": self._cle_couloir_plan(item, grouping),
                "lane_name": item.lane or _("Sans regroupement"),
                "lane_seq": item.sequence,
                "start": str(debut),
                "end": str(fin),
                "start_origin": "planifie",
                "deadline": str(fin),
                "progress": int(item.progress or 0),
                "status": item.gantt_status(aujourdhui),
                "assignee": item.assignee or "",
                "allocated_hours": round(item.allocated_hours or 0.0, 2),
                "effective_hours": 0.0,
                "is_milestone": item.is_milestone,
                "closed": item.state == "done",
            })

        connus = {b["ref"] for b in barres}
        deps = []
        for item in plan.item_ids:
            for amont in item.depend_on_ids:
                ref = "item-%s" % amont.id
                if ref in connus:
                    deps.append({"from": ref, "to": "item-%s" % item.id})

        return self._assembler(
            source={"kind": "plan", "model": "bf.gantt.plan", "id": plan.id},
            titre=plan.name,
            sous_titre=plan.partner_id.display_name or "",
            societe=plan.company_id or self.env.company,
            grouping=grouping,
            barres=barres,
            deps=deps,
            aujourdhui=aujourdhui,
            bornes=(_jour(plan.date_start), _jour(plan.date_end)),
            tronque=False,
        )

    def _cle_couloir_plan(self, item, grouping):
        if grouping == "assignee":
            return "assignee-%s" % (item.assignee or "")
        if grouping == "none":
            return "all"
        return "lane-%s" % (item.lane or "")

    # ------------------------------------------------------------------
    # Couloirs
    # ------------------------------------------------------------------

    def _etapes_de_progression_disponibles(self):
        """`bf_stepbystep_clients` est-il là ? On le demande, on n'en dépend pas."""
        return "progression_step_number" in self.env["project.task.type"]._fields

    def _couloir_de_tache(self, tache, grouping):
        """Rend (clé, nom, rang) du couloir d'une tâche selon le regroupement."""
        if grouping == "none":
            return "all", _("Toutes les tâches"), 0
        if grouping == "milestone":
            if tache.milestone_id:
                return ("milestone-%s" % tache.milestone_id.id,
                        tache.milestone_id.name, tache.milestone_id.id)
            return "milestone-none", _("Sans jalon"), 9999
        if grouping == "assignee":
            if tache.user_ids:
                premier = tache.user_ids[0]
                return ("assignee-%s" % premier.id,
                        abreger(premier.display_name), premier.id)
            return "assignee-none", _("Sans responsable"), 9999
        if grouping == "project":
            projet = tache.project_id
            return ("project-%s" % projet.id, projet.display_name or "", projet.id)
        if grouping == "company":
            societe = tache.company_id or self.env.company
            return ("company-%s" % societe.id, societe.name, societe.id)
        if grouping == "step" and self._etapes_de_progression_disponibles():
            etape = tache.stage_id
            numero = etape.progression_step_number or 0
            if numero:
                nom = etape.progression_step_name or etape.name or ""
                return "step-%s" % numero, nom, numero
            return "step-0", _("Hors étape"), 9999
        # Défaut : l'étape du projet.
        if tache.stage_id:
            return ("stage-%s" % tache.stage_id.id, tache.stage_id.name,
                    tache.stage_id.sequence or tache.stage_id.id)
        return "stage-none", _("Sans étape"), 9999

    # ------------------------------------------------------------------
    # Assemblage
    # ------------------------------------------------------------------

    def _assembler(self, source, titre, sous_titre, societe, grouping,
                   barres, deps, aujourdhui, bornes, tronque):
        couloirs = {}
        mini = maxi = None
        for b in barres:
            cle = b["lane"]
            c = couloirs.setdefault(cle, {
                "key": cle,
                "name": b["lane_name"],
                "seq": b["lane_seq"],
                "total": 0,
                "done": 0,
            })
            c["total"] += 1
            if b["closed"]:
                c["done"] += 1
            d, f = b["start"], b["end"]
            mini = d if mini is None else min(mini, d)
            maxi = f if maxi is None else max(maxi, f)

        liste = sorted(couloirs.values(), key=lambda c: (c["seq"], c["name"]))
        for c in liste:
            c["pct"] = round(c["done"] / c["total"] * 100) if c["total"] else 0

        debut = date.fromisoformat(mini) if mini else (bornes[0] or aujourdhui)
        fin = date.fromisoformat(maxi) if maxi else (
            bornes[1] or aujourdhui + timedelta(days=30))
        # ⚠️ Au bord du calendrier, ajouter trois jours lève `OverflowError`,
        # et cela se produit AVANT que la géométrie n'applique son plafond :
        # la route publique rendait alors un 500.
        try:
            debut -= timedelta(days=MARGE)
        except OverflowError:
            debut = date.min
        try:
            fin += timedelta(days=MARGE)
        except OverflowError:
            fin = date.max
        if fin <= debut:
            fin = debut + timedelta(days=DUREE_DEFAUT)

        return {
            "source": source,
            "title": titre or "",
            "subtitle": sous_titre or "",
            "company": self._marque(societe),
            "grouping": grouping,
            "lanes": liste,
            "tasks": barres,
            "deps": deps,
            "range": {
                "min": str(debut),
                "max": str(fin),
                "today": str(aujourdhui),
            },
            "truncated": tronque,
            "limit": PLAFOND_BARRES,
        }

    def _marque(self, societe):
        """Ce que les rendus savent de la société : nom, couleurs, logo.

        Les noms de champs varient selon les modules de marque installés, donc on
        essaie dans l'ordre du plus spécifique au plus général et on retombe sur
        le logo standard d'Odoo, qui existe toujours. Un locataire sans aucun
        module de marque sort quand même un document à ses couleurs.
        """
        return {
            "id": societe.id,
            "name": societe.name,
            "color": self._couleur(societe, ("report_brand_primary",
                                             "report_brand_color",
                                             "bf_brand_color",
                                             "primary_color"), "#29ABE1"),
            "dark": self._couleur(societe, ("report_brand_dark",
                                            "secondary_color"), "#2D3031"),
            "logo": self._logo(societe),
            "tagline": self._texte(societe, ("report_header", "brand_email_tagline")),
        }

    def _couleur(self, societe, noms, defaut):
        """⚠️ La validation est ICI, une seule fois. Vérifier seulement le « # »
        laissait passer `#ZZZZZZ`, que `pdf._rgb` et `png._rgb` transforment en
        `ValueError` non attrapée, donc en 500 sur une route publique."""
        for nom in noms:
            if nom in societe._fields:
                valeur = societe[nom]
                if valeur and isinstance(valeur, str) and COULEUR.match(valeur):
                    return valeur
        return defaut

    def _texte(self, societe, noms):
        for nom in noms:
            if nom in societe._fields and societe[nom]:
                brut = societe[nom]
                if hasattr(brut, "striptags"):
                    brut = brut.striptags()
                brut = re.sub(r"<[^>]+>", " ", str(brut))
                brut = re.sub(r"\s+", " ", brut).strip()
                if brut:
                    return brut[:120]
        return ""

    def _logo(self, societe):
        """Le logo en base64, tel que la base le stocke. Vide si la société n'en a
        pas : un document sans logo reste un document, il ne doit pas échouer."""
        for nom in ("report_brand_logo", "logo_web", "logo"):
            if nom in societe._fields and societe[nom]:
                brut = societe[nom]
                return brut.decode("ascii") if isinstance(brut, bytes) else str(brut)
        return ""

    # ------------------------------------------------------------------
    # Petites règles partagées
    # ------------------------------------------------------------------

    def _statut(self, state, echeance, aujourdhui):
        if state == "1_done":
            return "done"
        if state == "1_canceled":
            return "canceled"
        if echeance and echeance < aujourdhui and state not in CLOS:
            return "overdue"
        if state == "01_in_progress":
            return "in_progress"
        return "upcoming"

    def _avancement(self, tache):
        """0 à 100. `progress` d'`hr_timesheet` est un ratio, il peut dépasser 1."""
        if tache.state in CLOS:
            return 100
        if tache.allocated_hours and tache.allocated_hours > 0:
            return max(0, min(100, round((tache.progress or 0.0) * 100)))
        return 0
