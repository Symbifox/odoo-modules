# -*- coding: utf-8 -*-
"""Comparer deux cartes, et n'avoir qu'une seule façon de le faire.

Les identifiants de niveau et de nœud sont conservés d'une carte dérivée à
l'autre : c'est ce qui permet de dire « cette étape a été renommée » plutôt que
« une étape a disparu, une autre est apparue ». Sans eux, un diff n'aurait rien
à dire d'utile.

Le calcul sert deux appelants qui n'ont pas le même horizon :

* l'assistant de comparaison, qui affiche un rapport et l'oublie ;
* le semis des écarts vers un processus souhaité, qui les garde, les fait
  porter par quelqu'un et les suit jusqu'au bout.

D'où une seule fonction de calcul, `calculer_ecarts`, et deux rendus. Deux
implémentations auraient divergé au premier genre d'écart ajouté d'un côté.
Chaque écart porte une **clé stable** : c'est elle qui permet à un second semis
de reconnaître un écart déjà consigné au lieu d'en créer un jumeau et d'effacer
ce qu'une personne avait écrit dessus.
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

#: Les portées, dans l'ordre où elles se lisent, avec leur titre de bloc.
PORTEES = (
    ("niveau", "Niveaux"),
    ("noeud", "Nœuds"),
    ("flux", "Flux"),
    ("message", "Flux de message"),
)

#: Ce qu'un écart fait à l'étape qu'il vise. Sert aux teintes : un retrait se
#: montre sur la carte d'avant, un ajout sur celle d'après, une modification
#: sur les deux.
COTES = {
    "ajout": ("apres",),
    "retrait": ("avant",),
    "renommage": ("avant", "apres"),
    "nature": ("avant", "apres"),
    "couloir": ("avant", "apres"),
    "ton": ("avant", "apres"),
}


def _index(processus):
    """Aplati une carte en dictionnaires indexés par code."""
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


def _ecart(portee, genre, cle, libelle, diagram_code="", diagram_titre="",
           node_code=""):
    return {
        "portee": portee,
        "genre": genre,
        "cle": cle,
        "libelle": libelle,
        "diagram_code": diagram_code,
        "diagram_titre": diagram_titre,
        "node_code": node_code,
    }


def calculer_ecarts(avant, apres):
    """Ce qui sépare deux cartes, dans l'ordre où ça se lit.

    `avant` est la carte de référence (la version précédente, ou l'état
    actuel), `apres` celle qu'on lui compare (la version récente, ou le
    processus souhaité). Rend une liste de dictionnaires plats : le rapport
    HTML les groupe, le semis les enregistre.
    """
    av_n, av_no, av_f, av_m = _index(avant)
    ap_n, ap_no, ap_f, ap_m = _index(apres)
    out = []

    for code in sorted(set(ap_n) - set(av_n)):
        d = ap_n[code]
        out.append(_ecart("niveau", "ajout", f"niveau:ajout:{code}",
                          f"niveau « {d.title} »", code, d.title))
    for code in sorted(set(av_n) - set(ap_n)):
        d = av_n[code]
        out.append(_ecart("niveau", "retrait", f"niveau:retrait:{code}",
                          f"niveau « {d.title} »", code, d.title))
    for code in sorted(set(av_n) & set(ap_n)):
        if av_n[code].title != ap_n[code].title:
            out.append(_ecart(
                "niveau", "renommage", f"niveau:renommage:{code}",
                f"« {av_n[code].title} » → « {ap_n[code].title} »",
                code, ap_n[code].title))

    for cle in sorted(set(ap_no) - set(av_no)):
        n = ap_no[cle]
        out.append(_ecart(
            "noeud", "ajout", f"noeud:ajout:{cle[0]}:{cle[1]}",
            f"{GENRES.get(n.kind, n.kind)} « {n.name or n.code} » "
            f"dans « {n.diagram_id.title} »",
            cle[0], n.diagram_id.title, cle[1]))
    for cle in sorted(set(av_no) - set(ap_no)):
        n = av_no[cle]
        out.append(_ecart(
            "noeud", "retrait", f"noeud:retrait:{cle[0]}:{cle[1]}",
            f"{GENRES.get(n.kind, n.kind)} « {n.name or n.code} » "
            f"dans « {n.diagram_id.title} »",
            cle[0], n.diagram_id.title, cle[1]))
    for cle in sorted(set(av_no) & set(ap_no)):
        a, b = av_no[cle], ap_no[cle]
        ou = f" dans « {b.diagram_id.title} »"
        commun = (cle[0], b.diagram_id.title, cle[1])
        if (a.name or "") != (b.name or ""):
            out.append(_ecart(
                "noeud", "renommage", f"noeud:renommage:{cle[0]}:{cle[1]}",
                f"« {a.name or a.code} » → « {b.name or b.code} »{ou}", *commun))
        if a.kind != b.kind:
            out.append(_ecart(
                "noeud", "nature", f"noeud:nature:{cle[0]}:{cle[1]}",
                f"« {b.name or b.code} » : {GENRES.get(a.kind, a.kind)} "
                f"→ {GENRES.get(b.kind, b.kind)}{ou}", *commun))
        if (a.lane_id.code or "") != (b.lane_id.code or ""):
            out.append(_ecart(
                "noeud", "couloir", f"noeud:couloir:{cle[0]}:{cle[1]}",
                f"« {b.name or b.code} » : {a.lane_id.name or 'aucun'} "
                f"→ {b.lane_id.name or 'aucun'}{ou}", *commun))
        if (a.tone or "") != (b.tone or ""):
            out.append(_ecart(
                "noeud", "ton", f"noeud:ton:{cle[0]}:{cle[1]}",
                f"annotation « {(b.name or '')[:40]}… » : "
                f"{a.tone or 'aucun'} → {b.tone or 'aucun'}{ou}", *commun))

    for cle in sorted(set(ap_f) - set(av_f), key=str):
        f = ap_f[cle]
        out.append(_ecart(
            "flux", "ajout", f"flux:ajout:{cle[0]}:{cle[1]}>{cle[2]}",
            f"{f.source_id.name or f.source_id.code} → "
            f"{f.target_id.name or f.target_id.code}",
            cle[0], f.diagram_id.title))
    for cle in sorted(set(av_f) - set(ap_f), key=str):
        f = av_f[cle]
        out.append(_ecart(
            "flux", "retrait", f"flux:retrait:{cle[0]}:{cle[1]}>{cle[2]}",
            f"{f.source_id.name or f.source_id.code} → "
            f"{f.target_id.name or f.target_id.code}",
            cle[0], f.diagram_id.title))
    for cle in sorted(set(av_f) & set(ap_f), key=str):
        a, b = av_f[cle], ap_f[cle]
        if (a.label or "") != (b.label or ""):
            out.append(_ecart(
                "flux", "porte", f"flux:porte:{cle[0]}:{cle[1]}>{cle[2]}",
                f"{b.source_id.name or b.source_id.code} → "
                f"{b.target_id.name or b.target_id.code} : "
                f"« {a.label or 'sans porte'} » → « {b.label or 'sans porte'} »",
                cle[0], b.diagram_id.title))

    for cle in sorted(set(ap_m) - set(av_m), key=str):
        m = ap_m[cle]
        out.append(_ecart(
            "message", "ajout", f"message:ajout:{cle[0]}:{cle[1]}:{cle[2]}",
            f"« {m.label} »", cle[0], m.diagram_id.title))
    for cle in sorted(set(av_m) - set(ap_m), key=str):
        m = av_m[cle]
        out.append(_ecart(
            "message", "retrait", f"message:retrait:{cle[0]}:{cle[1]}:{cle[2]}",
            f"« {m.label} »", cle[0], m.diagram_id.title))
    for cle in sorted(set(av_m) & set(ap_m), key=str):
        a, b = av_m[cle], ap_m[cle]
        if a.label != b.label:
            out.append(_ecart(
                "message", "libelle",
                f"message:libelle:{cle[0]}:{cle[1]}:{cle[2]}",
                f"« {a.label} » → « {b.label} »", cle[0], b.diagram_id.title))
    return out


TEINTES_RAPPORT = {"ajout": "#1B8A4B", "retrait": "#C0392B"}


def rendre_rapport(ecarts, entete=Markup("")):
    """Compose le rapport en échappant tout ce qui vient d'un enregistrement.

    Les libellés comparés sont saisis par l'utilisateur, importés d'un `.bpmn`
    tiers ou posés depuis le tracé : ils ne sont pas sûrs. Chaque valeur passe
    donc par `Markup %`, qui échappe ses arguments, et le champ garde son
    `sanitize`. Concaténer ces textes en f-string dans du HTML rendu tel quel
    ferait exécuter un nom de nœud dans la session de quiconque ouvre la
    comparaison.
    """
    if not ecarts:
        return entete + Markup("<p>%s</p>") % _(
            "Aucun écart : les deux cartes disent la même chose.")
    blocs = []
    for portee, titre in PORTEES:
        lignes = [e for e in ecarts if e["portee"] == portee]
        if not lignes:
            continue
        items = Markup("").join(
            Markup("<li><span style='color:%s;font-weight:600'>%s</span>"
                   " : %s</li>") % (TEINTES_RAPPORT.get(e["genre"], "#B8860B"),
                                    e["genre"], e["libelle"])
            for e in lignes)
        blocs.append(Markup("<h4>%s <span class='text-muted'>(%s)</span>"
                            "</h4><ul>%s</ul>") % (titre, len(lignes), items))
    return entete + Markup("").join(blocs)


class BfProcessCompareWizard(models.TransientModel):
    _name = "bf.process.compare.wizard"
    _description = "Comparer deux cartes"

    source_id = fields.Many2one(
        "bf.process", string="Version récente", required=True)
    cible_id = fields.Many2one(
        "bf.process", string="Comparer avec", required=True,
        help="En général la version précédente, ou l'état actuel dont un"
             " processus souhaité est tiré.")
    # `sanitize` reste actif : c'est la deuxième couche. La première est
    # l'échappement de chaque valeur interpolée dans `rendre_rapport`.
    rapport_html = fields.Html(string="Écarts", readonly=True)
    ecart_count = fields.Integer(string="Nombre d'écarts", readonly=True)

    @api.onchange("source_id")
    def _onchange_source(self):
        for rec in self:
            if rec.source_id and not rec.cible_id:
                rec.cible_id = (rec.source_id.origine_id
                                or rec.source_id.version_precedente_id)

    def action_comparer(self):
        self.ensure_one()
        if self.source_id == self.cible_id:
            raise UserError(_("Comparer une carte avec elle-même ne dit rien."))
        ecarts = calculer_ecarts(self.cible_id, self.source_id)
        self.ecart_count = len(ecarts)
        self.rapport_html = rendre_rapport(ecarts, self._entete())
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _entete(self):
        self.ensure_one()
        return Markup("<p class='text-muted'>De <b>%s</b> vers "
                      "<b>%s</b>.</p>") % (self.cible_id.display_name,
                                           self.source_id.display_name)
