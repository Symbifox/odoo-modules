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
    _description = "Editorial entry"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "planned_date desc, id desc"

    # --- identité ---------------------------------------------------------
    name = fields.Char(
        string="Working title", required=True, tracking=True,
        help="It survives changes to the published title, which makes it "
             "possible to follow an entry whose article was renamed.",
    )
    active = fields.Boolean(string="Active", default=True)
    calendar_id = fields.Many2one(
        "bf.editorial.calendar", string="Calendar",
        ondelete="restrict", index=True, tracking=True,
    )
    company_id = fields.Many2one(
        "res.company", string="Company", related="calendar_id.company_id",
        store=True, index=True,
    )
    post_id = fields.Many2one(
        "blog.post", string="Post", ondelete="set null", index=True,
        tracking=True,
        help="Empty for a publication that does not go through the blog.",
    )
    kind = fields.Selection(
        [
            ("blog", "Blog article"),
            ("social", "Social post"),
            ("newsletter", "Newsletter"),
            ("video", "Video"),
        ],
        string="Kind", default="blog", required=True, tracking=True,
    )
    pillar_id = fields.Many2one(
        "blog.tag.category", string="Pillar",
        domain=[("is_pillar", "=", True)], index=True, tracking=True,
    )
    tag_ids = fields.Many2many("blog.tag", string="Topics")
    color = fields.Integer(string="Color", related="pillar_id.color")

    # --- cycle ------------------------------------------------------------
    stage_id = fields.Many2one(
        "bf.editorial.stage", string="Stage", index=True, tracking=True,
        group_expand="_group_expand_stage", default=lambda s: s._default_stage(),
        ondelete="restrict",
    )
    planned_date = fields.Date(string="Planned date", tracking=True)
    scheduled_publish_date = fields.Datetime(
        string="Scheduled publication", tracking=True,
        help="Once the date is approved, publication goes out by itself, "
             "but only if the preflight guard is green at that moment.",
    )
    published_date = fields.Datetime(
        string="Published on", tracking=True, readonly=True, copy=False,
    )
    timeline_date = fields.Date(
        string="Calendar date", compute="_compute_timeline_date", store=True,
        help="Planned date if there is one, otherwise publication date. "
             "This is the one the calendar view shows: a view based on "
             "the planned date alone hides everything that is already out.",
    )
    user_id = fields.Many2one(
        "res.users", string="Assignee", default=lambda self: self.env.user,
        tracking=True,
    )
    reviewer_id = fields.Many2one("res.users", string="Reviewer", tracking=True)
    task_id = fields.Many2one("project.task", string="Linked task")
    campaign_id = fields.Many2one("utm.campaign", string="Campaign")

    # --- angle ------------------------------------------------------------
    hook = fields.Text(
        string="Hook",
        help="The first sentence, or the idea that should hook the reader.",
    )
    thesis = fields.Text(
        string="Thesis", help="What the article argues, in one sentence.",
    )
    reader_problem = fields.Text(string="Reader problem")
    promise = fields.Text(string="Promise")
    cta = fields.Char(string="Call to action")

    # --- référencement ----------------------------------------------------
    target_keyword = fields.Char(string="Target keyword")
    search_intent = fields.Selection(
        [
            ("informational", "Get informed"),
            ("comparison", "Compare"),
            ("transactional", "Choose a vendor"),
            ("navigational", "Find a specific page"),
        ],
        string="Search intent",
    )
    pattern = fields.Selection(
        [
            ("alternative", "Alternative to"),
            ("howto", "How-to"),
            ("comparison", "Comparison"),
            ("posture", "Opinion piece"),
            ("product", "Product presentation"),
            ("case", "Case study"),
        ],
        string="Pattern",
        help="The \"alternative to\" pattern has historically dominated "
             "the ranking.",
    )
    competitor = fields.Char(string="Target competitor")

    # --- dépendances ------------------------------------------------------
    depends_on_ids = fields.Many2many(
        "bf.editorial.entry", "bf_editorial_entry_dep_rel", "entry_id",
        "depends_id", string="Depends on",
        help="Entries that must be published before this one: a published "
             "post must never point to a draft.",
    )
    blocks_ids = fields.Many2many(
        "bf.editorial.entry", "bf_editorial_entry_dep_rel", "depends_id",
        "entry_id", string="Blocks", readonly=True,
    )
    blocked_reason = fields.Char(string="Blocking reason", tracking=True)
    is_blocked = fields.Boolean(
        string="Entry blocked", compute="_compute_is_blocked", store=True,
    )
    # Calcul SÉPARÉ, et c'est délibéré : Odoo avertit à chaque chargement du
    # registre quand une même méthode calcule des champs de « store »
    # différents. Les deux partagent leurs dépendances, pas leur stockage.
    blocking_summary = fields.Text(
        string="What is blocking", compute="_compute_blocking_summary",
    )

    # --- véracité ---------------------------------------------------------
    fact_check_date = fields.Date(string="Fact-checked on", tracking=True)
    fact_checked_by = fields.Many2one("res.users", string="Fact-checked by")
    source_ids = fields.One2many(
        "bf.editorial.source", "entry_id", string="Sources",
    )
    claim_ids = fields.One2many(
        "bf.editorial.claim", "entry_id", string="Claims",
    )
    unverified_claim_count = fields.Integer(
        string="Unverified claims", compute="_compute_claim_stats",
    )
    dead_source_count = fields.Integer(
        string="Dead sources", compute="_compute_claim_stats",
    )

    # --- fraîcheur produit ------------------------------------------------
    subject_module_id = fields.Many2one(
        "ir.module.module", string="Documented module",
        help="When filled in, the module flags by itself that the version "
             "has changed since the fact-check.",
    )
    source_version = fields.Char(
        string="Version at fact-check",
        help="The module version documented at the time of the check.",
    )
    current_version = fields.Char(
        string="Current version", compute="_compute_version_drift",
    )
    version_drift = fields.Boolean(
        string="Version drift", compute="_compute_version_drift",
        search="_search_version_drift",
    )

    # --- créneaux de langue et qualité ------------------------------------
    version_ids = fields.One2many(
        "bf.editorial.version", "entry_id", string="Language slots",
    )
    checklist_ids = fields.One2many(
        "bf.editorial.checklist", "entry_id", string="Checklist",
    )
    open_checklist_count = fields.Integer(
        string="Open to-dos", compute="_compute_checklist_stats",
    )
    word_count = fields.Integer(
        string="Words (source language)", compute="_compute_language_state",
    )
    word_floor = fields.Integer(
        string="Floor", related="calendar_id.word_floor", readonly=True,
    )
    # Ces deux cases se calculent depuis les créneaux, et se COCHENT aussi :
    # cocher écrit la décision sur chaque créneau exigé (relue, ou publiée),
    # décocher la retire. Une case qui se calcule sans se cocher est une
    # case que personne ne peut cocher, et c'est ce qu'on a vécu.
    langs_complete = fields.Boolean(
        string="All languages delivered", compute="_compute_language_state",
        inverse="_inverse_langs_complete", search="_search_langs_complete",
        help="True when every required language carries a published slot. "
             "Checking sets all required slots to \"Published\" and "
             "freezes their slug, without publishing the post: it is the "
             "catch-up action for an article that came out outside the "
             "workshop. Reserved to Editorial management, like "
             "publishing. Unchecking returns published slots to "
             "\"Reviewed\".",
    )
    langs_ready = fields.Boolean(
        string="All languages reviewed", compute="_compute_language_state",
        inverse="_inverse_langs_ready", search="_search_langs_ready",
        help="True when every required language carries a reviewed or "
             "already published slot. This is the status the preflight "
             "guard checks: requiring \"published\" BEFORE publishing can "
             "never be satisfied. Checking sets every required slot that "
             "is not yet reviewed to \"Reviewed\", and creates the "
             "missing ones. Unchecking returns reviewed slots to "
             "\"Translated\"; a published slot does not move.",
    )
    language_summary = fields.Text(
        string="Language status", compute="_compute_language_state",
    )

    qa_state = fields.Selection(
        [
            ("todo", "To run"),
            ("clean", "Clean"),
            ("findings", "Findings"),
        ],
        string="QA status", default="todo", copy=False, tracking=True,
    )
    qa_findings = fields.Text(string="QA findings", readonly=True, copy=False)
    qa_last_run = fields.Datetime(string="QA run on", readonly=True, copy=False)
    qa_run_by = fields.Many2one(
        "res.users", string="QA run by", readonly=True, copy=False,
    )

    # --- dérogation -------------------------------------------------------
    # La QA dit ce qu'elle voit ; la dérogation dit ce qu'un humain accepte de
    # publier quand même. Les deux restent lisibles côte à côte : rien n'efface
    # un constat, c'est le REFUS qui cède, en nommant qui le fait céder.
    qa_waived = fields.Boolean(
        string="Under waiver", readonly=True, copy=False, tracking=True,
    )
    qa_waiver_reason = fields.Text(
        string="Waiver reason", readonly=True, copy=False,
    )
    qa_waiver_problems = fields.Text(
        string="Covered reasons", readonly=True, copy=False,
        help="The refusal reasons, word for word, as they read at "
             "signing. The waiver covers ONLY those: a reason whose text "
             "changes is no longer the signed reason.",
    )
    qa_waiver_findings = fields.Text(
        string="Covered findings", readonly=True, copy=False,
        help="The QA findings at the time of signing. They serve as a "
             "fingerprint: as soon as the QA reports others, the waiver "
             "no longer covers them.",
    )
    qa_waived_by = fields.Many2one(
        "res.users", string="Waiver signed by", readonly=True, copy=False,
    )
    qa_waived_on = fields.Datetime(
        string="Waiver signed on", readonly=True, copy=False,
    )
    qa_waiver_stale = fields.Boolean(
        string="Stale waiver", compute="_compute_qa_waiver_stale",
        help="True when a waiver exists but the text has changed since. "
             "It then covers nothing, and the guard resumes.",
    )

    preflight_ok = fields.Boolean(
        string="Preflight green", compute="_compute_preflight",
        search="_search_preflight_ok",
    )
    preflight_summary = fields.Text(
        string="Preflight", compute="_compute_preflight",
    )

    # --- mesures brutes ---------------------------------------------------
    raw_visits = fields.Integer(
        string="Visits (raw)", related="post_id.visits", readonly=True,
        help="Odoo's native counter. It also counts bots: read it as an "
             "order of magnitude, not as a readership.",
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
            reasons.append(_("\"%s\" is not published", dep.name))
        return reasons

    @api.depends("depends_on_ids.stage_id.is_closing", "blocked_reason")
    def _compute_is_blocked(self):
        for entry in self:
            reasons = entry._blocking_reasons()
            entry.is_blocked = bool(reasons)

    @api.depends("depends_on_ids.stage_id.is_closing",
                 "depends_on_ids.name", "blocked_reason")
    @api.depends_context("lang")
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
    @api.depends_context("lang")
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
                entry.language_summary = _("No language required.")
                continue

            lines, missing, not_ready = [], [], []
            for lang in required:
                version = entry.version_ids.filtered(
                    lambda v: v.lang_id == lang
                )[:1]
                if not version:
                    missing.append(lang.name)
                    not_ready.append(lang.name)
                    lines.append(_("%s: no slot", lang.name))
                    continue
                lines.append(_(
                    "%(lang)s: %(etat)s (%(mots)s words)",
                    lang=lang.name,
                    etat=dict(version._fields["state"]._description_selection(self.env)).get(version.state),
                    mots=version.word_count,
                ))
                if version.state != "published":
                    missing.append(lang.name)
                if version.state not in READY_STATES:
                    not_ready.append(lang.name)

            entry.langs_complete = not missing
            entry.langs_ready = not not_ready
            entry.language_summary = "\n".join(lines)

    # --- cocher les cases de langue --------------------------------------
    # Les deux booléens ci-dessus sont des dérivés, mais un dérivé qu'on ne
    # peut pas cocher oblige à ouvrir chaque créneau l'un après l'autre.
    # Cocher, c'est prendre la décision pour tous les créneaux exigés d'un
    # coup ; la case se recalcule ensuite depuis eux, et rien n'est stocké
    # sur l'entrée.
    def _required_langs(self):
        self.ensure_one()
        if not self.calendar_id:
            return self.env["res.lang"].browse()
        return self.calendar_id._required_langs()

    def _ensure_required_slots(self, required):
        """Créer les créneaux exigés qui manquent, sans toucher aux autres."""
        self.ensure_one()
        if self.post_id:
            # Le billet donne le compte de mots et la langue source.
            self.env["bf.editorial.version"]._sync_from_post(self)
        present = self.version_ids.mapped("lang_id")
        for lang in required - present:
            self.env["bf.editorial.version"].create({
                "entry_id": self.id, "lang_id": lang.id, "state": "todo",
            })

    def _move_required_slots(self, state, leaving):
        """Passer à ``state`` les créneaux exigés dont l'état est dans ``leaving``.

        Rend les créneaux déplacés, pour le journal.
        """
        self.ensure_one()
        required = self._required_langs()
        slots = self.version_ids.filtered(
            lambda v: v.lang_id in required and v.state in leaving
        )
        if slots:
            slots.write({"state": state})
        return slots

    def _log_slot_move(self, slots, state):
        """Une trace au chatter, sans notifier personne.

        ``_message_log`` plutôt que ``message_post`` : un journal interne ne
        réclame pas d'adresse d'expéditeur, et un compte sans courriel doit
        pouvoir cocher la case quand même.
        """
        if not slots:
            return
        # La phrase et l'état dans la même langue : sans langue au contexte (XML-RPC, travail
        # planifié), _() prend celle de l'usager alors que la sélection rendrait l'anglais.
        env = self.with_context(lang=self.env.lang or self.env.user.lang or "en_US").env
        label = dict(slots._fields["state"]._description_selection(env)).get(state, state)
        self._message_log(body=env._(
            "Slots moved to \"%(etat)s\" in one action: %(langues)s.",
            etat=label,
            langues=", ".join(slots.mapped("lang_id.name")),
        ))

    def _canon_lang(self):
        """La langue des textes que la dérogation fige et compare.

        Un motif signé dans une langue doit couvrir encore quand une autre personne, ou le travail
        planifié, relit l'entrée dans la sienne : le motif et les constats de QA se calculent donc
        toujours dans la langue de la société (l'anglais à défaut), jamais dans celle de l'acteur.
        Ce qui est figé (constats de QA, problèmes de la dérogation) se lit donc dans cette langue ;
        le pré-vol, lui, s'affiche dans la langue de la personne.
        """
        self.ensure_one()
        return (self.company_id.partner_id.lang
                or self.env.company.partner_id.lang
                or "en_US")

    def _inverse_langs_ready(self):
        for entry in self:
            if entry.langs_ready:
                required = entry._required_langs()
                if not required:
                    continue
                entry._ensure_required_slots(required)
                moved = entry._move_required_slots(
                    "reviewed", ("todo", "translated"),
                )
                entry._log_slot_move(moved, "reviewed")
            else:
                # Un créneau publié ne se « dé-relit » pas : l'article est
                # sorti. Si tous le sont, la case se recoche d'elle-même.
                moved = entry._move_required_slots("translated", ("reviewed",))
                entry._log_slot_move(moved, "translated")

    def _inverse_langs_complete(self):
        # Déclarer une langue livrée, c'est un geste de publication : le
        # même groupe que le bouton, et une phrase propre à cette garde.
        if not self.env.user.has_group("bf_editorial.group_editorial_manager"):
            raise AccessError(_(
                "Checking \"All languages delivered\" requires the "
                "\"Editorial management\" group."
            ))
        for entry in self:
            if entry.langs_complete:
                required = entry._required_langs()
                if not required:
                    continue
                entry._ensure_required_slots(required)
                moved = entry.version_ids.filtered(
                    lambda v: v.lang_id in required and v.state != "published"
                )
                entry._release_versions()
                entry._log_slot_move(moved, "published")
            else:
                moved = entry._move_required_slots("reviewed", ("published",))
                entry._log_slot_move(moved, "reviewed")

    @api.depends(
        "langs_complete", "open_checklist_count", "qa_state", "is_blocked",
        "word_count", "word_floor", "version_drift", "dead_source_count",
        "unverified_claim_count", "calendar_id",
    )
    @api.depends_context("lang")
    def _compute_preflight(self):
        for entry in self:
            problems = entry._preflight_problems()
            entry.preflight_ok = not problems
            lignes = (
                ["• " + p for p in problems] if problems
                else [_("Nothing stands in the way of publishing.")]
            )
            # Une dérogation ne se lit pas dans un onglet : elle se lit là où
            # se lit le verdict, sinon on publie sous dérogation sans le voir.
            if entry.qa_waived and not entry.qa_waiver_stale:
                lignes.append(_(
                    "⚠ Under waiver, signed by %(qui)s on %(quand)s: %(motif)s",
                    qui=entry.qa_waived_by.name or "?",
                    quand=entry.qa_waived_on or "?",
                    motif=entry.qa_waiver_reason or "",
                ))
            elif entry.qa_waiver_stale:
                lignes.append(_(
                    "⚠ A waiver exists but the text has changed since: it "
                    "no longer covers anything. Sign it again, or revoke "
                    "it."
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
        # Les motifs signés sont écrits dans la langue canonique : on compare ceux-là, et on AFFICHE
        # ceux de la langue de la personne (même code, même ordre).
        canon = {}
        if not ignore_waiver and self.qa_waived:
            canon = dict(self.with_context(lang=self._canon_lang())._preflight_findings())
        for code, message in self._preflight_findings():
            if (
                not ignore_waiver
                and code in WAIVABLE
                and self._waiver_covers(canon.get(code, message))
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
                ("blocked", _("Entry blocked: %s", self.blocking_summary))
            )

        if self.open_checklist_count:
            problems.append(("checklist", _(
                "%s blocking to-do(s) left in the checklist.",
                self.open_checklist_count,
            )))

        if self.qa_state == "todo":
            problems.append(
                ("qa_todo", _("The editorial QA has not been run."))
            )
        elif self.qa_state == "findings":
            problems.append(("qa_findings", _(
                "The editorial QA left open findings."
            )))

        floor = self.word_floor
        if floor and self.word_count and self.word_count < floor:
            problems.append(("word_floor", _(
                "Word floor not reached: %s against %s.",
                self.word_count, floor,
            )))

        if self.version_drift:
            problems.append(("version_drift", _(
                "The documented module has changed since the fact-check: "
                "%s → %s.",
                self.source_version, self.current_version,
            )))

        if self.dead_source_count:
            problems.append(("dead_source", _(
                "%s source(s) no longer respond.", self.dead_source_count,
            )))

        if self.unverified_claim_count:
            problems.append(("claims", _(
                "%s claim(s) without a verdict.", self.unverified_claim_count,
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
                    "Not all required languages are reviewed:\n%s",
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
                "This criterion can only be compared with \"=\" or \"!=\"."
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
            findings = self.env["bf.editorial.qa"].with_context(lang=entry._canon_lang()).run(entry)
            entry.write({
                "qa_findings": "\n".join(findings) if findings else False,
                "qa_state": "findings" if findings else "clean",
                "qa_last_run": fields.Datetime.now(),
                "qa_run_by": self.env.user.id,
            })
            entry.message_post(body=_(
                "Editorial QA run: %s finding(s).", len(findings),
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
                "Signing a waiver requires the \"Editorial management\" "
                "group. It is the same group as publishing, on purpose: a "
                "waiver is a publication in advance."
            ))
        problems = self._waivable_problems()
        if not problems:
            raise UserError(_(
                "There is nothing to sign: no judgment-based reason holds "
                "this entry back. What remains at preflight gets fixed, "
                "not signed."
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
            "name": _("Sign a waiver"),
        }

    def _sign_waiver(self, reason):
        """Poser la signature. Appelée par la fenêtre, jamais par un bouton."""
        self.ensure_one()
        problems = self._waivable_problems()
        if not problems:
            raise UserError(_("There is nothing left to sign."))
        # Figés dans la langue canonique (voir `_canon_lang`) ; `problems` reste celui de la personne.
        figes = self.with_context(lang=self._canon_lang())._waivable_problems()
        self.write({
            "qa_waived": True,
            "qa_waiver_reason": reason,
            "qa_waiver_problems": "\n".join(figes),
            "qa_waiver_findings": self.qa_findings or False,
            "qa_waived_by": self.env.user.id,
            "qa_waived_on": fields.Datetime.now(),
        })
        self.message_post(body=_(
            "Waiver signed by %(qui)s.\n\nReasons "
            "covered:\n%(motifs)s\n\nReason: %(raison)s",
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
                    "Revoking a waiver requires the \"Editorial "
                    "management\" group."
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
                "Waiver revoked by %s. The preflight guard resumes.",
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
                "Fixing a post requires at least the \"Writing\" group."
            ))
        for entry in self:
            if not entry.post_id:
                raise UserError(_(
                    "\"%s\" is not linked to any post: there is nowhere "
                    "to fix.", entry.name,
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
                "Mechanical repair: nothing to fix."
            ))
            return False

        self.post_id.invalidate_recordset(["content"])
        lignes = []
        phrases = {
            "titres_vides": _("%s empty heading(s) replaced by a spacer"),
            "portees": _("%s scope(s) set on table headers"),
        }
        for lang, rapport in sorted(rapports.items()):
            lignes.append(_("• %(lang)s: %(rapport)s", lang=lang, rapport=reparation.rapport_lisible(rapport, phrases)))
        # L'écriture est en SQL : le crochet ORM qui remet la QA à « à passer »
        # ne la voit pas. On repasse donc la QA nous-mêmes, ce qui a le mérite
        # de montrer tout de suite ce que la réparation a réglé.
        self.action_run_qa()
        self.message_post(body=_(
            "Mechanical repair applied by %(qui)s:\n%(detail)s\n\nThe QA "
            "was run again right after.",
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
                "Publishing requires the \"Editorial management\" group."
            ))
        for entry in self:
            problems = entry._preflight_problems()
            if problems:
                raise UserError(_(
                    "Publication refused for \"%(name)s\":\n\n%(list)s",
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
                "Published UNDER WAIVER, signed by %(qui)s on "
                "%(quand)s.\n\nReasons covered:\n%(motifs)s\n\nReason: "
                "%(raison)s",
                qui=self.qa_waived_by.name or "?",
                quand=self.qa_waived_on or "?",
                motifs=self.qa_waiver_problems or "",
                raison=self.qa_waiver_reason or "",
            ))
        else:
            self.message_post(body=_("Published, preflight guard green."))

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
            # Le travail planifié n'a pas de langue : la note part au responsable de l'entrée, dans la sienne
            # (entry.env._ : _() lirait la langue du `self` du travail planifié, pas celle de l'entrée).
            entry = entry.with_context(lang=entry.user_id.lang or entry._canon_lang())
            problems = entry._preflight_problems()
            if problems:
                entry.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=entry.env._("Scheduled publication refused"),
                    note=entry.env._(
                        "The approved date has passed, but the preflight "
                        "guard refused:<br/>%s",
                        "<br/>".join("• " + p for p in problems),
                    ),
                    user_id=entry.user_id.id or self.env.uid,
                )
                entry.message_post(body=entry.env._(
                    "Scheduled publication refused, %s blocking point(s).",
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
