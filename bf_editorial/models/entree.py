# -*- coding: utf-8 -*-
"""L'entrée éditoriale : l'unité de travail du module.

Une entrée peut porter un billet de blogue, ou n'être qu'un billet social
autonome. Ce qu'elle stocke, ce sont les décisions et le contexte de
production. Les mesures — mots, visites, dérive de version, complétude des
langues — se calculent.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


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
                entry.language_summary = _("Aucune langue exigée.")
                continue

            lines, missing = [], []
            for lang in required:
                version = entry.version_ids.filtered(
                    lambda v: v.lang_id == lang
                )[:1]
                if not version:
                    missing.append(lang.name)
                    lines.append(_("%s : aucun créneau", lang.name))
                    continue
                lines.append("%s : %s (%s mots)" % (
                    lang.name,
                    dict(version._fields["state"].selection).get(version.state),
                    version.word_count,
                ))
                if version.state != "published":
                    missing.append(lang.name)

            entry.langs_complete = not missing
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
            entry.preflight_summary = (
                "\n".join("• " + p for p in problems) if problems
                else _("Rien ne s'oppose à la publication.")
            )

    def _preflight_problems(self):
        """Les raisons de refuser la publication. Liste vide = feu vert."""
        self.ensure_one()
        problems = []

        if self.is_blocked:
            problems.append(_("Entrée bloquée : %s", self.blocking_summary))

        if self.open_checklist_count:
            problems.append(_(
                "%s reste(s) bloquant(s) dans la liste de contrôle.",
                self.open_checklist_count,
            ))

        if self.qa_state == "todo":
            problems.append(_("La QA éditoriale n'a pas été passée."))
        elif self.qa_state == "findings":
            problems.append(_("La QA éditoriale a laissé des constats ouverts."))

        floor = self.word_floor
        if floor and self.word_count and self.word_count < floor:
            problems.append(_(
                "Plancher de mots non atteint : %s contre %s.",
                self.word_count, floor,
            ))

        if self.version_drift:
            problems.append(_(
                "Le module documenté a bougé depuis le fact-check : %s → %s.",
                self.source_version, self.current_version,
            ))

        if self.dead_source_count:
            problems.append(_(
                "%s source(s) ne répondent plus.", self.dead_source_count,
            ))

        if self.unverified_claim_count:
            problems.append(_(
                "%s affirmation(s) sans verdict.", self.unverified_claim_count,
            ))

        # La politique multilingue est la dernière, parce que c'est celle qui
        # se règle et qu'il faut la lire en connaissant le reste.
        if self.calendar_id and self.calendar_id._requires_all_langs():
            if not self.langs_complete:
                problems.append(_(
                    "Toutes les langues exigées ne sont pas publiées."
                ))
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
        self.message_post(body=_("Publiée, garde de pré-vol verte."))

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
