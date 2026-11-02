"""Rétention et destruction de l'inbox unifiée.

Les cas visés sont ceux qui rendent une destruction fausse plutôt que celle qui
plante : détruire ce qu'on n'a pas su dater, certifier ce qui revient au
passage suivant, et — le piège de ce module — **attester une destruction qui
s'est contentée d'archiver**.
"""
import logging
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install")
class BfEmailRetentionCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.BfEmail = cls.env["bf.email"]
        cls.Register = cls.env["privacy.destruction.register"]
        cls.Campaign = cls.env["privacy.destruction.campaign"]
        cls.Line = cls.env["privacy.destruction.campaign.line"]
        cls.owner = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Titulaire Rétention",
            "login": "retention.owner@test.invalid",
            "email": "retention.owner@test.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.rule_unfiled = cls.env.ref(
            "bf_email_privacy.retention_bf_email_unfiled"
        )
        cls.rule_filed = cls.env.ref("bf_email_privacy.retention_bf_email_filed")
        cls.folded = cls.env["project.project.stage"].search(
            [("fold", "=", True)], limit=1
        ) or cls.env["project.project.stage"].create({
            "name": "Terminé (test)", "fold": True, "sequence": 90,
        })
        cls.open_stage = cls.env["project.project.stage"].search(
            [("fold", "=", False)], limit=1
        ) or cls.env["project.project.stage"].create({
            "name": "En cours (test)", "fold": False, "sequence": 10,
        })

    # ------------------------------------------------------------------

    def _email(self, days_ago, **extra):
        vals = {
            "date": fields.Datetime.now() - timedelta(days=days_ago),
            "direction": "in",
            "source": "imap",
            "subject": "Objet %s" % days_ago,
            "email_from": "correspondant@test.invalid",
            "user_id": self.owner.id,
        }
        vals.update(extra)
        return self.BfEmail.with_context(
            mail_create_nosubscribe=True, tracking_disable=True
        ).create(vals)

    def _years(self, rule):
        return rule.total_retention_days

    def _propose(self, **kw):
        return self.BfEmail._cron_privacy_propose_destruction(**kw)

    def _execute_campaigns(self):
        """Approuve puis exécute les campagnes en révision."""
        campaigns = self.Campaign.search([("state", "=", "review")])
        for campaign in campaigns:
            campaign.sudo().action_approve()
            campaign.sudo().action_execute()
        return campaigns

    def _quiet(self, model):
        """Créer sans journaliser.

        ⚠️ Créer un projet ou une tâche pose un message daté de MAINTENANT, et
        ce message est un signe de vie : sans ces contextes, tout dossier de
        test naît « actif aujourd'hui » et aucun ne peut être tenu pour clos.
        """
        return self.env[model].with_context(
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            tracking_disable=True,
        )

    def _closed_project(self, days_since_closure, name="Projet clos"):
        """Projet rangé ET silencieux depuis `days_since_closure` jours.

        Le silence se fabrique par un message ancien : c'est le dernier signe
        de vie, donc la date de clôture. Sans lui, le dossier n'aurait aucune
        activité et ne serait pas datable.
        """
        project = self._quiet("project.project").create({
            "name": name,
            "stage_id": self.folded.id,
        })
        # Le dernier signe de vie est posé en message daté, pas en feuille de
        # temps : `account.analytic.line.project_id` n'existe que si
        # `hr_timesheet` est installé, ce qui n'est pas garanti sur un banc.
        self.env["mail.message"].sudo().create({
            "model": "project.project",
            "res_id": project.id,
            "body": "Dernier échange sur %s" % name,
            "message_type": "comment",
            "date": fields.Datetime.now() - timedelta(days=days_since_closure),
        })
        return project

    # ── Le cron PROPOSE, il ne détruit pas ───────────────────────────────

    def test_cron_proposes_without_destroying(self):
        """Le passage du cron ne doit rien supprimer, jamais."""
        old = self._email(self._years(self.rule_unfiled) + 10)
        before = self.Register.search_count([])

        proposed = self._propose()

        self.assertGreaterEqual(proposed, 1)
        self.assertTrue(
            old.exists(),
            "le cron ne détruit pas : il dépose en campagne à approuver",
        )
        self.assertEqual(
            self.Register.search_count([]), before,
            "rien ne s'inscrit au registre avant l'exécution de la campagne",
        )
        campaign = self.Campaign.search([
            ("retention_calendar_id", "=", self.rule_unfiled.id)
        ], limit=1)
        self.assertEqual(campaign.state, "review")
        self.assertIn(old.id, campaign.line_ids.mapped("res_id"))

    def test_second_pass_does_not_duplicate_a_pending_line(self):
        """Le cron tourne chaque nuit ; l'approbation prend des semaines."""
        old = self._email(self._years(self.rule_unfiled) + 10)
        self._propose()
        self._propose()
        lines = self.Line.search([
            ("res_model", "=", "bf.email"), ("res_id", "=", old.id),
        ])
        self.assertEqual(len(lines), 1)

    def test_one_campaign_per_rule_not_one_per_night(self):
        self._email(self._years(self.rule_unfiled) + 10)
        self._propose()
        self._email(self._years(self.rule_unfiled) + 20)
        self._propose()
        self.assertEqual(
            self.Campaign.search_count([
                ("retention_calendar_id", "=", self.rule_unfiled.id)
            ]), 1,
        )

    # ── L'exécution détruit VRAIMENT ─────────────────────────────────────

    def test_execution_really_unlinks_it_does_not_archive(self):
        """🔴 Le piège du module.

        `_execute_destruction` de `privacy_consent` fait `active = False` dès
        que le modèle a un champ `active`. `bf.email` en a un. Sans la
        surcharge, la ligne serait simplement masquée, corps et copie RFC 2822
        intacts, et le registre attesterait « Suppression ».
        """
        old = self._email(self._years(self.rule_unfiled) + 10)
        doomed_id = old.id
        self._propose()
        self._execute_campaigns()

        survivor = self.BfEmail.with_context(active_test=False).browse(doomed_id)
        self.assertFalse(
            survivor.exists(),
            "la ligne doit être SUPPRIMÉE, pas archivée — vérifié avec "
            "active_test=False, sinon une ligne archivée passerait pour absente",
        )

    def test_execution_registers_the_destruction(self):
        old = self._email(self._years(self.rule_unfiled) + 10)
        before = self.Register.search_count([])
        self._propose()
        self._execute_campaigns()

        self.assertFalse(old.exists())
        self.assertEqual(self.Register.search_count([]), before + 1)
        entry = self.Register.search([], order="id desc", limit=1)
        self.assertEqual(entry.retention_calendar_id, self.rule_unfiled)
        self.assertEqual(
            entry.destruction_method, "delete",
            "supprimer des lignes n'est pas un effacement sécurisé",
        )

    def test_register_entry_states_what_it_does_not_cover(self):
        """L'entrée nomme les sauvegardes, leur rotation et la copie IMAP.

        La campagne compose une description générique. Sans la surcharge du
        registre, l'attestation tairait ses angles morts.
        """
        self._email(self._years(self.rule_unfiled) + 10)
        self._propose()
        self._execute_campaigns()
        description = self.Register.search(
            [], order="id desc", limit=1
        ).document_description or ""
        self.assertIn("sauvegardes", description)
        self.assertIn("IMAP", description)
        self.assertIn("politique de sauvegarde", description)

    def test_register_promises_no_duration_it_cannot_keep(self):
        """🔴 Une entrée de registre est IMMUABLE : ce qu'on y écrit y reste.

        Deux formulations antérieures étaient fausses, dans les deux sens :
        « rétention illimitée de type legal hold » (mauvais motif), puis
        « disparu de toutes les copies environ 12 mois après » (mauvaise
        promesse — la politique 14/8/12 est défaite par le groupage de
        `restic forget`, des centaines d'instantanés restés au dépôt).

        Le texte doit donc énoncer un principe vérifiable, jamais une durée.
        """
        statement = self.BfEmail._privacy_destruction_scope_statement()
        for interdit in ("illimité", "legal hold", "12 mois", "environ 12"):
            self.assertNotIn(
                interdit, statement,
                "le registre ne doit ni promettre une durée ni invoquer un "
                "legal hold : les deux ont déjà été démentis par la mesure",
            )
        self.assertIn("vérifie", statement)
        self.assertIn("politique de sauvegarde", statement)

    def test_line_keeps_the_name_after_the_record_is_gone(self):
        """Le nom est figé à la proposition : après coup, rien ne le produit."""
        old = self._email(self._years(self.rule_unfiled) + 10)
        expected = old.display_name
        self._propose()
        self._execute_campaigns()
        line = self.Line.search([("res_model", "=", "bf.email")], limit=1)
        self.assertEqual(line.state, "done")
        self.assertEqual(line.res_name, expected)

    def test_anonymize_is_refused_for_the_inbox(self):
        """Anonymiser laisserait le corps : on refuse plutôt que de certifier."""
        old = self._email(self._years(self.rule_unfiled) + 10)
        self._propose()
        line = self.Line.search([
            ("res_model", "=", "bf.email"), ("res_id", "=", old.id),
        ], limit=1)
        line.sudo().write({"destruction_method": "anonymize"})
        with self.assertRaises(UserError):
            line.sudo()._execute_destruction()
        self.assertTrue(old.exists())

    def test_register_refuses_to_certify_a_survivor(self):
        """🔴 Seconde garde, posée ailleurs que la première.

        Simule le cas que personne ne peut tester avec un seul pont chargé :
        un module tiers surcharge `_execute_destruction` SANS relayer à
        `super()`, ce qui fait taire en silence la garde de ce module. La
        campagne se remet alors à archiver, et `action_execute` inscrit
        « Suppression » au registre immuable.

        Le registre doit refuser. La destruction est cassée dans ce cas — mais
        elle ne sera pas en plus certifiée.
        """
        row = self._email(self._years(self.rule_unfiled) + 10)
        with self.assertRaises(UserError):
            self.Register.sudo().create({
                "destruction_date": fields.Datetime.now(),
                "destroyed_by_id": self.env.user.id,
                "approved_by_id": self.env.user.id,
                "res_model": "bf.email",
                "res_id": row.id,
                "res_name": row.display_name,
                "document_description": "Destruction par campagne « essai ».",
                "destruction_method": "delete",
                "legal_basis": "Art. 23 LPRPSP",
            })
        self.assertTrue(row.exists())

    def test_register_refuses_even_when_merely_archived(self):
        """Archivé n'est pas supprimé — la garde lit avec active_test=False.

        C'est exactement ce que fait la méthode « delete » du socle sur un
        modèle qui a un champ `active` : elle archive. Sans `active_test=False`,
        la ligne archivée passerait pour absente et l'attestation partirait.
        """
        row = self._email(self._years(self.rule_unfiled) + 10)
        row.write({"active": False})
        with self.assertRaises(UserError):
            self.Register.sudo().create({
                "destruction_date": fields.Datetime.now(),
                "destroyed_by_id": self.env.user.id,
                "approved_by_id": self.env.user.id,
                "res_model": "bf.email",
                "res_id": row.id,
                "res_name": "Courriel archivé",
                "document_description": "Destruction par campagne « essai ».",
                "destruction_method": "delete",
                "legal_basis": "Art. 23 LPRPSP",
            })

    def test_register_still_accepts_an_anonymisation(self):
        """La garde ne doit pas bloquer ce qui n'affirme pas la disparition.

        « anonymize » et « archive » disent que la ligne survit, neutralisée :
        les refuser ferait de la garde un faux positif permanent.
        """
        row = self._email(3)
        entry = self.Register.sudo().create({
            "destruction_date": fields.Datetime.now(),
            "destroyed_by_id": self.env.user.id,
            "approved_by_id": self.env.user.id,
            "res_model": "bf.email",
            "res_id": row.id,
            "res_name": row.display_name,
            "document_description": "Anonymisation ciblée.",
            "destruction_method": "anonymize",
            "legal_basis": "Art. 23 LPRPSP",
        })
        self.assertTrue(entry.id)
        self.assertIn("sauvegardes", entry.document_description)

    def test_bridges_overriding_execute_destruction_relay_to_super(self):
        """Les gardes des ponts ne composent que si chacun relaie.

        Contrôle statique sur la chaîne de résolution réellement chargée : tout
        maillon qui définit `_execute_destruction` doit contenir un appel à
        `super()._execute_destruction`, sinon il fait taire les maillons
        suivants. Le socle `privacy_consent` est le bout de chaîne et n'a pas à
        relayer.

        ⚠️ Ce test ne vaut que ce que la base porte : sur un banc où ce module
        est seul installé, il ne voit qu'un maillon et passe à vide. Il mord là
        où plusieurs ponts cohabitent — la production. Le compte de maillons
        est journalisé pour qu'on sache lequel des deux cas on a sous les yeux.
        """
        import inspect
        Line = type(self.env["privacy.destruction.campaign.line"])
        muets = []
        for klass in Line.__mro__:
            if "_execute_destruction" not in vars(klass):
                continue
            source = inspect.getsource(vars(klass)["_execute_destruction"])
            module = getattr(klass, "_module", None) or klass.__module__
            if "super()._execute_destruction" in source:
                continue
            if "privacy_consent" in str(module):
                continue  # bout de chaîne : c'est lui qui détruit pour de vrai
            muets.append(str(module))
        maillons = [
            str(getattr(k, "_module", None) or k.__module__)
            for k in Line.__mro__ if "_execute_destruction" in vars(k)
        ]
        _logger.info(
            "bf_email_privacy: %s maillon(s) sur _execute_destruction — %s",
            len(maillons), maillons,
        )
        self.assertFalse(
            muets,
            "ces modules surchargent _execute_destruction sans relayer à "
            "super() — ils neutralisent les gardes des autres ponts en "
            "silence : %s" % muets,
        )

    def test_native_scan_is_refused_on_a_cron_fed_campaign(self):
        """« Balayer » lit les classifications : il viderait l'écran."""
        self._email(self._years(self.rule_unfiled) + 10)
        self._propose()
        campaign = self.Campaign.search([
            ("retention_calendar_id", "=", self.rule_unfiled.id)
        ], limit=1)
        campaign.sudo().write({"state": "draft"})
        with self.assertRaises(UserError):
            campaign.sudo().action_scan()

    # ── La garde anti-ré-ingestion ───────────────────────────────────────

    def test_rule_under_reconcile_floor_proposes_nothing(self):
        """Sous la fenêtre de réconciliation IMAP, on ne propose pas.

        `_cron_imap_reconcile` re-créerait la ligne au passage suivant : la
        destruction ne détruirait rien et le registre certifierait du vent.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email.reconcile_days", "30"
        )
        self.rule_unfiled.sudo().write({
            "active_retention_years": 0, "semi_active_retention_years": 0,
        })
        old = self._email(400)

        self.assertEqual(self._propose(), 0)
        self.assertTrue(old.exists())
        self.assertEqual(
            self.Line.search_count([("res_model", "=", "bf.email")]), 0
        )
        _expired, held = self.BfEmail._privacy_scan_expired()
        self.assertTrue(
            any("plancher" in motif for motif in held),
            "le motif doit nommer le plancher de réconciliation",
        )

    def test_floor_follows_the_reconcile_parameter(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("bf_email.reconcile_days", "90")
        self.assertEqual(self.BfEmail._privacy_reconcile_floor_days(), 120)
        ICP.set_param("bf_email.reconcile_days", "pas un nombre")
        self.assertEqual(
            self.BfEmail._privacy_reconcile_floor_days(), 60,
            "un paramètre illisible retombe sur le défaut, il ne plante pas",
        )

    # ── Régime 2 — le courriel versé à un dossier ────────────────────────

    def test_filed_on_open_project_is_kept_forever(self):
        """Tant que le projet vit, la fin n'est pas accomplie."""
        project = self.env["project.project"].create({
            "name": "Projet vivant", "stage_id": self.open_stage.id,
        })
        task = self.env["project.task"].create({
            "name": "Tâche", "project_id": project.id,
        })
        row = self._email(
            self._years(self.rule_filed) + 4000,
            res_model="project.task", res_id=task.id, source="gateway",
        )
        self._propose()
        self._execute_campaigns()
        self.assertTrue(
            row.exists(), "un courriel de dix ans sur un projet ouvert reste"
        )

    def test_filed_on_closed_project_expires_from_the_closure(self):
        """L'ancrage est la clôture du projet, pas la date du courriel."""
        project = self._closed_project(self._years(self.rule_filed) + 10)
        task = self._quiet("project.task").create({
            "name": "Tâche close", "project_id": project.id,
        })
        # Courriel RÉCENT sur un projet clos depuis longtemps : c'est la
        # clôture qui décide, donc il est échu malgré sa fraîcheur.
        row = self._email(
            5, res_model="project.task", res_id=task.id, source="gateway",
        )
        self._propose()
        self._execute_campaigns()
        self.assertFalse(row.exists())
        entry = self.Register.search([], order="id desc", limit=1)
        self.assertEqual(entry.retention_calendar_id, self.rule_filed)

    def test_project_id_bearing_models_resolve(self):
        """Les modèles qui portent project_id suivent le régime du dossier.

        On éprouve celui qui est présent sur le banc : `sale.order`,
        `helpdesk.ticket` et les autres viennent de modules qui ne sont pas des
        dépendances de `bf_email_management`.
        """
        candidates = [
            m for m in self.BfEmail._privacy_project_bearing_models()
            if m not in ("project.project", "project.task")
            and m in self.env
            and "project_id" in self.env[m]._fields
        ]
        if not candidates:
            self.skipTest(
                "aucun des six modèles à project_id n'est installé sur ce banc"
            )
        project = self._closed_project(self._years(self.rule_filed) + 10)
        for model in sorted(candidates):
            target = self.env[model].sudo().search(
                [("project_id", "=", project.id)], limit=1
            )
            if not target:
                continue
            row = self._email(
                5, res_model=model, res_id=target.id, source="gateway",
            )
            anchor, rule, motif = row._privacy_retention_anchor()
            self.assertTrue(anchor, f"{model} devait être datable : {motif}")
            self.assertEqual(rule, self.rule_filed)
        # Le contrat minimal, lui, tient toujours : les six sont déclarés.
        for model in ("meeting.record", "sale.order", "helpdesk.ticket",
                      "project.document", "meeting.agenda",
                      "project.knowledge.item"):
            self.assertIn(model, self.BfEmail._privacy_project_bearing_models())

    def test_folded_but_still_talking_is_held(self):
        """🔴 Ranger n'est pas clore.

        Un projet d'accueil est en étape « Terminé » avec quarante tâches
        ouvertes et un mois de messages ; un projet interne est archivé avec un
        millier de tâches ouvertes. Un dossier rangé qui parle encore n'est pas
        clos, et ses courriels ne sont pas échus.
        """
        project = self.env["project.project"].create({
            "name": "Rangé mais vivant", "stage_id": self.folded.id,
        })
        task = self.env["project.task"].create({
            "name": "Tâche vivante", "project_id": project.id,
        })
        task.message_post(body="On y travaille encore aujourd'hui.")
        row = self._email(
            self._years(self.rule_filed) + 4000,
            res_model="project.task", res_id=task.id, source="gateway",
        )
        self._propose()
        self._execute_campaigns()
        self.assertTrue(row.exists())
        _expired, held = self.BfEmail._privacy_scan_expired()
        self.assertTrue(
            any("encore actif" in motif for motif in held),
            "le motif doit dire que le dossier parle encore",
        )

    def test_archiving_alone_does_not_close_a_dossier(self):
        """Archiver sert à désencombrer la liste, pas à clore."""
        project = self.env["project.project"].create({
            "name": "Archivé mais vivant", "stage_id": self.open_stage.id,
        })
        task = self.env["project.task"].create({
            "name": "Tâche récente", "project_id": project.id,
        })
        task.message_post(body="Message d'aujourd'hui.")
        project.write({"active": False})
        row = self._email(
            self._years(self.rule_filed) + 4000,
            res_model="project.task", res_id=task.id, source="gateway",
        )
        self._propose()
        self._execute_campaigns()
        self.assertTrue(row.exists())
        # ⚠️ « la ligne existe encore » ne prouve rien ici : elle survivrait
        # aussi si le dossier était tenu pour clos AUJOURD'HUI, l'échéance
        # tombant alors dans cinq ans. Ce qui distingue, c'est le MOTIF.
        _expired, held = self.BfEmail._privacy_scan_expired()
        self.assertTrue(
            any("encore actif" in motif for motif in held),
            "un dossier archivé qui parle encore doit être RETENU comme actif, "
            "pas daté d'une clôture d'aujourd'hui",
        )

    def test_declared_end_date_alone_does_not_close_a_dossier(self):
        """« Date de fin » est une fin PRÉVUE : une bonne part des projets
        qui en portent une sont encore actifs."""
        project = self.env["project.project"].create({
            "name": "Date posée mais vivant",
            "stage_id": self.folded.id,
            "date": fields.Date.today() - timedelta(days=4000),
        })
        task = self.env["project.task"].create({
            "name": "Tâche encore vivante", "project_id": project.id,
        })
        task.message_post(body="Toujours en cours malgré la date.")
        row = self._email(
            10, res_model="project.task", res_id=task.id, source="gateway",
        )
        self._propose()
        self._execute_campaigns()
        self.assertTrue(
            row.exists(),
            "une date de fin déclarée ne prime pas sur une activité récente",
        )
        _expired, held = self.BfEmail._privacy_scan_expired()
        self.assertTrue(
            any("encore actif" in motif for motif in held),
            "la date déclarée ne doit pas transformer un dossier bavard en "
            "dossier clos",
        )

    def test_quiet_period_follows_the_parameter(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_privacy.closure_quiet_days", "365")
        self.assertEqual(self.BfEmail._privacy_closure_quiet_days(), 365)
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_privacy.closure_quiet_days", "pas un nombre")
        self.assertEqual(self.BfEmail._privacy_closure_quiet_days(), 180)

    def test_closure_date_is_the_last_sign_of_life_not_write_date(self):
        """🔴 Presque tous les projets portaient le même write_date.

        Dater une clôture par « dernière modification » proposait donc la date
        d'une passe automatisée pour la plupart des dossiers.
        """
        span = self._years(self.rule_filed)
        project = self._closed_project(span + 10, name="Clos et silencieux")
        # Une écriture d'aujourd'hui, comme une passe de masse en ferait une :
        # elle déplace `write_date` sans être une activité.
        project.with_context(tracking_disable=True).write(
            {"description": "retouche du jour"})
        self.assertEqual(
            project.write_date.date(), fields.Date.today(),
            "le montage doit bien avoir déplacé write_date, sinon le test "
            "ne prouve rien",
        )
        closure = self.BfEmail._privacy_project_closure_date(project)
        self.assertEqual(
            closure, fields.Date.today() - timedelta(days=span + 10),
            "la clôture suit le dernier signe de vie, pas la dernière écriture",
        )

    def test_model_without_a_project_is_held(self):
        """Un modèle sans projet est conservé, et compté sous son nom.

        En production ce sont surtout des pistes CRM et des factures, par
        milliers. `res.partner` sert ici de représentant :
        `crm` n'est pas une dépendance du module, donc le banc ne l'a pas.
        """
        partner = self.env["res.partner"].create({"name": "Correspondant"})
        row = self._email(
            9000, res_model="res.partner", res_id=partner.id, source="gateway",
        )
        self._propose()
        self._execute_campaigns()
        self.assertTrue(row.exists())
        _expired, held = self.BfEmail._privacy_scan_expired()
        self.assertTrue(any("res.partner" in motif for motif in held))

    def test_dead_pointer_is_held_with_its_own_reason(self):
        """Une cible disparue ne devient pas une destruction par défaut."""
        project = self.env["project.project"].create({
            "name": "Projet à supprimer", "stage_id": self.open_stage.id,
        })
        task = self.env["project.task"].create({
            "name": "Tâche à supprimer", "project_id": project.id,
        })
        row = self._email(
            9000, res_model="project.task", res_id=task.id, source="gateway",
        )
        task.unlink()
        self._propose()
        self._execute_campaigns()
        self.assertTrue(row.exists())
        _expired, held = self.BfEmail._privacy_scan_expired()
        self.assertTrue(any("disparue" in motif for motif in held))

    # ── Simulation et aperçu ─────────────────────────────────────────────

    def test_dry_run_proposes_nothing(self):
        row = self._email(self._years(self.rule_unfiled) + 10)
        self.assertEqual(self._propose(dry_run=True), 0)
        self.assertTrue(row.exists())
        self.assertEqual(
            self.Line.search_count([("res_model", "=", "bf.email")]), 0
        )

    def test_preview_shows_what_the_scan_measured(self):
        """L'écran relit ce que le balayage a écrit, pas une seconde lecture."""
        self._email(self._years(self.rule_unfiled) + 10)
        preview = self.BfEmail._privacy_refresh_preview()
        self.assertGreaterEqual(
            preview["expired"], 1,
            "le montage doit produire au moins une échéance, sinon l'égalité "
            "ci-dessous serait vraie pour rien",
        )
        settings = self.env["res.config.settings"].create({})
        self.assertEqual(settings.bf_email_expired_count, preview["expired"])
        self.assertTrue(settings.bf_email_preview_taken_at)

    def test_preview_says_so_when_never_computed(self):
        """Jamais calculé ≠ zéro échéance : l'écran doit le dire."""
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", "bf_email_privacy.last_scan")]
        ).unlink()
        settings = self.env["res.config.settings"].create({})
        self.assertIn("jamais calculé", settings.bf_email_held_summary)

    def test_cron_refreshes_the_preview(self):
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", "bf_email_privacy.last_scan")]
        ).unlink()
        self._email(self._years(self.rule_unfiled) + 10)
        self._propose()
        self.assertIsNotNone(self.BfEmail._privacy_read_preview())

    def test_settings_write_reaches_the_calendar_rule(self):
        """L'écran est une façade : il écrit bien sur la règle."""
        settings = self.env["res.config.settings"].create({
            "bf_email_unfiled_years": 7,
        })
        settings.execute()
        self.assertEqual(self.rule_unfiled.active_retention_years, 7)

    def test_batch_limit_is_honoured(self):
        span = self._years(self.rule_unfiled)
        for offset in range(4):
            self._email(span + 20 + offset)
        self.assertEqual(self._propose(limit=2), 2)

    # ── Classification ───────────────────────────────────────────────────

    def test_bf_email_is_classifiable(self):
        """Un courriel particulièrement sensible peut être classifié à la main."""
        row = self._email(3)
        classification = self.env["privacy.document.classification"].create({
            "res_model": "bf.email",
            "res_id": row.id,
            "pi_category": "medical",
            "sensitivity_level": "highly_confidential",
        })
        self.assertTrue(classification.id)
