# -*- coding: utf-8 -*-
"""Ce qui sépare l'état actuel du processus souhaité, et qui s'en occupe.

Le calcul mécanique sait déjà dire ce qui a bougé entre deux cartes. Il ne sait
pas dire **pourquoi**, ni **ce que ça rapporte**, ni **qui le fait**. Or c'est
exactement là que se joue la valeur d'un dossier de transformation : « l'étape
Saisir la facture est retirée » n'intéresse personne, « la saisie disparaît
parce que la facture entre par la boîte de dépôt, ce qui rend deux heures par
semaine au commis » se défend devant un comité.

D'où deux moitiés dans le même enregistrement :

* la moitié mécanique, **semée** depuis le diff et rafraîchie à chaque semis ;
* la moitié humaine (intention, gain, effort, responsable, état, tâche), qu'un
  semis ne touche **jamais**.

La clé stable de l'écart est ce qui rend la seconde possible. Sans elle, un
deuxième semis créerait des jumeaux et le travail écrit sur les premiers
partirait à la corbeille : le module aurait l'air de fonctionner, et perdrait
en silence ce qu'il y a de plus cher dedans.

⚠️ Les écarts ne gèlent pas avec la version. C'est délibéré, et c'est la seule
entorse au gel dans ce module : la carte est la pièce datée, le plan de
transformation est ce qui vit après elle. Valider la cible puis découvrir qu'on
ne peut plus cocher « fait » serait un piège.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .comparaison import COTES, PORTEES, calculer_ecarts

#: Ce qu'on cherche à obtenir en changeant l'étape. Volontairement court : une
#: liste d'intentions qu'on ne peut pas tenir de tête ne se remplit pas.
INTENTIONS = [
    ("automatiser", "Automate"),
    ("eliminer", "Eliminate"),
    ("simplifier", "Simplify"),
    ("deleguer", "Delegate"),
    ("controler", "Control"),
    ("outiller", "Provide tooling"),
]

ETATS = [
    ("a_decider", "To decide"),
    ("retenu", "Selected"),
    ("ecarte", "Dismissed"),
    ("fait", "Done"),
]

#: Les teintes de la vue delta, par côté. Un retrait se montre sur la carte
#: d'avant (l'étape y est encore), un ajout sur celle d'après.
TEINTES = {"ajout": "vert", "retrait": "rouge"}


class BfProcessEcart(models.Model):
    _name = "bf.process.ecart"
    _description = "Gap between the current state and the target process"
    _order = "cible_id, diagram_code, sequence, id"

    cible_id = fields.Many2one(
        "bf.process", string="Target process", required=True,
        ondelete="cascade", index=True,
        domain=[("nature", "=", "cible")])
    origine_id = fields.Many2one(
        "bf.process", string="Current state", related="cible_id.origine_id",
        store=True, index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Client", related="cible_id.partner_id", store=True)
    sequence = fields.Integer(string="Order", default=10)

    # --- la moitié mécanique, semée depuis le diff ---------------------------
    genre = fields.Selection(
        [("ajout", "Addition"), ("retrait", "Removal"), ("renommage", "Renaming"),
         ("nature", "Type change"), ("couloir", "Lane change"),
         ("ton", "Tone change"), ("porte", "Gate change"),
         ("libelle", "Label change")],
        string="Kind", required=True, default="ajout")
    portee = fields.Selection(
        [(cle, titre) for cle, titre in PORTEES],
        string="Scope", required=True, default="noeud")
    cle = fields.Char(
        string="Key", required=True, index=True, copy=False,
        help="What lets a new seeding recognize this gap rather than "
             "create a twin.")
    libelle = fields.Char(string="Gap", required=True)
    diagram_code = fields.Char(string="Level code")
    diagram_titre = fields.Char(string="Level")
    node_code = fields.Char(string="Step code")
    node_actuel_id = fields.Many2one(
        "bf.process.node", string="Current step", ondelete="set null")
    node_cible_id = fields.Many2one(
        "bf.process.node", string="Target step", ondelete="set null")
    source = fields.Selection(
        [("seme", "Seeded from the maps"), ("manuel", "Added by hand")],
        string="Provenance", required=True, default="manuel",
        help="A seeded gap is refreshed at the next seeding; a gap added "
             "by hand is never touched.")
    caduc = fields.Boolean(
        string="Lapsed",
        help="The maps no longer show this gap, but someone had written "
             "on it: it stays, flagged, rather than disappearing without "
             "anyone seeing it go.")

    # --- la moitié humaine, qu'un semis ne touche jamais ---------------------
    intention = fields.Selection(INTENTIONS, string="Intent")
    gain = fields.Text(
        string="Expected gain",
        help="What the change brings, in lead time, cost, risk or "
             "compliance. A gap without a gain is a preference.")
    effort = fields.Selection(
        [("faible", "Low"), ("moyen", "Medium"), ("eleve", "High")],
        string="Effort")
    responsable_id = fields.Many2one("res.users", string="Assignee")
    etat = fields.Selection(ETATS, string="State", required=True,
                            default="a_decider", index=True)
    task_id = fields.Many2one(
        "project.task", string="Task", ondelete="set null",
        help="Where the change actually happens.")
    note = fields.Text(string="Note")

    _sql_constraints = [
        ("cle_uniq", "unique (cible_id, cle)",
         "This gap is already recorded for this target process."),
    ]

    @api.depends("libelle", "cle")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec.libelle or rec.cle

    def _porte_du_travail(self):
        """Quelqu'un a-t-il écrit sur cet écart ?

        Sert au semis : un écart mécanique que plus rien ne justifie et sur
        lequel personne n'a rien mis peut partir sans qu'on perde quoi que ce
        soit. Dès qu'une intention, un gain, un responsable, une tâche, une
        remarque ou une décision d'état s'y trouve, il reste.
        """
        self.ensure_one()
        return bool(self.intention or (self.gain or "").strip() or self.effort
                    or self.responsable_id or self.task_id
                    or (self.note or "").strip()
                    or self.etat != "a_decider")

    def action_creer_tache(self):
        """Fait descendre l'écart là où le travail se suit vraiment."""
        self.ensure_one()
        if self.task_id:
            raise UserError(_("This gap already has the task \"%s\".")
                            % self.task_id.display_name)
        projet = self.cible_id.project_id
        if not projet:
            raise UserError(_(
                "\"%s\" is not linked to any project: a task would have "
                "nowhere to go.") % self.cible_id.display_name)
        self.task_id = self.env["project.task"].create({
            "name": self.libelle,
            "project_id": projet.id,
            "user_ids": [(6, 0, self.responsable_id.ids)],
            "partner_id": self.cible_id.partner_id.id or False,
            "description": self._description_tache(),
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "res_id": self.task_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def _description_tache(self):
        """Le pourquoi part avec la tâche : sinon elle arrive sans son dossier.

        Écrite pour qui fera le travail : dans la langue du responsable de
        l'écart, à défaut dans celle du livrable. Un paragraphe par ligne : la
        description est un champ Html, où un saut de ligne se colle.
        """
        self.ensure_one()
        moi = self.with_context(lang=self.responsable_id.lang
                                or self.cible_id._langue_livrable())
        env = moi.env
        lignes = [env._("Gap found between \"%s\" and \"%s\".",
                        moi.origine_id.display_name, moi.cible_id.display_name)]
        if self.diagram_titre:
            lignes.append(env._("Level: %s", self.diagram_titre))
        if self.intention:
            intentions = dict(
                self._fields["intention"]._description_selection(env))
            lignes.append(env._("Intent: %s", intentions[self.intention]))
        if (self.gain or "").strip():
            lignes.append(env._("Expected gain: %s", self.gain.strip()))
        return Markup("").join(Markup("<p>%s</p>") % l for l in lignes)


class BfProcessCible(models.Model):
    """Le semis des écarts, et les teintes qu'ils commandent."""

    _inherit = "bf.process"

    ecart_ids = fields.One2many(
        "bf.process.ecart", "cible_id", string="Gaps")
    ecart_count = fields.Integer(compute="_compute_ecarts")
    ecart_retenu_count = fields.Integer(compute="_compute_ecarts")
    ecart_fait_count = fields.Integer(compute="_compute_ecarts")
    ecart_a_decider_count = fields.Integer(compute="_compute_ecarts")
    taux_transformation = fields.Float(
        string="Transformation", compute="_compute_ecarts",
        help="Share of selected gaps that are done. Gaps still to decide "
             "do not count: you do not measure progress on a plan that "
             "has not been settled.")

    @api.depends("ecart_ids.etat")
    def _compute_ecarts(self):
        for rec in self:
            ecarts = rec.ecart_ids
            retenus = ecarts.filtered(lambda e: e.etat == "retenu")
            faits = ecarts.filtered(lambda e: e.etat == "fait")
            rec.ecart_count = len(ecarts)
            rec.ecart_retenu_count = len(retenus)
            rec.ecart_fait_count = len(faits)
            rec.ecart_a_decider_count = len(
                ecarts.filtered(lambda e: e.etat == "a_decider"))
            arretes = len(retenus) + len(faits)
            rec.taux_transformation = (len(faits) / arretes * 100
                                       if arretes else 0.0)

    # ------------------------------------------------------------------ semis
    def action_semer_ecarts(self):
        """Rejoue le diff et réconcilie, sans jamais écraser la part humaine."""
        self.ensure_one()
        if self.nature != "cible":
            raise UserError(_(
                "Gaps are seeded on a target process: \"%s\" describes "
                "the current state.") % self.display_name)
        if not self.origine_id:
            raise UserError(_(
                "\"%s\" does not say which current state it is drawn from."
            ) % self.display_name)

        Ecart = self.env["bf.process.ecart"]
        # le libellé d'un écart est stocké et part au plan de transformation du
        # livrable : il se compose dans la langue du client
        livrable = self._livrable()
        lignes = calculer_ecarts(livrable.origine_id, livrable)
        existants = {e.cle: e for e in self.ecart_ids if e.source == "seme"}
        noeuds_avant = self.origine_id._noeuds_par_cle()
        noeuds_apres = self._noeuds_par_cle()

        vus, cree, maj = set(), 0, 0
        for i, ligne in enumerate(lignes, 1):
            vus.add(ligne["cle"])
            cle_noeud = (ligne["diagram_code"], ligne["node_code"])
            vals = {
                "sequence": i * 10,
                "genre": ligne["genre"],
                "portee": ligne["portee"],
                "libelle": ligne["libelle"],
                "diagram_code": ligne["diagram_code"],
                "diagram_titre": ligne["diagram_titre"],
                "node_code": ligne["node_code"],
                "node_actuel_id": (noeuds_avant.get(cle_noeud).id
                                   if ligne["node_code"]
                                   and cle_noeud in noeuds_avant else False),
                "node_cible_id": (noeuds_apres.get(cle_noeud).id
                                  if ligne["node_code"]
                                  and cle_noeud in noeuds_apres else False),
                "caduc": False,
            }
            ancien = existants.get(ligne["cle"])
            if ancien:
                ancien.write(vals)
                maj += 1
            else:
                Ecart.create(dict(vals, cible_id=self.id, cle=ligne["cle"],
                                  source="seme"))
                cree += 1

        perimes = [e for e in existants.values() if e.cle not in vus]
        gardes = [e for e in perimes if e._porte_du_travail()]
        jetables = [e for e in perimes if not e._porte_du_travail()]
        if gardes:
            Ecart.union(*gardes).write({"caduc": True})
        if jetables:
            Ecart.union(*jetables).unlink()

        self.message_post(body=self._compte_rendu_semis(
            cree, maj, len(gardes), len(jetables)))
        return {
            "type": "ir.actions.act_window",
            "name": _("Gaps"),
            "res_model": "bf.process.ecart",
            "view_mode": "list,form",
            "domain": [("cible_id", "=", self.id)],
            "context": {"default_cible_id": self.id},
        }

    def _compte_rendu_semis(self, cree, maj, gardes, jetables):
        """Dire ce qui a bougé, y compris ce qui est parti."""
        self.ensure_one()
        bouts = [Markup(_(
            "Gaps seeded against <b>%s</b>: %d new, %d refreshed."))
            % (self.origine_id.display_name, cree, maj)]
        if gardes:
            bouts.append(_(
                "%d gap(s) no longer appear on the maps but carried work: "
                "kept and marked as lapsed.") % gardes)
        if jetables:
            bouts.append(_(
                "%d gap(s) that no longer applied, with nothing written "
                "on them, were removed.") % jetables)
        if not self.ecart_ids:
            bouts.append(_("Both maps say the same thing."))
        # `Markup.join` échappe les morceaux en `str` et garde le gras du premier
        return Markup(" ").join(bouts)

    def _noeuds_par_cle(self):
        """{(code du niveau, code du nœud): nœud} pour cette carte."""
        self.ensure_one()
        return {(d.code, n.code): n
                for d in self.diagram_ids for n in d.node_ids}

    # ----------------------------------------------------------------- teintes
    def _teintes(self, cote):
        """Les teintes de la vue delta pour un côté, depuis les écarts.

        `cote` vaut « avant » (la carte de l'état actuel) ou « apres » (celle
        du processus souhaité). Un écart mis de côté ne teint rien : décider
        de ne pas faire un changement, c'est décider qu'il n'y en a pas.
        """
        self.ensure_one()
        out = {}
        for e in self.ecart_ids:
            if e.etat == "ecarte" or e.caduc or not e.node_code:
                continue
            if cote not in COTES.get(e.genre, ()):
                continue
            teinte = TEINTES.get(e.genre, "ambre")
            cle = (e.diagram_code, e.node_code)
            # rouge et vert l'emportent sur ambre : une étape qui disparaît ne
            # se lit pas « modifiée »
            if out.get(cle) in ("rouge", "vert"):
                continue
            out[cle] = teinte
        return out

    def _cible_pour_teintes(self):
        """La cible dont les teintes s'appliquent à CETTE carte, s'il y en a une.

        Sur un processus souhaité, c'est lui-même. Sur un état actuel, ce
        n'est net que s'il n'a qu'une seule cible : en teindre une carte
        d'après l'une des trois sans le dire ferait lire au client un plan qui
        n'est pas celui qu'il regarde.
        """
        self.ensure_one()
        if self.nature == "cible":
            return self
        cibles = self.cible_ids
        return cibles if len(cibles) == 1 else self.browse()

    def teintes_de_la_carte(self):
        """Les teintes à poser sur CETTE carte, ou rien si c'est ambigu."""
        self.ensure_one()
        cible = self._cible_pour_teintes()
        if not cible:
            return {}
        return cible._teintes("apres" if self.nature == "cible" else "avant")

    def _legende_delta(self):
        """La phrase qui accompagne les teintes à l'écran, et dit d'où elles viennent.

        Sur un état actuel, la légende nomme la cible : le lecteur doit savoir
        quel plan il regarde, surtout le jour où il y en aura deux.
        """
        self.ensure_one()
        cible = self._cible_pour_teintes()
        if not cible:
            return ""
        if self.nature == "cible":
            return _("Delta view against \"%s\": green = added, amber = "
                     "changed."
                     ) % (self.origine_id.display_name or "")
        return _("Delta view based on \"%s\": red = removed, amber = changed."
                 ) % cible.display_name
