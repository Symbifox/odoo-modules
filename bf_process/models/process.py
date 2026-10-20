# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .structure import ACTIVITES


class BfProcess(models.Model):
    """Une cartographie de processus, versionnée."""

    _name = "bf.process"
    _description = "Process mapping"
    _inherit = ["mail.thread"]
    _order = "name, version"

    name = fields.Char(string="Process", required=True, tracking=True)
    code = fields.Char(string="Code", help="Prefix of the exported BPMN IDs.")
    version = fields.Char(string="Version", default="1.0", required=True, tracking=True)
    state = fields.Selection(
        [("brouillon", "Draft"), ("valide", "Validated"), ("obsolete", "Obsolete")],
        string="State", default="brouillon", required=True, tracking=True)
    pool_name = fields.Char(
        string="Main pool", required=True,
        help="The organization that performs the process. Performers are "
             "lanes; external participants are black-box pools.")
    partner_id = fields.Many2one("res.partner", string="Client", tracking=True)
    project_id = fields.Many2one("project.project", string="Project", tracking=True)
    source = fields.Text(
        string="Source",
        help="Where the map comes from, and what was not done. An AS-IS "
             "map without its source is not a deliverable.")
    diagram_ids = fields.One2many("bf.process.diagram", "process_id", string="Levels")
    diagram_count = fields.Integer(compute="_compute_counts")
    node_count = fields.Integer(compute="_compute_counts")
    active = fields.Boolean(default=True)

    # --- lignée des versions -------------------------------------------------
    version_precedente_id = fields.Many2one(
        "bf.process", string="Previous version", ondelete="set null", copy=False)
    version_suivante_ids = fields.One2many(
        "bf.process", "version_precedente_id", string="Next versions")

    # --- état actuel ou processus souhaité -----------------------------------
    # Deux branches, pas une ligne. Une lignée de versions dit « la réalité a
    # changé » ; un processus souhaité dit « on veut qu'elle change ». Les
    # confondre ferait qu'un état actuel recartographié après coup ne se
    # comparerait plus à rien de cohérent, et la carte cesserait d'être le
    # référentiel de l'état actuel, ce qui est sa raison d'être.
    nature = fields.Selection(
        [("actuel", "Current state"), ("cible", "Target process")],
        string="Map type", default="actuel", required=True, tracking=True,
        help="What the map describes: what happens today, or what we want "
             "to happen.")
    origine_id = fields.Many2one(
        "bf.process", string="Drawn from", ondelete="restrict",
        copy=False, index=True,
        help="The current-state version this target process is drawn "
             "from. Gaps are computed against it, and it does not move "
             "when the current state is remapped: the target was drawn "
             "from that snapshot.")
    cible_ids = fields.One2many(
        "bf.process", "origine_id", string="Target processes")
    cible_count = fields.Integer(compute="_compute_cible_count")

    # --- registre de validation ---------------------------------------------
    activite_count = fields.Integer(compute="_compute_validation")
    validee_count = fields.Integer(compute="_compute_validation")
    taux_validation = fields.Float(
        string="Validation", compute="_compute_validation",
        help="Share of activities validated by the owner AND by a performer.")
    conteste_count = fields.Integer(compute="_compute_validation")

    # `nature` entre dans la clé : l'état actuel et le processus souhaité
    # portent le même nom de processus, et chacun a sa propre suite de
    # versions. Sans elle, dessiner une cible obligerait à inventer un numéro
    # de version qui ne veut rien dire.
    _sql_constraints = [
        ("name_version_uniq", "unique (name, nature, version)",
         "This version of this process already exists."),
    ]

    @api.constrains("nature", "origine_id")
    def _check_origine(self):
        """Une cible sait d'où elle part ; un état actuel ne part de rien."""
        for rec in self:
            if rec.nature == "cible" and not rec.origine_id:
                raise ValidationError(_(
                    "A target process is drawn from a current state. "
                    "Without one, there is no gap to show, just a second "
                    "map."))
            if rec.nature == "actuel" and rec.origine_id:
                raise ValidationError(_(
                    "A current-state map is not drawn from anything: it "
                    "describes what happens."))
            if rec.origine_id == rec:
                raise ValidationError(_(
                    "A map cannot be drawn from itself."))
            if rec.origine_id and rec.origine_id.nature != "actuel":
                raise ValidationError(_(
                    "\"%s\" is already a target process: a target is "
                    "drawn from the current state, not from another "
                    "target.") % rec.origine_id.display_name)

    @api.depends("cible_ids")
    def _compute_cible_count(self):
        for rec in self:
            rec.cible_count = len(rec.cible_ids)

    @api.depends("diagram_ids", "diagram_ids.node_ids")
    def _compute_counts(self):
        for rec in self:
            rec.diagram_count = len(rec.diagram_ids)
            rec.node_count = sum(len(d.node_ids) for d in rec.diagram_ids)

    @api.depends("diagram_ids.node_ids.validation")
    def _compute_validation(self):
        for rec in self:
            activites = rec.diagram_ids.mapped("node_ids").filtered(
                lambda n: n.kind in ACTIVITES)
            validees = activites.filtered(lambda n: n.validation == "validee")
            rec.activite_count = len(activites)
            rec.validee_count = len(validees)
            rec.conteste_count = len(activites.filtered(
                lambda n: n.validation == "conteste"))
            rec.taux_validation = (len(validees) / len(activites) * 100
                                   if activites else 0.0)

    @api.depends("name", "version", "nature")
    @api.depends_context("lang")
    def _compute_display_name(self):
        """La nature se lit dans le nom : deux cartes portent le même titre.

        Sans elle, une liste déroulante offrirait deux « Tenir la comptabilité
        v1.0 » impossibles à départager, et le mauvais serait choisi une fois
        sur deux.

        ⚠️ C'était un `name_get`, et Odoo 18 ne l'appelle plus : la méthode
        restait là, juste, et sans effet. Le nom affiché avait perdu son numéro
        de version sans que rien ne le signale. Un remplacement qui ne casse
        rien est le plus difficile à voir.
        """
        suffixe = _(" (target)")
        for rec in self:
            rec.display_name = (f"{rec.name} v{rec.version}"
                                + (suffixe if rec.nature == "cible" else ""))

    # --- langue du livrable ----------------------------------------------------
    @api.model
    def _langue_du_client(self, partner=None):
        """La langue d'un document qui part chez ce client.

        La sienne, à défaut celle de la société, à défaut celle de la session.
        Jamais celle de qui imprime : le livrable est une pièce du client.
        """
        return ((partner.lang if partner else False)
                or self.env.company.partner_id.lang
                or self.env.lang or "en_US")

    def _langue_livrable(self):
        """La langue de tout ce qui part chez le client de CETTE carte."""
        self.ensure_one()
        return self._langue_du_client(self.partner_id)

    def _livrable(self):
        """La carte, dans la langue de son livrable : `._livrable().env._(…)`."""
        self.ensure_one()
        return self.with_context(lang=self._langue_livrable())

    def _nom_par_defaut(self, genre):
        """Le nom d'un nœud posé sans libellé, dans la langue du livrable.

        Il s'écrit dans la carte, donc il finit au PDF du client : « À nommer »
        dans la langue de la session aurait imprimé un mot de l'atelier
        plutôt qu'un mot du client.
        """
        env = self._livrable().env
        if genre == "note":
            return env._("To be written")
        if genre in ACTIVITES:
            return env._("To be named")
        return False

    # --- gel ------------------------------------------------------------------
    MODIFIABLE_APRES_VALIDATION = {
        "state", "active", "partner_id", "project_id", "source",
        "version_precedente_id", "message_follower_ids", "message_ids",
        "activity_ids",
    }

    def write(self, vals):
        if not self.env.context.get("bf_process_degel"):
            touche = set(vals) - self.MODIFIABLE_APRES_VALIDATION
            geles = self.filtered(lambda p: p.state == "valide")
            if touche and geles:
                raise UserError(_(
                    "\"%s\" is a validated version: its content can no "
                    "longer be edited. Create the next version, or reopen "
                    "this one."
                ) % ", ".join(f"{p.name} v{p.version}" for p in geles))
        return super().write(vals)

    def unlink(self):
        geles = self.filtered(lambda p: p.state == "valide")
        if geles and not self.env.context.get("bf_process_degel"):
            raise UserError(_(
                "A validated version cannot be deleted. Mark it obsolete."))
        return super(BfProcess, self.with_context(bf_process_degel=True)).unlink()

    def action_valider(self):
        """Fige la version. C'est ce qui rend la carte cit​able."""
        for rec in self:
            if not rec.diagram_ids:
                raise UserError(_("A process map without a level cannot "
                                  "be validated."))
            rec.with_context(bf_process_degel=True).state = "valide"
            rec.message_post(body=Markup(_(
                "Version <b>%s</b> validated and frozen: %d of %d "
                "activities validated in the register.")) % (
                    rec.version, rec.validee_count, rec.activite_count))
        return True

    def action_rouvrir(self):
        """Dégèle, et le dit. Rouvrir en silence serait pire que ne pas geler."""
        for rec in self:
            rec.with_context(bf_process_degel=True).state = "brouillon"
            rec.message_post(body=Markup(_(
                "Version <b>%s</b> reopened: it can be edited again."))
                % rec.version)
        return True

    def _version_suivante(self):
        """1.0 → 1.1 ; 2 → 3 ; sinon on suffixe.

        Incrémente au-delà d'un numéro déjà pris : une version supprimée puis
        recréée, ou un essai laissé derrière, ne doit pas coller sur un numéro
        vivant — la contrainte d'unicité le refuserait, en aval, sans dire
        pourquoi le numéro choisi ne convenait pas.
        """
        self.ensure_one()
        prises = set(self.env["bf.process"].search(
            [("name", "=", self.name),
             ("nature", "=", self.nature)]).mapped("version"))
        morceaux = (self.version or "1.0").split(".")
        if not morceaux[-1].isdigit():
            candidate, n = f"{self.version}-bis", 2
            while candidate in prises:
                candidate, n = f"{self.version}-bis{n}", n + 1
            return candidate
        while True:
            morceaux[-1] = str(int(morceaux[-1]) + 1)
            candidate = ".".join(morceaux)
            if candidate not in prises:
                return candidate

    def action_nouvelle_version(self):
        """Repart de cette version, sans toucher à celle-ci.

        Le contenu passe par la forme d'échange puis par le chargeur : mêmes
        codes, mêmes identifiants BPMN, donc les deux versions se comparent
        ligne à ligne. La validation, elle, ne se recopie pas — une nouvelle
        version se revalide.
        """
        self.ensure_one()
        nouvelle = self.copy({
            "name": self.name,
            "version": self._version_suivante(),
            "state": "brouillon",
            "version_precedente_id": self.id,
            # `origine_id` ne se recopie pas (copy=False) : sans ce report, la
            # version suivante d'un processus souhaité perdrait la photo dont
            # il est tiré et la contrainte la refuserait, sans dire laquelle.
            "origine_id": self.origine_id.id or False,
            "diagram_ids": [],
        })
        nouvelle._charger_niveaux(self.to_dicts())
        nouvelle.message_post(body=Markup(_(
            "Taken over from version <b>%s</b>: %d levels, %d nodes. The "
            "validation register starts over.")) % (
                self.version, len(nouvelle.diagram_ids), nouvelle.node_count))
        return {
            "type": "ir.actions.act_window",
            "name": _("New version"),
            "res_model": "bf.process",
            "res_id": nouvelle.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_dessiner_cible(self):
        """Ouvre une branche : le processus souhaité, dessiné d'après celle-ci.

        Même mécanique que la version suivante, et pour la même raison : le
        contenu repasse par la forme d'échange, donc mêmes codes et mêmes
        identifiants BPMN, donc les écarts se calculent étape par étape plutôt
        que de conclure que tout a disparu et que tout est apparu.

        Ce qui ne suit pas, et c'est délibéré : le registre de validation (une
        cible ne s'est pas encore produite, personne ne peut certifier qu'elle
        se passe comme ça) et la prose du livrable (les constats de l'état
        actuel ne sont pas ceux de la cible ; les recopier ferait signer à la
        cible des observations qu'on n'a pas faites sur elle).
        """
        self.ensure_one()
        if self.nature != "actuel":
            raise UserError(_(
                "\"%s\" is already a target process. A target is drawn "
                "from the current state.") % self.display_name)
        if not self.diagram_ids:
            raise UserError(_(
                "\"%s\" has no level: there is nothing to start from to "
                "draw what we would want instead.") % self.name)
        cible = self.copy({
            "name": self.name,
            "nature": "cible",
            "origine_id": self.id,
            "version": self._version_cible(),
            "state": "brouillon",
            "version_precedente_id": False,
            "diagram_ids": [],
            "section_ids": [],
        })
        cible._charger_niveaux(self.to_dicts())
        cible.message_post(body=Markup(_(
            "Target process drawn from <b>%s</b>: %d levels, %d nodes, "
            "identical for now. Gaps appear as the target is edited."
        )) % (self.display_name, len(cible.diagram_ids), cible.node_count))
        self.message_post(body=Markup(_(
            "A target process was drawn from this version: <b>%s</b>."))
            % cible.display_name)
        return {
            "type": "ir.actions.act_window",
            "name": _("Target process"),
            "res_model": "bf.process",
            "res_id": cible.id,
            "view_mode": "form",
            "target": "current",
        }

    def _version_cible(self):
        """Le premier numéro libre dans la branche souhaitée de ce processus.

        Les deux branches ont chacune leur suite : une cible repart à 1.0
        même si l'état actuel en est à sa v3. Le numéro dit l'âge de la
        cible, pas celui de la photo dont elle est tirée.
        """
        self.ensure_one()
        prises = set(self.env["bf.process"].search(
            [("name", "=", self.name), ("nature", "=", "cible")]).mapped("version"))
        n = 1
        while f"{n}.0" in prises:
            n += 1
        return f"{n}.0"

    def action_ouvrir_cibles(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Target processes"),
            "res_model": "bf.process",
            "view_mode": "list,form",
            "domain": [("origine_id", "=", self.id)],
        }

    def action_comparer(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Compare two maps"),
            "res_model": "bf.process.compare.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_source_id": self.id,
                        "default_cible_id": (self.origine_id.id
                                             or self.version_precedente_id.id
                                             or False)},
        }

    def action_fusionner(self):
        """Rapatrier un `.bpmn` retouché dans CETTE carte, plutôt qu'à côté."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Merge a BPMN 2.0 file"),
            "res_model": "bf.process.merge.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_process_id": self.id},
        }

    def action_ouvrir_niveaux(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Levels"),
            "res_model": "bf.process.diagram",
            "view_mode": "list,form",
            "domain": [("process_id", "=", self.id)],
            "context": {"default_process_id": self.id},
        }


class BfProcessDiagram(models.Model):
    """Un niveau : une page de la carte, autonome, avec son propre plan.

    Le niveau 1 donne la vue d'ensemble ; chaque sous-processus replié y ouvre
    son propre niveau. La règle « les portes du parent portent les états de fin
    de l'enfant » s'encode par `parent_node_ids` — au pluriel, parce
    qu'un sous-processus commun est appelé de plusieurs endroits.
    """

    _name = "bf.process.diagram"
    _inherit = ["bf.process.gel"]
    _description = "Process map level"
    _order = "process_id, sequence, id"

    process_id = fields.Many2one(
        "bf.process", string="Process", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(string="Order", default=10)
    title = fields.Char(string="Title", required=True)
    level = fields.Char(
        string="Level", help="Displayed label, for example \"Level 2\".")
    code = fields.Char(
        string="Code", required=True,
        help="Short identifier of the level, unique within the process.")
    bpmn_id = fields.Char(
        string="BPMN ID", required=True, copy=False,
        help="Stable, never regenerated: it is what lets a reimport merge "
             "instead of duplicating.")
    parent_node_ids = fields.One2many(
        "bf.process.node", "child_diagram_id", string="Opened from",
        help="The subprocesses that open this page. There can be several: "
             "a shared subprocess is called from several places.")
    parent_count = fields.Integer(compute="_compute_parent_count")
    pool_name = fields.Char(
        string="Main pool",
        help="Empty: the process's pool name applies.")
    flat = fields.Boolean(
        string="Unfolded map",
        help="Single page gathering all levels.")

    # --- géométrie ----------------------------------------------------------
    col_w = fields.Float(string="Column width", default=168.0)
    row_h = fields.Float(string="Row height", default=100.0)
    lane_pad = fields.Float(string="Lane padding", default=50.0)
    ext_header = fields.Float(
        string="External pool height", default=0.0,
        help="Empty: measured from the longest participant name. A value "
             "here is an override, and it survives recomputation.")

    lane_ids = fields.One2many("bf.process.lane", "diagram_id", string="Lanes")
    pool_ids = fields.One2many("bf.process.pool", "diagram_id", string="External "
                                                                       "pools")
    node_ids = fields.One2many("bf.process.node", "diagram_id", string="Nodes")
    flow_ids = fields.One2many("bf.process.flow", "diagram_id", string="Flows")
    message_ids = fields.One2many(
        "bf.process.message", "diagram_id", string="Message flow")
    node_count = fields.Integer(compute="_compute_node_count")

    _sql_constraints = [
        ("code_uniq", "unique (process_id, code)",
         "This level code is already used in this process."),
        ("bpmn_id_uniq", "unique (process_id, bpmn_id)",
         "This BPMN ID is already used in this process."),
    ]

    @api.depends("node_ids")
    def _compute_node_count(self):
        for rec in self:
            rec.node_count = len(rec.node_ids)

    @api.depends("parent_node_ids")
    def _compute_parent_count(self):
        for rec in self:
            rec.parent_count = len(rec.parent_node_ids)

    @api.depends("level", "title")
    def _compute_display_name(self):
        """🔴 Un niveau s'affichait « bf.process.diagram,90 ».

        Le modèle n'a pas de champ `name` et son `name_get` n'est plus appelé
        depuis Odoo 18 : le repli est alors le couple modèle-identifiant, servi
        tel quel dans les fils d'Ariane et les listes déroulantes. Le calcul
        est ce qu'Odoo 18 lit vraiment.
        """
        for rec in self:
            rec.display_name = " · ".join(x for x in (rec.level, rec.title) if x)

    def to_dict(self, teintes=None):
        """Rend le niveau sous la forme que les générateurs attendent.

        `teintes` est indexé par (code du niveau, code du nœud) : la même
        table sert à toutes les pages, et un code de nœud n'est unique que
        dans son niveau.
        """
        self.ensure_one()
        teintes = teintes or {}
        d = {
            "title": self.title,
            "pool": self.pool_name or self.process_id.pool_name,
            "col_w": self.col_w,
            "row_h": self.row_h,
            "lane_pad": self.lane_pad,
            "ext_header": self.ext_header,
            "lanes": [ln.to_dict() for ln in self.lane_ids],
            "ext": [p.to_dict() for p in self.pool_ids],
            "nodes": [n.to_dict(teintes.get((self.code, n.code)))
                      for n in self.node_ids],
            "flows": [f.to_dict() for f in self.flow_ids],
            "msgs": [m.to_dict() for m in self.message_ids],
        }
        if self.level:
            d["level"] = self.level
        if self.flat:
            d["flat"] = True
        return d
