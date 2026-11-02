"""Rétention et destruction du contenu de l'inbox unifiée (Art. 3.2 LPRPSP).

Deux régimes, parce qu'il y a deux natures de ligne — voir la description du
manifeste. Un seul principe commun : on ne détruit que ce qu'on a su dater.
"""
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Marge exigée AU-DESSUS de la fenêtre de réconciliation IMAP. En deçà, une
# ligne détruite est re-créée par `_cron_imap_reconcile` au passage suivant :
# la destruction ne détruit rien et le registre certifie du vent.
RECONCILE_SAFETY_DAYS = 30

# Modèles dont on sait remonter au projet qui gouverne la rétention.
# Tout le reste est CONSERVÉ, faute d'ancrage de clôture.
#
# Les six derniers portent un `project_id` en propre : les ajouter n'invente
# aucun régime, ça applique la règle du courriel classé à des enregistrements
# qui désignent déjà leur dossier. Sur une base réelle, cela rend datables
# quelques centaines de courriels de plus.
#
# ⚠️ N'y mettre QUE des modèles dont le lien vers le projet est le dossier qui
# gouverne la rétention. `crm.lead` a une date de fermeture mais pas de projet ;
# `account.move` relève du régime FISCAL (6 ans, art. 35 LAF), pas contractuel.
# Les deux restent hors liste, donc conservés et comptés.
_PROJECT_BEARING_MODELS = (
    "project.project",
    "project.task",
    "meeting.record",
    "sale.order",
    "helpdesk.ticket",
    "project.document",
    "meeting.agenda",
    "project.knowledge.item",
)

# Les modèles ci-dessus, hormis les deux premiers, se résolvent tous de la même
# façon : un many2one `project_id` à lire. On les traite en un seul balayage.
_PROJECT_ID_MODELS = (
    "meeting.record",
    "sale.order",
    "helpdesk.ticket",
    "project.document",
    "meeting.agenda",
    "project.knowledge.item",
)

# ⚠️ La liste et le résolveur doivent bouger ENSEMBLE : le balayage narrowit
# par la liste, donc un modèle résolu mais non listé ne serait jamais lu.


class BfEmail(models.Model):
    _inherit = "bf.email"

    # ------------------------------------------------------------------
    # Règles du calendrier de conservation
    # ------------------------------------------------------------------

    @api.model
    def _privacy_rule_unfiled(self):
        """Règle applicable au courriel jamais versé à un dossier."""
        return self.env.ref(
            "bf_email_privacy.retention_bf_email_unfiled", raise_if_not_found=False
        )

    @api.model
    def _privacy_rule_filed(self):
        """Règle applicable au courriel versé au chatter d'un dossier."""
        return self.env.ref(
            "bf_email_privacy.retention_bf_email_filed", raise_if_not_found=False
        )

    # ------------------------------------------------------------------
    # Ancrage : à partir de quelle date la conservation court-elle ?
    # ------------------------------------------------------------------

    @api.model
    def _privacy_project_bearing_models(self):
        """Modèles dont une ligne classée sait désigner son projet.

        Point d'extension, en tandem avec `_privacy_resolve_projects`. ⚠️ Les
        deux bougent ensemble : le balayage narrowit par cette liste, donc un
        modèle résolu mais non listé ne serait jamais lu — et un modèle listé
        mais non résolu retombe simplement dans les lignes conservées, avec son
        motif, jamais dans les détruites.
        """
        return set(_PROJECT_BEARING_MODELS)

    @api.model
    def _privacy_resolve_projects(self, rows):
        """``{id de ligne: recordset projet}`` — résolution EN MASSE.

        En masse par construction, et pas par optimisation tardive : une
        résolution ligne par ligne, c'est une requête par courriel, donc
        plusieurs milliers sur l'écran de réglages. Une surcharge compose ::

            def _privacy_resolve_projects(self, rows):
                mapping = super()._privacy_resolve_projects(rows)
                mine = rows.filtered(lambda r: r.res_model == "hour.bank.client")
                for bank in self.env["hour.bank.client"].browse(mine.mapped("res_id")):
                    ...
                return mapping

        Les lignes absentes du dictionnaire n'ont pas de projet : pointeur
        mort, ou modèle hors périmètre. Elles sont conservées.
        """
        Project = self.env["project.project"]
        mapping = {}

        # Un dossier clos est très souvent archivé, et c'est justement celui-là
        # dont la rétention est échue : `active_test=False` des deux côtés.
        direct = rows.filtered(lambda r: r.res_model == "project.project")
        if direct:
            alive = Project.with_context(active_test=False).search(
                [("id", "in", direct.mapped("res_id"))]
            )
            by_id = {project.id: project for project in alive}
            for row in direct:
                project = by_id.get(row.res_id)
                if project:
                    mapping[row.id] = project

        via_task = rows.filtered(lambda r: r.res_model == "project.task")
        if via_task:
            tasks = self.env["project.task"].with_context(active_test=False).search(
                [("id", "in", via_task.mapped("res_id"))]
            )
            by_id = {task.id: task.project_id for task in tasks}
            for row in via_task:
                project = by_id.get(row.res_id)
                if project:
                    mapping[row.id] = project

        # Les modèles qui portent un `project_id` en propre, un balayage par
        # modèle. On vérifie que le champ existe avant de chercher : ces
        # modèles viennent de modules maison dont l'un peut être désinstallé,
        # et un domaine sur un champ absent lèverait au lieu de rendre vide.
        for model in _PROJECT_ID_MODELS:
            batch = rows.filtered(lambda r, m=model: r.res_model == m)
            if not batch:
                continue
            if model not in self.env or "project_id" not in self.env[model]._fields:
                _logger.info(
                    "bf_email_privacy: %s n'a pas de project_id ici, "
                    "ses courriels restent conservés", model,
                )
                continue
            targets = self.env[model].with_context(active_test=False).search(
                [("id", "in", batch.mapped("res_id"))]
            )
            by_id = {target.id: target.project_id for target in targets}
            for row in batch:
                project = by_id.get(row.res_id)
                if project:
                    mapping[row.id] = project

        return mapping

    def _privacy_email_project(self):
        """Projet qui gouverne la rétention de CETTE ligne classée.

        Enveloppe unitaire du résolveur en masse — même décision, pour les
        tests et l'inspection d'une ligne. Rend un recordset vide quand la
        ligne ne relève d'aucun projet, auquel cas elle est conservée.
        """
        self.ensure_one()
        Project = self.env["project.project"]
        if not self.res_model or not self.res_id:
            return Project
        if self.res_model not in self._privacy_project_bearing_models():
            return Project
        return self._privacy_resolve_projects(self).get(self.id, Project)

    @api.model
    def _privacy_closure_quiet_days(self):
        """Silence exigé avant de tenir un dossier pour clos.

        ⚠️ Mesuré sur une base réelle, et c'est le cœur du problème : **aucun
        signal déclaratif ne dit la clôture**.

        * « archivé » : une partie des projets archivés est encore active —
          archiver, c'est désencombrer la liste.
        * « étape repliée » : pire signal que l'archivage. Un projet d'accueil
          peut être en « Terminé » avec quarante tâches ouvertes et un mois de
          messages.
        * « date de fin posée » : c'est une date de fin PRÉVUE, pas une date de
          clôture, et une bonne partie de ces projets vit encore.
        * « repliée ET zéro tâche ouverte » : trop strict, ça ne retient presque
          rien.

        Le seul critère qui discrimine est le silence : plus aucun message ni
        feuille de temps. Il a écarté tous les dossiers encore vivants.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        try:
            return int(ICP.get_param("bf_email_privacy.closure_quiet_days", "180"))
        except (TypeError, ValueError):
            return 180

    @api.model
    def _privacy_project_activity(self, projects):
        """``{id de projet: date du dernier signe de vie}`` — en masse.

        Quatre requêtes pour l'ensemble des projets, pas deux par projet.

        ⚠️ On ne lit NI `write_date` NI `create_date` : sur une base réelle, la
        quasi-totalité des projets portaient le même `write_date`, trace d'une
        passe automatisée. Un premier essai de datation par « dernière
        modification » proposait donc cette date-là pour la plupart des
        dossiers. Une modification n'est pas une activité.
        """
        if not projects:
            return {}
        last = {}

        def keep(project_id, value):
            """⚠️ `read_group` rend une Date en chaîne et un Datetime en objet
            selon le champ agrégé : normaliser avant de comparer, sinon on
            compare une chaîne à une date et ça lève."""
            if not value:
                return
            if isinstance(value, str):
                try:
                    moment = date.fromisoformat(value[:10])
                except ValueError:
                    return
            elif isinstance(value, datetime):
                moment = value.date()
            elif isinstance(value, date):
                moment = value
            else:
                return
            if not last.get(project_id) or moment > last[project_id]:
                last[project_id] = moment

        # ⚠️ `account.analytic.line.project_id` n'existe que si `hr_timesheet`
        # est installé — ce n'est pas une dépendance de `bf_email_management`.
        # Sans la garde, `read_group` lève « Invalid field » et le balayage
        # entier s'arrête sur un banc qui n'a pas la feuille de temps.
        Timesheet = self.env["account.analytic.line"].sudo()
        if "project_id" in Timesheet._fields:
            for group in Timesheet.read_group(
                [("project_id", "in", projects.ids)], ["date:max"], ["project_id"]
            ):
                if group.get("project_id"):
                    keep(group["project_id"][0], group.get("date"))

        for group in self.env["mail.message"].sudo().read_group(
            [("model", "=", "project.project"), ("res_id", "in", projects.ids)],
            ["date:max"], ["res_id"],
        ):
            keep(group["res_id"], group.get("date"))

        tasks = self.env["project.task"].with_context(active_test=False).sudo().search_read(
            [("project_id", "in", projects.ids)], ["project_id"]
        )
        task_project = {t["id"]: t["project_id"][0] for t in tasks if t.get("project_id")}
        if task_project:
            for group in self.env["mail.message"].sudo().read_group(
                [("model", "=", "project.task"),
                 ("res_id", "in", list(task_project))],
                ["date:max"], ["res_id"],
            ):
                keep(task_project[group["res_id"]], group.get("date"))
        return last

    @api.model
    def _privacy_project_closure_date(self, project, closure_cache=None):
        """Date de clôture d'un projet, ou ``False`` si le dossier n'est pas clos.

        Un dossier est clos quand il est rangé (étape repliée ou archivé) **et**
        qu'il s'est tu depuis `closure_quiet_days`. La date retenue est celle du
        dernier signe de vie : c'est le moment où le dossier a réellement cessé.

        Le silence est réévalué à chaque balayage, donc un dossier qui repart
        repousse sa propre date de clôture — la règle se corrige d'elle-même.
        """
        if not project:
            return False
        if closure_cache is not None and project.id in closure_cache:
            return closure_cache[project.id]
        result = False
        if project.stage_id.fold or not project.active:
            activity = (closure_cache or {}).get("_activity") if closure_cache else None
            if activity is None:
                activity = self._privacy_project_activity(project)
            last = activity.get(project.id)
            quiet_before = fields.Date.context_today(self) - timedelta(
                days=self._privacy_closure_quiet_days()
            )
            if last and last <= quiet_before:
                result = last
            elif not last and project.date and project.date <= quiet_before:
                # Aucun signe de vie du tout : le dossier n'a peut-être jamais
                # servi. La date de fin déclarée devient le seul repère.
                result = project.date
        if closure_cache is not None:
            closure_cache[project.id] = result
        return result

    def _privacy_retention_anchor(self, closure_cache=None, project_map=None):
        """``(date d'ancrage, règle, motif)`` pour cette ligne.

        Une date d'ancrage ``False`` veut dire « conservée » : le motif dit
        alors pourquoi, et c'est ce motif que l'écran de réglages agrège.
        """
        self.ensure_one()
        if not self.res_model:
            rule = self._privacy_rule_unfiled()
            if not self.date:
                return False, rule, _("ligne sans date de réception")
            return self.date.date(), rule, False

        rule = self._privacy_rule_filed()
        if project_map is not None:
            project = project_map.get(self.id, self.env["project.project"])
        else:
            project = self._privacy_email_project()
        if not project:
            if self.res_model in self._privacy_project_bearing_models():
                # La cible relève bien d'un modèle porteur, mais elle a disparu
                # (pointeur mort) ou ne porte pas de projet. Distinguer les deux
                # motifs : le premier se répare en reclassant, le second en
                # rattachant la tâche.
                return False, rule, _(
                    "classé sur « %s » : cible disparue ou sans projet",
                    self.res_model,
                )
            return False, rule, _(
                "classé sur « %s », modèle sans projet rattaché", self.res_model
            )
        closure = self._privacy_project_closure_date(
            project, closure_cache=closure_cache
        )
        if not closure:
            if project.stage_id.fold or not project.active:
                return False, rule, _(
                    "projet « %s » rangé mais encore actif (silence de moins "
                    "de %s jours)", project.name,
                    self._privacy_closure_quiet_days(),
                )
            return False, rule, _("projet « %s » toujours ouvert", project.name)
        return closure, rule, False

    def _privacy_expiry_date(self):
        """Date à laquelle la conservation de cette ligne expire, ou ``False``."""
        self.ensure_one()
        anchor, rule, _motif = self._privacy_retention_anchor()
        if not anchor or not rule:
            return False
        return anchor + timedelta(days=rule.total_retention_days)

    # ------------------------------------------------------------------
    # Garde anti-ré-ingestion
    # ------------------------------------------------------------------

    @api.model
    def _privacy_reconcile_floor_days(self):
        """Nombre de jours sous lequel une rétention ne veut rien dire.

        `_cron_imap_reconcile` rebalaie INBOX et Sent sur cette fenêtre et
        re-crée toute ligne dont le Message-ID n'a plus de ligne. Détruire à
        l'intérieur, c'est alimenter une boucle.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        try:
            lookback = int(ICP.get_param("bf_email.reconcile_days", "30"))
        except (TypeError, ValueError):
            lookback = 30
        return lookback + RECONCILE_SAFETY_DAYS

    # ------------------------------------------------------------------
    # Balayage
    # ------------------------------------------------------------------

    @api.model
    def _privacy_rule_problem(self, rule, floor_days):
        """Ce qui empêche une règle de piloter une destruction, ou ``False``."""
        if not rule:
            return _("règle de conservation absente")
        if rule.final_disposition != "destroy":
            return _("règle « %s » : sort final ≠ destruction", rule.code)
        if rule.total_retention_days < floor_days:
            return _(
                "règle « %(code)s » sous le plancher de réconciliation IMAP "
                "(%(days)s jours) : les lignes détruites seraient re-créées",
                code=rule.code, days=floor_days,
            )
        return False

    @api.model
    def _privacy_scan_expired(self, limit=None):
        """``(échues, retenues_par_motif)`` — sans rien détruire.

        Sert au cron ET à l'aperçu de l'écran de réglages : c'est la même
        lecture, donc l'aperçu ne peut pas mentir sur ce que la passe fera.

        Le balayage est piloté par domaine, pas par une boucle sur la table :
        les lignes qui ne peuvent pas être échues ne sont jamais chargées, et
        celles qui sont retenues pour un motif lisible d'avance sont comptées
        par regroupement.
        """
        held = defaultdict(int)
        today = fields.Date.context_today(self)
        floor_days = self._privacy_reconcile_floor_days()
        Bf = self.with_context(active_test=False).sudo()
        expired = self.browse()

        # ── Régime 1 — le courriel qui dort dans la boîte ────────────────
        rule = self._privacy_rule_unfiled()
        problem = self._privacy_rule_problem(rule, floor_days)
        unfiled_domain = [("res_model", "=", False)]
        if problem:
            held[problem] += Bf.search_count(unfiled_domain)
        else:
            cutoff = today - timedelta(days=rule.total_retention_days)
            expired |= Bf.search(
                unfiled_domain + [("date", "<", cutoff)],
                order="date asc", limit=limit or None,
            )

        # ── Régime 2 — le courriel versé à un dossier ────────────────────
        rule = self._privacy_rule_filed()
        problem = self._privacy_rule_problem(rule, floor_days)
        if problem:
            held[problem] += Bf.search_count([("res_model", "!=", False)])
            return expired, dict(held)

        bearing = sorted(self._privacy_project_bearing_models())

        # Les modèles hors périmètre se comptent sans se charger.
        for group in Bf.read_group(
            [
                ("res_model", "!=", False),
                ("res_model", "not in", bearing),
            ],
            ["res_model"], ["res_model"],
        ):
            held[
                _("classé sur « %s », modèle sans projet rattaché",
                  group["res_model"])
            ] += group["res_model_count"]

        closure_cache = {}
        candidates = Bf.search(
            [("res_model", "in", bearing)],
            order="date asc",
        )
        project_map = self._privacy_resolve_projects(candidates)
        projects = self.env["project.project"].browse(
            {p.id for p in project_map.values()}
        )
        closure_cache["_activity"] = self._privacy_project_activity(projects)
        for row in candidates:
            anchor, rule_row, motif = row._privacy_retention_anchor(
                closure_cache=closure_cache, project_map=project_map
            )
            if not anchor:
                held[motif or _("ancrage indéterminé")] += 1
                continue
            if anchor + timedelta(days=rule_row.total_retention_days) > today:
                continue
            expired |= row
            if limit and len(expired) >= limit:
                break
        return expired, dict(held)

    # ------------------------------------------------------------------
    # Aperçu mémorisé
    # ------------------------------------------------------------------

    # L'écran de réglages ne relance PAS le balayage : sur les données réelles
    # il coûte quelques secondes, et la page Paramètres calcule tous les onglets à
    # l'ouverture. On mémorise donc ce que le balayage a mesuré, horodaté, et
    # l'écran se contente de le relire — un chiffre daté vaut mieux qu'un
    # chiffre frais qui fait attendre tout le monde.
    _PREVIEW_PARAM = "bf_email_privacy.last_scan"

    @api.model
    def _privacy_store_preview(self, expired, held):
        """Mémorise le résultat d'un balayage pour l'écran de réglages."""
        payload = {
            "taken_at": fields.Datetime.to_string(fields.Datetime.now()),
            "expired": len(expired),
            "held": {motif: count for motif, count in held.items()},
            "floor_days": self._privacy_reconcile_floor_days(),
        }
        self.env["ir.config_parameter"].sudo().set_param(
            self._PREVIEW_PARAM, json.dumps(payload)
        )
        return payload

    @api.model
    def _privacy_read_preview(self):
        """Dernier balayage mémorisé, ou ``None`` s'il n'y en a jamais eu."""
        raw = self.env["ir.config_parameter"].sudo().get_param(
            self._PREVIEW_PARAM
        )
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            _logger.warning(
                "bf_email_privacy: aperçu mémorisé illisible, il sera "
                "reconstruit au prochain balayage"
            )
            return None

    @api.model
    def _privacy_refresh_preview(self):
        """Balaie et mémorise. C'est ce que le bouton « Simuler » appelle."""
        expired, held = self._privacy_scan_expired()
        return self._privacy_store_preview(expired, held)

    # ------------------------------------------------------------------
    # Destruction
    # ------------------------------------------------------------------

    @api.model
    def _cron_privacy_propose_destruction(self, limit=None, dry_run=False):
        """Propose une campagne de destruction. Ne détruit RIEN.

        Le cron balaie et dépose les lignes échues dans une
        `privacy.destruction.campaign` laissée en révision. C'est le
        responsable de la vie privée qui approuve puis exécute, et c'est la
        campagne qui écrit au registre — pas nous.

        Rend le nombre de lignes proposées (0 en simulation).
        """
        if not (self._privacy_rule_unfiled() and self._privacy_rule_filed()):
            _logger.error(
                "bf_email_privacy: règle(s) de conservation absente(s), "
                "proposition abandonnée"
            )
            return 0

        if limit is None:
            ICP = self.env["ir.config_parameter"].sudo()
            try:
                limit = int(ICP.get_param("bf_email_privacy.batch_size", "500"))
            except (TypeError, ValueError):
                limit = 500

        expired, held = self._privacy_scan_expired()
        self._privacy_store_preview(expired, held)
        if held:
            _logger.info(
                "bf_email_privacy: %s ligne(s) conservées faute d'ancrage — %s",
                sum(held.values()),
                "; ".join(f"{motif} : {n}" for motif, n in sorted(held.items())),
            )
        if not expired:
            return 0

        expired = expired[:limit] if limit else expired
        if dry_run:
            _logger.info(
                "bf_email_privacy (simulation) : %s ligne(s) seraient proposées "
                "à la destruction", len(expired),
            )
            return 0

        proposed = 0
        by_regime = defaultdict(lambda: self.browse())
        for row in expired:
            by_regime["unfiled" if not row.res_model else "filed"] |= row
        for regime, rows in by_regime.items():
            proposed += self._privacy_propose_lines(regime, rows)
        return proposed

    @api.model
    def _privacy_propose_lines(self, regime, rows):
        """Dépose `rows` en lignes d'une campagne ouverte, sans rien détruire."""
        rule = (
            self._privacy_rule_unfiled()
            if regime == "unfiled"
            else self._privacy_rule_filed()
        )
        campaign = self._privacy_open_campaign(rule)
        Line = self.env["privacy.destruction.campaign.line"].sudo()

        # Ne pas re-proposer ce qui attend déjà dans une campagne ouverte : le
        # cron tourne tous les jours et l'approbation peut prendre des semaines.
        already = set(Line.search([
            ("campaign_id.state", "in",
             ["draft", "scanning", "review", "approved", "executing"]),
            ("res_model", "=", self._name),
            ("state", "=", "pending"),
        ]).mapped("res_id"))

        vals_list = []
        for row in rows:
            if row.id in already:
                continue
            vals_list.append({
                "campaign_id": campaign.id,
                "res_model": self._name,
                "res_id": row.id,
                # Le nom est figé ici : après la destruction, plus rien ne le
                # produit, et la ligne de campagne est ce qui en reste.
                "res_name": row.display_name or _("Courriel #%s", row.id),
                "retention_calendar_id": rule.id,
                "retention_expiry_date": row._privacy_expiry_date(),
                "destruction_method": rule.destruction_method or "delete",
                "state": "pending",
            })
        if not vals_list:
            return 0
        Line.create(vals_list)
        campaign.message_post(
            body=_(
                "%(count)s courriel(s) ajouté(s) par le balayage de rétention "
                "de l'inbox unifiée. Rien n'est détruit tant que la campagne "
                "n'est pas approuvée puis exécutée.",
                count=len(vals_list),
            ),
            message_type="notification",
        )
        return len(vals_list)

    @api.model
    def _privacy_open_campaign(self, rule):
        """Campagne ouverte pour cette règle, créée au besoin.

        Une seule campagne vivante par règle : le cron tourne tous les jours et
        en créerait autrement une par nuit, jusqu'à noyer la révision.
        """
        Campaign = self.env["privacy.destruction.campaign"].sudo()
        existing = Campaign.search([
            ("retention_calendar_id", "=", rule.id),
            ("state", "in", ["draft", "scanning", "review"]),
        ], order="id desc", limit=1)
        if existing:
            return existing
        campaign = Campaign.create({
            "name": _("Inbox unifiée — %s", rule.name),
            "retention_calendar_id": rule.id,
            "cutoff_date": fields.Date.context_today(self),
            "notes": _(
                "Campagne alimentée par le balayage de rétention de "
                "`bf_email_privacy`. Les lignes sont déposées par le cron ; "
                "l'approbation et l'exécution restent manuelles.\n\n%s",
                self._privacy_destruction_scope_statement(),
            ),
        })
        # `create` laisse la campagne en brouillon. On la passe en révision :
        # « Balayer » ne trouverait rien (le balayage natif lit les
        # classifications, pas nos lignes) et remettrait l'état à zéro.
        campaign.write({"state": "review"})
        campaign.message_post(
            body=_(
                "Campagne ouverte par le balayage de rétention de l'inbox "
                "unifiée. ⚠️ Ne pas utiliser « Balayer » : les lignes sont "
                "déposées par le cron, un balayage natif chercherait des "
                "classifications documentaires et n'en trouverait aucune."
            ),
            message_type="notification",
        )
        return campaign

    @api.model
    def _privacy_destruction_scope_statement(self):
        """Ce que la destruction ne couvre pas.

        ⚠️ Ce texte entre dans une entrée de registre IMMUABLE : ce qui y est
        écrit y reste. Il énonce donc un PRINCIPE vérifiable, pas un chiffre
        qui vieillit — et surtout pas une durée déduite d'une politique dont
        l'effet n'a pas été constaté.

        Deux versions antérieures se sont trompées, dans les deux sens :

        1. « rétention illimitée, de type legal hold » sur les dépôts de
           sauvegarde. Faux comme motif : une rétention illimitée décidée pour
           d'autres fichiers ne s'étend pas aux dépôts des bases Odoo.
        2. « disparu de toutes les copies environ 12 mois après ». Faux comme
           promesse : une politique annoncée de 14/8/12 peut cohabiter avec des
           centaines d'instantanés vieux de plusieurs mois, un par groupe de
           chemins, dès que chaque sauvegarde écrit dans un répertoire
           temporaire unique et défait ainsi le groupage par défaut de
           `restic forget`.

        D'où la règle : on dit que les sauvegardes ne sont pas atteintes, et
        que leur profondeur se VÉRIFIE au dépôt.
        """
        return _(
            "Portée : la ligne et sa copie RFC 2822 quittent la base Odoo ; le "
            "bloc quitte le filestore au passage suivant du ramasse-miettes des "
            "pièces jointes.\n"
            "Ne sont PAS atteintes par cette destruction : (1) les sauvegardes, "
            "qui gardent l'état antérieur aussi longtemps que leur propre "
            "rotation le conserve, selon la politique de sauvegarde de "
            "l'exploitant, dont la "
            "profondeur RÉELLE se vérifie au dépôt plutôt que de se déduire de "
            "la politique ; (2) la copie sur le serveur IMAP, le cas échéant, "
            "le module écrivant vers la boîte sans la vider.\n"
            "La méthode déclarée est « suppression », pas « effacement "
            "sécurisé » : on retire des lignes et des objets, sans garantie sur "
            "les blocs sous-jacents."
        )
