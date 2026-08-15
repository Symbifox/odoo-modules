# -*- coding: utf-8 -*-
"""Comparer deux versions d'une cartographie.

Les identifiants de niveau et de nœud sont conservés d'une version à l'autre :
c'est ce qui permet de dire « cette étape a été renommée » plutôt que « une
étape a disparu, une autre est apparue ». Sans eux, un diff n'aurait rien à
dire d'utile.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

GENRES = dict([
    ("start", "début"), ("msgStart", "début sur message"),
    ("timerCatch", "attente d'un délai"), ("msgCatch", "attente d'un message"),
    ("end", "fin"), ("task", "tâche"), ("send", "envoi"), ("receive", "réception"),
    ("user", "tâche humaine"), ("sub", "sous-processus"),
    ("xor", "passerelle exclusive"), ("and", "passerelle parallèle"),
    ("or", "passerelle inclusive"), ("note", "annotation"),
    ("store", "réserve de données"),
])


class BfProcessCompareWizard(models.TransientModel):
    _name = "bf.process.compare.wizard"
    _description = "Comparer deux versions"

    source_id = fields.Many2one(
        "bf.process", string="Version récente", required=True)
    cible_id = fields.Many2one(
        "bf.process", string="Comparer avec", required=True,
        help="En général la version précédente.")
    # `sanitize` reste actif : c'est la deuxième couche. La première est
    # l'échappement de chaque valeur interpolée dans `_rendre`.
    rapport_html = fields.Html(string="Écarts", readonly=True)
    ecart_count = fields.Integer(string="Nombre d'écarts", readonly=True)

    @api.onchange("source_id")
    def _onchange_source(self):
        for rec in self:
            if rec.source_id and not rec.cible_id:
                rec.cible_id = rec.source_id.version_precedente_id

    def action_comparer(self):
        self.ensure_one()
        if self.source_id == self.cible_id:
            raise UserError(_("Comparer une version avec elle-même ne dit rien."))
        ecarts = self._ecarts()
        self.ecart_count = sum(len(v) for v in ecarts.values())
        self.rapport_html = self._rendre(ecarts)
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    # ------------------------------------------------------------------ calcul
    @staticmethod
    def _index(processus):
        """Aplati une version en dictionnaires indexés par code."""
        niveaux, noeuds, flux, msgs = {}, {}, {}, {}
        for d in processus.diagram_ids:
            niveaux[d.code] = d
            for n in d.node_ids:
                noeuds[(d.code, n.code)] = n
            for f in d.flow_ids:
                flux[(d.code, f.source_id.code, f.target_id.code)] = f
            for m in d.message_ids:
                msgs[(d.code, m.node_id.code, m.pool_id.code)] = m
        return niveaux, noeuds, flux, msgs

    def _ecarts(self):
        av_n, av_no, av_f, av_m = self._index(self.cible_id)
        ap_n, ap_no, ap_f, ap_m = self._index(self.source_id)
        out = {"niveaux": [], "noeuds": [], "flux": [], "messages": []}

        for code in sorted(set(ap_n) - set(av_n)):
            out["niveaux"].append(("ajout", f"niveau « {ap_n[code].title} »"))
        for code in sorted(set(av_n) - set(ap_n)):
            out["niveaux"].append(("retrait", f"niveau « {av_n[code].title} »"))
        for code in sorted(set(av_n) & set(ap_n)):
            if av_n[code].title != ap_n[code].title:
                out["niveaux"].append((
                    "renommage",
                    f"« {av_n[code].title} » → « {ap_n[code].title} »"))

        for cle in sorted(set(ap_no) - set(av_no)):
            n = ap_no[cle]
            out["noeuds"].append((
                "ajout", f"{GENRES.get(n.kind, n.kind)} « {n.name or n.code} » "
                         f"dans « {n.diagram_id.title} »"))
        for cle in sorted(set(av_no) - set(ap_no)):
            n = av_no[cle]
            out["noeuds"].append((
                "retrait", f"{GENRES.get(n.kind, n.kind)} « {n.name or n.code} » "
                           f"dans « {n.diagram_id.title} »"))
        for cle in sorted(set(av_no) & set(ap_no)):
            a, b = av_no[cle], ap_no[cle]
            ou = f" dans « {b.diagram_id.title} »"
            if (a.name or "") != (b.name or ""):
                out["noeuds"].append((
                    "renommage", f"« {a.name or a.code} » → « {b.name or b.code} »{ou}"))
            if a.kind != b.kind:
                out["noeuds"].append((
                    "nature", f"« {b.name or b.code} » : {GENRES.get(a.kind, a.kind)} "
                              f"→ {GENRES.get(b.kind, b.kind)}{ou}"))
            if (a.lane_id.code or "") != (b.lane_id.code or ""):
                out["noeuds"].append((
                    "couloir", f"« {b.name or b.code} » : {a.lane_id.name or '—'} "
                               f"→ {b.lane_id.name or '—'}{ou}"))
            if (a.tone or "") != (b.tone or ""):
                out["noeuds"].append((
                    "ton", f"annotation « {(b.name or '')[:40]}… » : "
                           f"{a.tone or '—'} → {b.tone or '—'}{ou}"))

        for cle in sorted(set(ap_f) - set(av_f), key=str):
            f = ap_f[cle]
            out["flux"].append((
                "ajout", f"{f.source_id.name or f.source_id.code} → "
                         f"{f.target_id.name or f.target_id.code}"))
        for cle in sorted(set(av_f) - set(ap_f), key=str):
            f = av_f[cle]
            out["flux"].append((
                "retrait", f"{f.source_id.name or f.source_id.code} → "
                           f"{f.target_id.name or f.target_id.code}"))
        for cle in sorted(set(av_f) & set(ap_f), key=str):
            a, b = av_f[cle], ap_f[cle]
            if (a.label or "") != (b.label or ""):
                out["flux"].append((
                    "porte", f"{b.source_id.name or b.source_id.code} → "
                             f"{b.target_id.name or b.target_id.code} : "
                             f"« {a.label or '—'} » → « {b.label or '—'} »"))

        for cle in sorted(set(ap_m) - set(av_m), key=str):
            out["messages"].append(("ajout", f"« {ap_m[cle].label} »"))
        for cle in sorted(set(av_m) - set(ap_m), key=str):
            out["messages"].append(("retrait", f"« {av_m[cle].label} »"))
        for cle in sorted(set(av_m) & set(ap_m), key=str):
            a, b = av_m[cle], ap_m[cle]
            if a.label != b.label:
                out["messages"].append(("libellé", f"« {a.label} » → « {b.label} »"))
        return out

    # ------------------------------------------------------------------ rendu
    TEINTES = {"ajout": "#1B8A4B", "retrait": "#C0392B"}

    def _rendre(self, ecarts):
        """Compose le rapport en échappant tout ce qui vient d'un enregistrement.

        Les libellés comparés sont saisis par l'utilisateur, importés d'un
        `.bpmn` tiers ou posés depuis le tracé : ils ne sont pas sûrs. Chaque
        valeur passe donc par `Markup %`, qui échappe ses arguments, et le
        champ garde son `sanitize`. Concaténer ces textes en f-string dans du
        HTML rendu tel quel ferait exécuter un nom de nœud dans la session de
        quiconque ouvre la comparaison.
        """
        self.ensure_one()
        entete = Markup("<p class='text-muted'>De <b>v%s</b> vers "
                        "<b>v%s</b>.</p>") % (self.cible_id.version or "",
                                              self.source_id.version or "")
        if not any(ecarts.values()):
            return entete + Markup("<p>%s</p>") % _(
                "Aucun écart : les deux versions disent la même chose.")
        blocs = []
        for titre, cle in (("Niveaux", "niveaux"), ("Nœuds", "noeuds"),
                           ("Flux", "flux"), ("Flux de message", "messages")):
            lignes = ecarts[cle]
            if not lignes:
                continue
            items = Markup("").join(
                Markup("<li><span style='color:%s;font-weight:600'>%s</span>"
                       " — %s</li>") % (self.TEINTES.get(genre, "#B8860B"),
                                        genre, texte)
                for genre, texte in lignes)
            blocs.append(Markup("<h4>%s <span class='text-muted'>(%s)</span>"
                                "</h4><ul>%s</ul>") % (titre, len(lignes), items))
        return entete + Markup("").join(blocs)
