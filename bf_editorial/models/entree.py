# -*- coding: utf-8 -*-
"""L'entrée éditoriale : l'unité de travail du module.

Une entrée peut porter un billet de blogue, ou n'être qu'un billet social
autonome. Ce qu'elle stocke, ce sont les décisions et le contexte de
production. Les mesures — mots, visites, dérive de version, complétude des
langues — se calculent.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import reparation
from .version import IGNORED_SLOTS, READY_STATES

# Les motifs de refus qu'un humain peut signer. Ce sont les deux motifs de
# JUGEMENT : la QA a vu quelque chose qu'on accepte de publier quand même, ou
# l'article est plus court que le plancher et on le sort quand même. Les
# autres motifs sont des FAITS — une dépendance pas sortie, une source morte,
# une langue pas relue — et un fait ne se signe pas, il se règle.
WAIVABLE = ("qa_findings", "word_floor")


class EditorialEntry(models.Model):
    _name = "bf.editorial.entry"
    _description = "Entrée éditoriale"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "planned_date desc, id desc"

    # --- identité ---------------------------------------------------------
    name = fields.Char(
        string="Titre de travail", required=True, tracking=True,
        help="Il survit aux changements de titre publié, ce qui permet de"
             " suivre une entrée dont l'article a été renommé.",
    )
    active = fields.Boolean(string="Actif", default=True)
    calendar_id = fields.Many2one(
        "bf.editorial.calendar", string="Calendrier",
        ondelete="restrict", index=True, tracking=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Société", related="calendar_id.company_id",
        store=True, index=True,
    )
    post_id = fields.Many2one(
        "blog.post", string="Billet", ondelete="set null", index=True,
        tracking=True,
        help="Vide pour une publication qui ne passe pas par le blogue.",
    )
    kind = fields.Selection(
        [
            ("blog", "Article de blogue"),
            ("social", "Publication sociale"),
            ("newsletter", "Infolettre"),
            ("video", "Vidéo"),
        ],
        string="Nature", default="blog", required=True, tracking=True,
    )
    pillar_id = fields.Many2one(
        "blog.tag.category", string="Pilier",
        domain=[("is_pillar", "=", True)], index=True, tracking=True,
    )
    tag_ids = fields.Many2many("blog.tag", string="Sujets")
    color = fields.Integer(string="Couleur", related="pillar_id.color")

    # --- cycle ------------------------------------------------------------
    stage_id = fields.Many2one(
        "bf.editorial.stage", string="Étape", index=True, tracking=True,
        group_expand="_group_expand_stage", default=lambda s: s._default_stage(),
        ondelete="restrict",
    )
    planned_date = fields.Date(string="Date prévue", tracking=True)
    scheduled_publish_date = fields.Datetime(
        string="Publication différée", tracking=True,
        help="Une fois la date approuvée, la publication part toute seule —"
             " mais seulement si la garde de pré-vol est verte à ce moment-là.",
    )
    published_date = fields.Datetime(
        string="Publié le", tracking=True, readonly=True, copy=False,
    )
    timeline_date = fields.Date(
        string="Date au calendrier", compute="_compute_timeline_date", store=True,
        help="Date prévue si elle existe, sinon date de publication. C'est"
             " celle que la vue calendrier affiche : une vue calée sur la"
             " seule date prévue laisse invisible tout ce qui est déjà sorti.",
    )
    user_id = fields.Many2one(
        "res.users", string="Responsable", default=lambda self: self.env.user,
        tracking=True,
    )
    reviewer_id = fields.Many2one("res.users", string="Relecteur", tracking=True)
    task_id = fields.Many2one("project.task", string="Tâche liée")
    campaign_id = fields.Many2one("utm.campaign", string="Campagne")

    # --- angle ------------------------------------------------------------
    hook = fields.Text(
        string="Accroche",
        help="La première phrase, ou l'idée qui doit accrocher.",
    )
    thesis = fields.Text(
        string="Thèse", help="Ce que l'article défend, en une phrase.",
    )
    reader_problem = fields.Text(string="Problème du lecteur")
    promise = fields.Text(string="Promesse")
    cta = fields.Char(string="Appel à l'action")

    # --- référencement ----------------------------------------------------
    target_keyword = fields.Char(string="Mot-clé visé")
    search_intent = fields.Selection(
        [
            ("informational", "S'informer"),
            ("comparison", "Comparer"),
            ("transactional", "Choisir un fournisseur"),
            ("navigational", "Trouver une page précise"),
        ],
        string="Intention de recherche",
    )
    pattern = fields.Selection(
        [
            ("alternative", "Alternative à"),
            ("howto", "Comment faire"),
            ("comparison", "Comparatif"),
            ("posture", "Prise de position"),
            ("product", "Présentation produit"),
            ("case", "Étude de cas"),
        ],
        string="Patron",
        help="Le patron « alternative à » domine historiquement le palmarès.",
    )
    competitor = fields.Char(string="Concurrent visé")

    # --- dépendances ------------------------------------------------------
    depends_on_ids = fields.Many2many(
        "bf.editorial.entry", "bf_editorial_entry_dep_rel", "entry_id",
        "depends_id", string="Dépend de",
        help="Entrées qui doivent être publiées avant celle-ci — un billet"
             " publié ne doit jamais pointer un brouillon.",
    )
    blocks_ids = fields.Many2many(
        "bf.editorial.entry", "bf_editorial_entry_dep_rel", "depends_id",
        "entry_id", string="Bloque", readonly=True,
    )
    blocked_reason = fields.Char(string="Motif de blocage", tracking=True)
    is_blocked = fields.Boolean(
        string="Bloquée", compute="_compute_is_blocked", store=True,
    )
    # Calcul SÉPARÉ, et c'est délibéré : Odoo avertit à chaque chargement du
    # registre quand une même méthode calcule des champs de « store »
    # différents. Les deux partagent leurs dépendances, pas leur stockage.
    blocking_summary = fields.Text(
        string="Ce qui bloque", compute="_compute_blocking_summary",
    )

    # --- véracité ---------------------------------------------------------
    fact_check_date = fields.Date(string="Fact-check du", tracking=True)
    fact_checked_by = fields.Many2one("res.users", string="Fact-check par")
    source_ids = fields.One2many(
        "bf.editorial.source", "entry_id", string="Sources",
    )
    claim_ids = fields.One2many(
        "bf.editorial.claim", "entry_id", string="Affirmations",
    )
    unverified_claim_count = fields.Integer(
        string="Affirmations non vérifiées", compute="_compute_claim_stats",
    )
    dead_source_count = fields.Integer(
        string="Sources mortes", compute="_compute_claim_stats",
    )

    # --- fraîcheur produit ------------------------------------------------
    subject_module_id = fields.Many2one(
        "ir.module.module", string="Module documenté",
        help="Renseigné, le module signale de lui-même que la version a bougé"
             " depuis le fact-check.",
    )
    source_version = fields.Char(
        string="Version au fact-check",
        help="La version du module documentée au moment de la vérification.",
    )
    current_version = fields.Char(
        string="Version actuelle", compute="_compute_version_drift",
    )
    version_drift = fields.Boolean(
        string="Dérive de version", compute="_compute_version_drift",
        search="_search_version_drift",
    )

    # --- créneaux de langue et qualité ------------------------------------
    version_ids = fields.One2many(
        "bf.editorial.version", "entry_id", string="Créneaux de langue",
    )
    checklist_ids = fields.One2many(
        "bf.editorial.checklist", "entry_id", string="Liste de contrôle",
    )
    open_checklist_count = fields.Integer(
        string="Restes ouverts", compute="_compute_checklist_stats",
    )
    word_count = fields.Integer(
        string="Mots (langue source)", compute="_compute_language_state",
    )
    word_floor = fields.Integer(
        string="Plancher", related="calendar_id.word_floor", readonly=True,
    )
    langs_complete = fields.Boolean(
        string="Toutes les langues livrées", compute="_compute_language_state",
        search="_search_langs_complete",
    )
    langs_ready = fields.Boolean(
        string="Toutes les langues relues", compute="_compute_language_state",
        search="_search_langs_ready",
        help="Vrai quand chaque langue exigée porte un créneau relu ou déjà"
             " publié. C'est cet état que la garde de pré-vol contrôle :"
             " exiger « publiée » AVANT de publier ne se satisfait jamais.",
    )
    language_summary = fields.Text(
        string="État des langues", compute="_compute_language_state",
    )

    qa_state = fields.Selection(
        [
            ("todo", "À passer"),
            ("clean", "Propre"),
            ("findings", "Constats"),
        ],
        string="État QA", default="todo", copy=False, tracking=True,
    )
    qa_findings = fields.Text(string="Constats QA", readonly=True, copy=False)
    qa_last_run = fields.Datetime(string="QA passée le", readonly=True, copy=False)
    qa_run_by = fields.Many2one(
        "res.users", string="QA passée par", readonly=True, copy=False,
    )

    # --- dérogation -------------------------------------------------------
    # La QA dit ce qu'elle voit ; la dérogation dit ce qu'un humain accepte de
    # publier quand même. Les deux restent lisibles côte à côte : rien n'efface
    # un constat, c'est le REFUS qui cède, en nommant qui le fait céder.
    qa_waived = fields.Boolean(
        string="Sous dérogation", readonly=True, copy=False, tracking=True,
    )
    qa_waiver_reason = fields.Text(
        string="Motif de la dérogation", readonly=True, copy=False,
    )
    qa_waiver_problems = fields.Text(
        string="Motifs couverts", readonly=True, copy=False,
        help="Les motifs de refus, mot pour mot, tels qu'ils se lisaient à la"
             " signature. La dérogation ne couvre QUE ceux-là : un motif qui"
             " change de texte n'est plus le motif signé.",
    )
    qa_waiver_findings = fields.Text(
        string="Constats couverts", readonly=True, copy=False,
        help="Les constats de QA au moment de la signature. Ils servent"
             " d'empreinte : dès que la QA en rapporte d'autres, la"
             " dérogation ne les couvre plus.",
    )
    qa_waived_by = fields.Many2one(
        "res.users", string="Dérogation signée par", readonly=True, copy=False,
    )
    qa_waived_on = fields.Datetime(
        string="Dérogation signée le", readonly=True, copy=False,
    )
    qa_waiver_stale = fields.Boolean(
        string="Dérogation périmée", compute="_compute_qa_waiver_stale",
        help="Vrai quand une dérogation existe mais que le texte a bougé"
             " depuis. Elle ne couvre alors plus rien, et la garde reprend.",
    )

    preflight_ok = fields.Boolean(
        string="Pré-vol vert", compute="_compute_preflight",
        search="_search_preflight_ok",
    )
    preflight_summary = fields.Text(
        string="Pré-vol", compute="_compute_preflight",
    )

    # --- mesures brutes ---------------------------------------------------
    raw_visits = fields.Integer(
        string="Visites (brut)", related="post_id.visits", readonly=True,
        help="Compteur natif d'Odoo. Il compte aussi les robots : à lire comme"
             " un ordre de grandeur, pas comme un lectorat.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Poser d'emblée les restes humains prévus par les gabarits.

        Sans ce crochet les gabarits ne servaient à rien : rien n'appelait
        ``_apply_templates``, et la méthode étant privée elle n'était pas non
        plus joignable depuis l'extérieur. Une entrée naissait sans liste.
        """
        entries = super().create(vals_list)
        for entry in entries:
            self.env["bf.editorial.checklist"]._apply_templates(entry)
        return entries

    def action_apply_checklist_templates(self):
        """Reposer les gabarits, par exemple après un changement de pilier."""
        for entry in self:
            self.env["bf.editorial.checklist"]._apply_templates(entry)
        return True

    # --- valeurs par défaut ----------------------------------------------
    @api.model
    def _default_stage(self):
        return self.env["bf.editorial.stage"].search([], order="sequence", limit=1)

    @api.model
    def _group_expand_stage(self, stages, domain):
        return self.env["bf.editorial.stage"].search([], order="sequence")

    # --- calculs ----------------------------------------------------------
    def _blocking_reasons(self):
        """Les motifs de blocage, partagés par les deux calculs."""
        self.ensure_one()
        reasons = []
        if self.blocked_reason:
            reasons.append(self.blocked_reason)
        for dep in self.depends_on_ids.filtered(
            lambda e: not e.stage_id.is_closing
        ):
            reasons.append(_("« %s » n'est pas publiée", dep.name))
        return reasons

    @api.depends("depends_on_ids.stage_id.is_closing", "blocked_reason")
    def _compute_is_blocked(self):
        for entry in self:
            reasons = entry._blocking_reasons()
            entry.is_blocked = bool(reasons)

    @api.depends("depends_on_ids.stage_id.is_closing",
                 "depends_on_ids.name", "blocked_reason")
    def _compute_blocking_summary(self):
        for entry in self:
            reasons = entry._blocking_reasons()
            entry.blocking_summary = "\n".join(reasons) if reasons else False

    @api.depends("claim_ids.verdict", "source_ids.is_dead")
    def _compute_claim_stats(self):
        for entry in self:
            entry.unverified_claim_count = len(
                entry.claim_ids.filtered(lambda c: c.verdict == "todo")
            )
            entry.dead_source_count = len(
                entry.source_ids.filtered(lambda s: s.is_dead)
            )

    @api.depends("planned_date", "published_date")
    def _compute_timeline_date(self):
        for entry in self:
            if entry.planned_date:
                entry.timeline_date = entry.planned_date
            elif entry.published_date:
                entry.timeline_date = entry.published_date.date()
            else:
                entry.timeline_date = False

    @api.depends("subject_module_id", "source_version")
    def _compute_version_drift(self):
        for entry in self:
            current = entry.subject_module_id.latest_version or False
            entry.current_version = current
            entry.version_drift = bool(
                entry.source_version and current
                and entry.source_version != current
            )

    @api.depends("checklist_ids.done")
    def _compute_checklist_stats(self):
        for entry in self:
            entry.open_checklist_count = len(
                entry.checklist_ids.filtered(
                    lambda c: not c.done and c.is_blocking
                )
            )

    @api.depends(
        "version_ids.state", "version_ids.word_count", "version_ids.lang_id",
        "version_ids.is_source", "calendar_id",
    )
    def _compute_language_state(self):
        for entry in self:
            source = entry.version_ids.filtered("is_source")[:1]
            entry.word_count = source.word_count if source else 0

            required = (
                entry.calendar_id._required_langs()
                if entry.calendar_id else self.env["res.lang"].browse()
            )
            if not required:
                entry.langs_complete = True
                entry.langs_ready = True
                entry.language_summary = _("Aucune langue exigée.")
                continue

            lines, missing, not_ready = [], [], []
            for lang in required:
                version = entry.version_ids.filtered(
                    lambda v: v.lang_id == lang
                )[:1]
                if not version:
                    missing.append(lang.name)
                    not_ready.append(lang.name)
                    lines.append(_("%s : aucun créneau", lang.name))
                    continue
                lines.append("%s : %s (%s mots)" % (
                    lang.name,
                    dict(version._fields["state"].selection).get(version.state),
                    version.word_count,
                ))
                if version.state != "published":
                    missing.append(lang.name)
                if version.state not in READY_STATES:
                    not_ready.append(lang.name)

            entry.langs_complete = not missing
            entry.langs_ready = not not_ready
            entry.language_summary = "\n".join(lines)

    @api.depends(
        "langs_complete", "open_checklist_count", "qa_state", "is_blocked",
        "word_count", "word_floor", "version_drift", "dead_source_count",
        "unverified_claim_count", "calendar_id",
    )
    def _compute_preflight(self):
        for entry in self:
            problems = entry._preflight_problems()
            entry.preflight_ok = not problems
            lignes = (
                ["• " + p for p in problems] if problems
                else [_("Rien ne s'oppose à la publication.")]
            )
            # Une dérogation ne se lit pas dans un onglet : elle se lit là où
            # se lit le verdict, sinon on publie sous dérogation sans le voir.
            if entry.qa_waived and not entry.qa_waiver_stale:
                lignes.append(_(
                    "⚠ Sous dérogation, signée par %(qui)s le %(quand)s :"
                    " %(motif)s",
                    qui=entry.qa_waived_by.name or "?",
                    quand=entry.qa_waived_on or "?",
                    motif=entry.qa_waiver_reason or "",
                ))
            elif entry.qa_waiver_stale:
                lignes.append(_(
                    "⚠ Une dérogation existe mais le texte a bougé depuis :"
                    " elle ne couvre plus rien. À signer de nouveau, ou à"
                    " lever."
                ))
            entry.preflight_summary = "\n".join(lignes)

    @api.depends("qa_waived", "qa_waiver_findings", "qa_findings")
    def _compute_qa_waiver_stale(self):
        for entry in self:
            entry.qa_waiver_stale = bool(entry.qa_waived) and (
                (entry.qa_waiver_findings or "") != (entry.qa_findings or "")
            )

    def _preflight_problems(self, ignore_waiver=False):
        """Les raisons de refuser la publication. Liste vide = feu vert.

        Les motifs qu'une dérogation couvre sont retirés ici, pas plus haut :
        la garde continue de les CALCULER, elle cesse seulement de refuser
        dessus. Un motif signé reste donc lisible dans le chatter, dans les
        constats et dans le résumé de pré-vol.
        """
        self.ensure_one()
        problems = []
        for code, message in self._preflight_findings():
            if (
                not ignore_waiver
                and code in WAIVABLE
                and self._waiver_covers(message)
            ):
                continue
            problems.append(message)
        return problems

    def _preflight_findings(self):
        """Les motifs de refus, chacun avec son code. Liste vide = feu vert.

        Le code sert à savoir lequel se signe : les motifs de JUGEMENT (la QA
        a vu quelque chose, l'article est court) se signent, les motifs de
        FAIT (une dépendance n'est pas sortie, une source est morte, une
        langue n'a pas été relue) ne se signent pas — ils se règlent.
        """
        self.ensure_one()
        problems = []

        if self.is_blocked:
            problems.append(
                ("blocked", _("Entrée bloquée : %s", self.blocking_summary))
            )

        if self.open_checklist_count:
            problems.append(("checklist", _(
                "%s reste(s) bloquant(s) dans la liste de contrôle.",
                self.open_checklist_count,
            )))

        if self.qa_state == "todo":
            problems.append(
                ("qa_todo", _("La QA éditoriale n'a pas été passée."))
            )
        elif self.qa_state == "findings":
            problems.append(("qa_findings", _(
                "La QA éditoriale a laissé des constats ouverts."
            )))

        floor = self.word_floor
        if floor and self.word_count and self.word_count < floor:
            problems.append(("word_floor", _(
                "Plancher de mots non atteint : %s contre %s.",
                self.word_count, floor,
            )))

        if self.version_drift:
            problems.append(("version_drift", _(
                "Le module documenté a bougé depuis le fact-check : %s → %s.",
                self.source_version, self.current_version,
            )))

        if self.dead_source_count:
            problems.append(("dead_source", _(
                "%s source(s) ne répondent plus.", self.dead_source_count,
            )))

        if self.unverified_claim_count:
            problems.append(("claims", _(
                "%s affirmation(s) sans verdict.", self.unverified_claim_count,
            )))

        # La politique multilingue est la dernière, parce que c'est celle qui
        # se règle et qu'il faut la lire en connaissant le reste.
        #
        # ⚠️ On contrôle « relue », pas « publiée ». La garde exigeait l'état
        # publié avant de publier : rien ne l'atteignait jamais, puisque seule
        # une synchronisation depuis un billet DÉJÀ publié le posait. Sur un
        # vrai brouillon, la condition était insatisfiable et le bouton
        # refusait à perpétuité.
        if self.calendar_id and self.calendar_id._requires_all_langs():
            if not self.langs_ready:
                problems.append(("langs", _(
                    "Toutes les langues exigées ne sont pas relues :\n%s",
                    self.language_summary,
                )))
        return problems

    # --- recherche sur les dérivés ----------------------------------------
    # Ces trois booléens ne sont PAS stockés, et c'est délibéré : un « pré-vol
    # vert » ou une « dérive de version » figés en base mentiraient dès que le
    # module documenté bouge ou qu'une traduction sort. On les rend cherchables
    # par une méthode, qui recalcule au moment de la requête.
    #
    # Limite assumée : la recherche balaie les entrées en Python. C'est tenable
    # sur un corpus de quelques milliers d'entrées, pas au-delà.
    @api.model
    def _search_derived_bool(self, operator, value, getter):
        if operator not in ("=", "!="):
            raise UserError(_(
                "Ce critère ne se compare qu'avec « = » ou « != »."
            ))
        wanted = bool(value) if operator == "=" else not bool(value)
        matching = [
            entry.id for entry in self.search([]) if getter(entry) == wanted
        ]
        return [("id", "in", matching)]

    def _search_langs_complete(self, operator, value):
        return self._search_derived_bool(
            operator, value, lambda e: e.langs_complete,
        )

    def _search_langs_ready(self, operator, value):
        return self._search_derived_bool(
            operator, value, lambda e: e.langs_ready,
        )

    def _search_version_drift(self, operator, value):
        return self._search_derived_bool(
            operator, value, lambda e: e.version_drift,
        )

    def _search_preflight_ok(self, operator, value):
        return self._search_derived_bool(
            operator, value, lambda e: not e._preflight_problems(),
        )

    # --- actions ----------------------------------------------------------
    def action_run_qa(self):
        """Passer les contrôles déterministes. Aucune IA n'intervient ici."""
        for entry in self:
            findings = self.env["bf.editorial.qa"].run(entry)
            entry.write({
                "qa_findings": "\n".join(findings) if findings else False,
                "qa_state": "findings" if findings else "clean",
                "qa_last_run": fields.Datetime.now(),
                "qa_run_by": self.env.user.id,
            })
            entry.message_post(body=_(
                "QA éditoriale passée : %s constat(s).", len(findings),
            ))
        return True

    # --- dérogation -------------------------------------------------------
    def _waiver_covers(self, message):
        """La dérogation couvre-t-elle CE motif, tel qu'il se lit aujourd'hui ?

        Deux conditions, et les deux comptent :

        * le motif doit figurer mot pour mot parmi ceux qui ont été signés.
          Les motifs portent leurs chiffres (« 1667 contre 1900 ») : un
          article qui raccourcit encore change son motif, donc sort de la
          signature tout seul, sans qu'on ait à comparer des nombres ;
        * les constats de QA ne doivent pas avoir bougé depuis. Le motif de QA,
          lui, est générique (« la QA a laissé des constats ouverts ») : sans
          cette seconde condition, une signature d'hier couvrirait des défauts
          apparus depuis.
        """
        self.ensure_one()
        if not self.qa_waived or self.qa_waiver_stale:
            return False
        return message in (self.qa_waiver_problems or "").split("\n")

    def _waivable_problems(self):
        """Les motifs de refus actuels qu'un humain a le droit de signer."""
        self.ensure_one()
        return [
            message for code, message in self._preflight_findings()
            if code in WAIVABLE
        ]

    def action_open_waiver(self):
        """Ouvrir la fenêtre de signature, qui montre ce qu'on signe."""
        self.ensure_one()
        if not self.env.user.has_group("bf_editorial.group_editorial_manager"):
            raise AccessError(_(
                "Signer une dérogation demande le groupe « Direction"
                " éditoriale ». C'est le même groupe que publier, et c'est"
                " voulu : une dérogation est une publication d'avance."
            ))
        problems = self._waivable_problems()
        if not problems:
            raise UserError(_(
                "Il n'y a rien à signer : aucun motif de jugement ne retient"
                " cette entrée. Ce qui reste au pré-vol se règle, il ne se"
                " signe pas."
            ))
        wizard = self.env["bf.editorial.waiver"].create({
            "entry_id": self.id,
            "problems": "\n".join("• " + p for p in problems),
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.editorial.waiver",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Signer une dérogation"),
        }

    def _sign_waiver(self, reason):
        """Poser la signature. Appelée par la fenêtre, jamais par un bouton."""
        self.ensure_one()
        problems = self._waivable_problems()
        if not problems:
            raise UserError(_("Il n'y a plus rien à signer."))
        self.write({
            "qa_waived": True,
            "qa_waiver_reason": reason,
            "qa_waiver_problems": "\n".join(problems),
            "qa_waiver_findings": self.qa_findings or False,
            "qa_waived_by": self.env.user.id,
            "qa_waived_on": fields.Datetime.now(),
        })
        self.message_post(body=_(
            "Dérogation signée par %(qui)s.\n\nMotifs couverts :\n%(motifs)s"
            "\n\nRaison : %(raison)s",
            qui=self.env.user.name,
            motifs="\n".join("• " + p for p in problems),
            raison=reason,
        ))
        return True

    def action_revoke_waiver(self):
        """Lever la dérogation. La garde reprend là où elle s'était arrêtée."""
        for entry in self:
            if not self.env.user.has_group(
                "bf_editorial.group_editorial_manager"
            ):
                raise AccessError(_(
                    "Lever une dérogation demande le groupe « Direction"
                    " éditoriale »."
                ))
            if not entry.qa_waived:
                continue
            entry.write({
                "qa_waived": False,
                "qa_waiver_reason": False,
                "qa_waiver_problems": False,
                "qa_waiver_findings": False,
                "qa_waived_by": False,
                "qa_waived_on": False,
            })
            entry.message_post(body=_(
                "Dérogation levée par %s. La garde de pré-vol reprend.",
                self.env.user.name,
            ))
        return True

    # --- réparations mécaniques -------------------------------------------
    def action_fix_mechanical(self):
        """Réparer les défauts qui n'appellent aucun arbitrage, puis repasser
        la QA pour que l'état dise la vérité tout de suite.

        ⚠️ C'est le SEUL endroit du module qui écrit dans le contenu d'un
        billet, et il n'écrit que deux choses (voir ``reparation.py``).
        L'écriture est du SQL créneau par créneau : un ``write`` ORM dans un
        contexte de langue étrangère écraserait le créneau source, et un
        ``write`` sur le champ entier effacerait ce qu'un humain aurait
        corrigé dans une autre langue entre-temps.
        """
        if not self.env.user.has_group("bf_editorial.group_editorial_user"):
            raise AccessError(_(
                "Corriger un billet demande au moins le groupe « Rédaction »."
            ))
        for entry in self:
            if not entry.post_id:
                raise UserError(_(
                    "« %s » n'est rattachée à aucun billet : il n'y a nulle"
                    " part où corriger.", entry.name,
                ))
            entry._fix_mechanical_one()
        return True

    def _fix_mechanical_one(self):
        self.ensure_one()
        slots = self._read_content_slots()
        rapports = {}
        for lang, html in slots.items():
            corrige, rapport = reparation.corriger(html)
            if corrige == html:
                continue
            self._write_content_slot(lang, corrige)
            rapports[lang] = rapport

        if not rapports:
            self.message_post(body=_(
                "Réparation mécanique : rien à corriger."
            ))
            return False

        self.post_id.invalidate_recordset(["content"])
        lignes = []
        for lang, rapport in sorted(rapports.items()):
            lignes.append("• %s : %s" % (
                lang, reparation.rapport_lisible(rapport, _),
            ))
        # L'écriture est en SQL : le crochet ORM qui remet la QA à « à passer »
        # ne la voit pas. On repasse donc la QA nous-mêmes, ce qui a le mérite
        # de montrer tout de suite ce que la réparation a réglé.
        self.action_run_qa()
        self.message_post(body=_(
            "Réparation mécanique appliquée par %(qui)s :\n%(detail)s"
            "\n\nLa QA a été repassée dans la foulée.",
            qui=self.env.user.name, detail="\n".join(lignes),
        ))
        return True

    def _read_content_slots(self):
        """Les créneaux de langue du billet, tels qu'ils sont en base.

        ⚠️ Le créneau source fantôme (``en_US``) est réparé lui aussi. Le site
        ne le sert jamais, mais l'éditeur s'en sert de base au prochain
        enregistrement : le laisser cassé ferait revenir le défaut.
        """
        self.ensure_one()
        self.env.cr.execute(
            "SELECT content FROM blog_post WHERE id = %s", (self.post_id.id,)
        )
        row = self.env.cr.fetchone()
        return dict(row[0] or {}) if row else {}

    def _write_content_slot(self, lang, html):
        """Poser un seul créneau, sans toucher aux autres clés du jsonb."""
        self.ensure_one()
        self.env.cr.execute(
            "UPDATE blog_post SET content = jsonb_set("
            "  COALESCE(content, '{}'::jsonb), %s, to_jsonb(%s::text), true"
            ") WHERE id = %s",
            ("{%s}" % lang, html, self.post_id.id),
        )

    def action_publish(self):
        """Publier, si et seulement si la garde de pré-vol est verte.

        Réservé à la direction éditoriale : le groupe Rédaction annonce
        « sans pouvoir publier », et une promesse de groupe qui n'est pas
        appliquée dans le code n'est pas une permission, c'est un décor.
        """
        if not self.env.user.has_group("bf_editorial.group_editorial_manager"):
            raise AccessError(_(
                "Publier demande le groupe « Direction éditoriale »."
            ))
        for entry in self:
            problems = entry._preflight_problems()
            if problems:
                raise UserError(_(
                    "Publication refusée pour « %(name)s » :\n\n%(list)s",
                    name=entry.name,
                    list="\n".join("• " + p for p in problems),
                ))
            entry._do_publish()
        return True

    def _do_publish(self):
        """L'écriture proprement dite. Isolée pour que le cron la partage."""
        self.ensure_one()
        if self.post_id and not self.post_id.is_published:
            self.post_id.sudo().write({"is_published": True})
        closing = self.env["bf.editorial.stage"].search(
            [("is_closing", "=", True)], order="sequence", limit=1,
        )
        values = {"published_date": fields.Datetime.now()}
        if closing:
            values["stage_id"] = closing.id
        self.write(values)
        self._release_versions()
        if self.qa_waived and not self.qa_waiver_stale:
            # Publier sous dérogation et publier au vert ne sont pas le même
            # geste. Le chatter doit pouvoir les distinguer des mois plus tard.
            self.message_post(body=_(
                "Publiée SOUS DÉROGATION, signée par %(qui)s le %(quand)s."
                "\n\nMotifs couverts :\n%(motifs)s\n\nRaison : %(raison)s",
                qui=self.qa_waived_by.name or "?",
                quand=self.qa_waived_on or "?",
                motifs=self.qa_waiver_problems or "",
                raison=self.qa_waiver_reason or "",
            ))
        else:
            self.message_post(body=_("Publiée, garde de pré-vol verte."))

    def _release_versions(self):
        """Faire sortir les créneaux de langue avec l'article.

        Un créneau relu qui reste « à relire » après la publication ferait
        mentir l'état des langues, et le slug figé n'était relevé par personne :
        ``action_freeze_slug`` était écrit, documenté, et jamais appelé.
        """
        self.ensure_one()
        required = (
            self.calendar_id._required_langs() if self.calendar_id
            else self.env["res.lang"].browse()
        )
        versions = self.version_ids
        if required:
            versions = versions.filtered(lambda v: v.lang_id in required)
        pending = versions.filtered(lambda v: v.state != "published")
        if pending:
            pending.write({"state": "published"})
        self.version_ids.action_freeze_slug()

    @api.model
    def _cron_publish_scheduled(self):
        """Publier les entrées dont la date approuvée est atteinte.

        La garde de pré-vol s'applique ici comme au bouton : une entrée dont
        l'anglais n'est pas prêt ne part pas, même si sa date est arrivée.
        Elle est signalée à son responsable plutôt que publiée à moitié.
        """
        now = fields.Datetime.now()
        due = self.search([
            ("scheduled_publish_date", "<=", now),
            ("published_date", "=", False),
            ("active", "=", True),
        ])
        for entry in due:
            problems = entry._preflight_problems()
            if problems:
                entry.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Publication différée refusée"),
                    note=_(
                        "La date approuvée est passée, mais la garde de pré-vol"
                        " a refusé :<br/>%s",
                        "<br/>".join("• " + p for p in problems),
                    ),
                    user_id=entry.user_id.id or self.env.uid,
                )
                entry.message_post(body=_(
                    "Publication différée refusée, %s point(s) bloquant(s).",
                    len(problems),
                ))
                continue
            entry._do_publish()
        return True

    def action_sync_from_post(self):
        """Recréer les créneaux de langue depuis le billet lié."""
        for entry in self.filtered("post_id"):
            entry.version_ids._sync_from_post(entry)
        return True
