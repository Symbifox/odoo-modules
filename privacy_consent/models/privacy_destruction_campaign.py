import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import privacy_destruction_ops as destruction_ops

_logger = logging.getLogger(__name__)


class _LineNotCertifiable(Exception):
    """La ligne ne s'est pas détruite : rien ne doit être inscrit au registre.

    Interne à ``action_execute``. Elle sert à sortir du point de sauvegarde
    sans laisser passer la création de l'entrée, tout en distinguant ce cas
    d'une vraie erreur — l'exécuteur s'est déjà expliqué dans l'état de la
    ligne, il n'y a pas de trace de pile à produire.
    """


class PrivacyDestructionCampaign(models.Model):
    """Campagne de destruction en lot (Art. 23 LPRPSP).

    Permet d'exécuter des purges périodiques basées sur le calendrier
    de conservation. Le processus : scan → révision → approbation → exécution.
    Chaque document détruit génère une entrée au registre de destruction.
    """

    _name = "privacy.destruction.campaign"
    _description = "Campagne de destruction"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Nom",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        selection=[
            ("draft", "Brouillon"),
            ("scanning", "Balayage"),
            ("review", "Révision"),
            ("approved", "Approuvée"),
            ("executing", "En cours"),
            ("completed", "Terminée"),
            ("cancelled", "Annulée"),
        ],
        string="État",
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )

    # Scope
    retention_calendar_id = fields.Many2one(
        comodel_name="privacy.retention.calendar",
        string="Règle de conservation",
        help="Limiter à une règle spécifique (vide = toutes les règles)",
    )
    cutoff_date = fields.Date(
        string="Date limite",
        required=True,
        default=fields.Date.today,
        help="Détruire les documents dont la rétention expire avant cette date",
    )

    # Lines
    line_ids = fields.One2many(
        comodel_name="privacy.destruction.campaign.line",
        inverse_name="campaign_id",
        string="Documents identifiés",
    )
    line_count = fields.Integer(
        string="Total",
        compute="_compute_line_stats",
    )
    executed_count = fields.Integer(
        string="Exécutés",
        compute="_compute_line_stats",
    )
    failed_count = fields.Integer(
        string="Échecs",
        compute="_compute_line_stats",
    )
    skipped_count = fields.Integer(
        string="Ignorés",
        compute="_compute_line_stats",
    )

    # Approval
    created_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Créée par",
        default=lambda self: self.env.user,
    )
    approved_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Approuvée par",
        tracking=True,
    )
    approved_date = fields.Datetime(
        string="Date d'approbation",
    )

    # Execution timestamps
    started_at = fields.Datetime(
        string="Démarrée le",
    )
    completed_at = fields.Datetime(
        string="Terminée le",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
    )
    notes = fields.Text(string="Notes")

    def _compute_line_stats(self):
        for record in self:
            lines = record.line_ids
            record.line_count = len(lines)
            record.executed_count = len(lines.filtered(lambda l: l.state == "done"))
            record.failed_count = len(lines.filtered(lambda l: l.state == "failed"))
            record.skipped_count = len(lines.filtered(lambda l: l.state == "skipped"))

    def action_scan(self):
        """Scan for documents past their retention period."""
        for campaign in self:
            if campaign.state != "draft":
                raise UserError("Seules les campagnes en brouillon peuvent être balayées.")
            campaign.write({"state": "scanning"})

            # Build domain
            domain = [
                ("retention_expiry_date", "<=", campaign.cutoff_date),
                ("retention_expiry_date", "!=", False),
                ("active", "=", True),
            ]
            if campaign.retention_calendar_id:
                domain.append(
                    ("retention_calendar_id", "=", campaign.retention_calendar_id.id)
                )
            if campaign.company_id:
                domain.append(("company_id", "=", campaign.company_id.id))

            classifications = self.env["privacy.document.classification"].search(domain)

            # Filter out documents that already have a pending/done campaign line
            existing_lines = self.env["privacy.destruction.campaign.line"].search([
                ("classification_id", "in", classifications.ids),
                ("state", "in", ["pending", "done"]),
            ])
            existing_cls_ids = set(existing_lines.mapped("classification_id").ids)
            new_classifications = classifications.filtered(
                lambda c: c.id not in existing_cls_ids
            )

            # Create campaign lines
            line_vals = []
            for cls in new_classifications:
                method = "delete"
                if cls.retention_calendar_id:
                    method = cls.retention_calendar_id.destruction_method or "delete"
                line_vals.append({
                    "campaign_id": campaign.id,
                    "classification_id": cls.id,
                    "res_model": cls.res_model,
                    "res_id": cls.res_id,
                    "res_name": cls.res_name or f"{cls.res_model},{cls.res_id}",
                    "retention_calendar_id": cls.retention_calendar_id.id if cls.retention_calendar_id else False,
                    "retention_expiry_date": cls.retention_expiry_date,
                    "destruction_method": method,
                    "state": "pending",
                })

            if line_vals:
                self.env["privacy.destruction.campaign.line"].create(line_vals)

            campaign.write({"state": "review"})
            campaign.message_post(
                body=f"Balayage terminé : {len(line_vals)} document(s) identifié(s) pour destruction.",
                message_type="notification",
            )
        return True

    def action_approve(self):
        """Approve campaign for execution (Privacy Officer only)."""
        if not self.env.su and not self.env.user.has_group(
            "privacy_consent.group_privacy_officer"
        ):
            raise UserError(
                "Seul le responsable de la vie privée peut approuver "
                "une campagne de destruction."
            )
        for campaign in self:
            if campaign.state != "review":
                raise UserError("Seules les campagnes en révision peuvent être approuvées.")
            if not campaign.line_ids:
                raise UserError("La campagne ne contient aucun document à détruire.")
            campaign.write({
                "state": "approved",
                "approved_by_id": self.env.user.id,
                "approved_date": fields.Datetime.now(),
            })
            campaign.message_post(
                body=f"Campagne approuvée par {self.env.user.name}.",
                message_type="notification",
            )
        return True

    def action_execute(self):
        """Execute destruction for all pending lines (Privacy Officer only)."""
        if not self.env.su and not self.env.user.has_group(
            "privacy_consent.group_privacy_officer"
        ):
            raise UserError(
                "Seul le responsable de la vie privée peut exécuter "
                "une campagne de destruction."
            )
        for campaign in self:
            if campaign.state != "approved":
                raise UserError("Seules les campagnes approuvées peuvent être exécutées.")
            campaign.write({
                "state": "executing",
                "started_at": fields.Datetime.now(),
            })

            Register = self.env["privacy.destruction.register"]

            for line in campaign.line_ids.filtered(lambda l: l.state == "pending"):
                # 🔴 Ce qu'il faut savoir avant de toucher à cette boucle.
                #
                # 1. `_execute_destruction` NE LÈVE PAS toujours quand il
                #    échoue : plusieurs de ses sorties écrivent « échec » ou
                #    « ignoré » dans l'état de la ligne et rendent la main
                #    normalement. La version d'avant la 18.0.5.0.0 enchaînait alors sur
                #    la création de l'entrée de registre et RÉÉCRIVAIT l'état à
                #    « fait ». Une ligne sautée était donc certifiée détruite.
                #    On relit donc l'état après l'appel, et on refuse de
                #    certifier ce que l'exécuteur vient de dire non fait.
                #
                # 2. Le point de sauvegarde n'est pas un ornement. Un `unlink`
                #    sur un enregistrement retenu par une clé étrangère lève une
                #    `IntegrityError` qui AVORTE la transaction : sans
                #    savepoint, l'`except` attrapait bien l'erreur mais toutes
                #    les instructions suivantes — y compris l'écriture de
                #    l'état d'échec — mouraient sur « current transaction is
                #    aborted », et la campagne entière tombait sur sa première
                #    ligne récalcitrante.
                #
                # 3. Ce que le savepoint garantit en plus : ou bien la ligne est
                #    détruite ET certifiée, ou bien ni l'un ni l'autre. Une
                #    destruction à moitié faite ne peut plus survivre à
                #    l'échec de sa certification.
                outcome = {"state": "failed", "error": "", "entry": False,
                           "applied": None}
                try:
                    with self.env.cr.savepoint():
                        line._execute_destruction()
                        if line.state != "pending":
                            # L'exécuteur s'est marqué lui-même. On garde SON
                            # verdict et son message, on annule le reste.
                            outcome["state"] = line.state
                            outcome["error"] = line.error_message or ""
                            # ⚠ Relevé AVANT la levée, qui annule le point de
                            # sauvegarde : sans ça, une ligne « Ignoré » ne
                            # garde aucune trace de ce que la primitive a
                            # répondu, et le responsable ne lit que l'erreur.
                            outcome["applied"] = {
                                "applied_label": line.applied_label or False,
                                "applied_scope": line.applied_scope or False,
                                "applied_fields": line.applied_fields or False,
                            }
                            raise _LineNotCertifiable()
                        # Create register entry
                        register_vals = {
                            "campaign_id": campaign.id,
                            "destruction_date": fields.Datetime.now(),
                            "destroyed_by_id": self.env.user.id,
                            "approved_by_id": campaign.approved_by_id.id,
                            "res_model": line.res_model,
                            "res_id": line.res_id,
                            "res_name": line.res_name,
                            "document_description": (
                                f"Destruction par campagne « {campaign.name} ». "
                                f"Document : {line.res_name}."
                            ),
                            "pi_categories": line.classification_id.pi_category if line.classification_id else "",
                            "subject_count": 1,
                            "destruction_method": line.destruction_method,
                            # 🔴 La portée vient de ce qui a été fait,
                            # pas du défaut du champ. Un pont qui surcharge
                            # `_execute_destruction` sans la renseigner retombe
                            # sur « entier », et la garde du registre l'arrête
                            # si son effet ne la justifie pas.
                            **({"destruction_scope": line.applied_scope,
                                "res_field": line.applied_fields}
                               if line.applied_scope else {}),
                            "legal_basis": self._build_line_legal_basis(line),
                            "retention_calendar_id": line.retention_calendar_id.id if line.retention_calendar_id else False,
                            "company_id": campaign.company_id.id or self.env.company.id,
                        }
                        entry = Register.create(register_vals)
                        outcome["state"] = "done"
                        outcome["entry"] = entry.id
                except _LineNotCertifiable:
                    _logger.info(
                        "Campaign %s: line %s (%s,%s) not certified — executor "
                        "reported state %s",
                        campaign.name, line.id, line.res_model, line.res_id,
                        outcome["state"],
                    )
                except Exception as e:
                    _logger.exception(
                        "Campaign %s: failed to destroy line %s (%s,%s): %s",
                        campaign.name, line.id, line.res_model, line.res_id, e
                    )
                    outcome["state"] = "failed"
                    outcome["error"] = str(e)
                # ⚠ HORS du point de sauvegarde : une écriture faite dedans
                # serait annulée avec lui, et la ligne resterait « en attente »
                # à jamais, prête à être rejouée par la campagne suivante.
                line.write({
                    "state": outcome["state"],
                    "error_message": outcome["error"] or False,
                    "register_entry_id": outcome["entry"] or False,
                    **(outcome["applied"] or {}),
                })

            campaign.write({
                "state": "completed",
                "completed_at": fields.Datetime.now(),
            })
            campaign.message_post(
                body=(
                    f"Campagne terminée. "
                    f"{campaign.executed_count} détruit(s), "
                    f"{campaign.failed_count} échec(s), "
                    f"{campaign.skipped_count} ignoré(s)."
                ),
                message_type="notification",
            )
            campaign._privacy_review_skipped_lines()
        return True

    def _privacy_review_skipped_lines(self):
        """Lève une activité de revue pour les lignes qui n'ont rien retiré.

        🔴 Depuis que « rien détruit, rien
        certifié » vaut sur la campagne, une ligne dont la méthode ne retire
        rien se marque « Ignoré » et sa classification reste ACTIVE, pour ne pas
        disparaître des campagnes suivantes. Sans geste, la campagne suivante la
        rebalaye, la réignore, et personne n'est jamais saisi : des centaines de
        lignes ignorées par mois sur une base réelle, dont le défaut réel est la
        règle de conservation, pas la campagne.

        ⚠ Pas d'état terminal dédupliqué à `action_scan` : il ferait
        disparaître la classification de toutes les campagnes à venir, et
        l'obligation ne serait plus jamais honorée, en silence. Ce qui ferme la
        boucle est une décision sur la RÈGLE (choisir « Supprimer », ou déclarer
        un anonymiseur pour le modèle), donc une personne. L'activité le dit, et
        l'écart se redira tant que la règle n'aura pas changé.
        """
        self.ensure_one()
        ignorees = self.line_ids.filtered(lambda l: l.state == "skipped")
        if not ignorees:
            return False
        motifs = []
        for motif in ignorees.mapped("error_message"):
            motif = (motif or "").strip()
            if motif and motif not in motifs:
                motifs.append(motif)
        regles = ", ".join(sorted(set(
            ignorees.mapped("retention_calendar_id.name")))) or "(aucune règle nommée)"
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            summary=_("Destruction : %s document(s) ignoré(s), rien n'a été "
                      "retiré", len(ignorees)),
            note=_(
                "<p>La campagne « %(nom)s » a terminé en laissant "
                "%(n)s ligne(s) « Ignoré » : la méthode demandée ne retire rien, "
                "ou le modèle ne sait pas s'anonymiser. Rien n'a été certifié "
                "et rien n'a été inscrit au registre, et les classifications "
                "restent ACTIVES, donc la prochaine campagne les reverra.</p>"
                "<p><b>Règle(s) de conservation en cause :</b> %(regles)s</p>"
                "<p><b>Motifs relevés :</b></p><ul>%(motifs)s</ul>"
                "<p>Pour fermer la boucle : choisir « Supprimer » sur la règle, "
                "ou déclarer « _privacy_anonymize() » sur le modèle visé. "
                "L'écart se redira à chaque campagne tant que ce ne sera pas "
                "tranché.</p>",
                nom=self.name, n=len(ignorees), regles=regles,
                motifs="".join("<li>%s</li>" % m for m in motifs[:10]),
            ),
            # ⚠ Au responsable qui a APPROUVÉ la campagne, pas à l'exécutant :
            # sous cron, `env.user` est le compte système, et une activité qui
            # lui est assignée n'est lue par personne.
            user_id=(self.approved_by_id or self.env.user).id,
        )
        return True

    def _build_line_legal_basis(self, line):
        """Build legal basis text for a campaign line."""
        parts = ["Art. 23 LPRPSP (obligation de destruction)"]
        if line.retention_calendar_id and line.retention_calendar_id.legal_basis:
            parts.append(line.retention_calendar_id.legal_basis)
        return " | ".join(parts)

    def action_cancel(self):
        """Cancel the campaign."""
        for campaign in self:
            if campaign.state == "completed":
                raise UserError("Impossible d'annuler une campagne terminée.")
            campaign.write({"state": "cancelled"})
            campaign.message_post(
                body="Campagne annulée.",
                message_type="notification",
            )
        return True


class PrivacyDestructionCampaignLine(models.Model):
    """Ligne de campagne de destruction."""

    _name = "privacy.destruction.campaign.line"
    _description = "Ligne de campagne de destruction"
    _order = "state, id"

    campaign_id = fields.Many2one(
        comodel_name="privacy.destruction.campaign",
        string="Campagne",
        required=True,
        ondelete="cascade",
        index=True,
    )
    classification_id = fields.Many2one(
        comodel_name="privacy.document.classification",
        string="Classification",
        ondelete="set null",
    )

    # Snapshot (survives even if source record is deleted)
    res_model = fields.Char(string="Modèle")
    res_id = fields.Integer(string="ID")
    res_name = fields.Char(string="Document")

    retention_calendar_id = fields.Many2one(
        comodel_name="privacy.retention.calendar",
        string="Règle de conservation",
    )
    retention_expiry_date = fields.Date(
        string="Fin de rétention",
    )

    state = fields.Selection(
        selection=[
            ("pending", "En attente"),
            ("done", "Détruit"),
            ("failed", "Échec"),
            ("skipped", "Ignoré"),
        ],
        string="État",
        default="pending",
        required=True,
        index=True,
    )
    destruction_method = fields.Selection(
        selection=[
            ("anonymize", "Anonymiser"),
            ("delete", "Supprimer"),
            ("secure_wipe", "Effacement sécurisé"),
            ("manual", "Manuel"),
        ],
        string="Méthode",
        default="delete",
    )
    error_message = fields.Text(string="Erreur")
    # 🔴 Ce que la primitive a rendu, pour que l'entrée de registre le
    # reprenne au lieu de supposer une portée « enregistrement entier ».
    applied_label = fields.Char(string="Effet appliqué", readonly=True, copy=False)
    applied_scope = fields.Selection(
        selection=[("full", "Enregistrement entier"),
                   ("partial", "Partie de l'enregistrement")],
        string="Portée appliquée",
        readonly=True,
        copy=False,
    )
    applied_fields = fields.Char(
        string="Champs vidés", readonly=True, copy=False,
        help="Ce que l'anonymisation a retiré, repris par le registre.",
    )
    register_entry_id = fields.Many2one(
        comodel_name="privacy.destruction.register",
        string="Entrée registre",
        ondelete="set null",
    )

    def _execute_destruction(self):
        """Execute destruction for this single line.

        Handles the actual record modification/deletion based on method.
        Validates access rights before using sudo() for the actual operation.

        ⚠ Un pont qui surcharge cette méthode DOIT relayer à ``super()`` pour
        les modèles qu'il ne possède pas. Cinq ponts la surchargent déjà et ne
        cohabitent que par ce relais ; un sixième qui l'oublierait ferait taire
        les gardes de tous les autres, en silence. La garde de
        ``privacy.destruction.register.create()`` est la seconde ligne, posée
        exprès sur une méthode que ce contournement n'atteint pas.
        """
        self.ensure_one()
        if not self.res_model or not self.res_id:
            return

        # Validate method
        valid_methods = {"anonymize", "delete", "secure_wipe", "manual"}
        if self.destruction_method and self.destruction_method not in valid_methods:
            self.write({"state": "failed", "error_message": f"Méthode invalide : {self.destruction_method}"})
            return

        try:
            record = self.env[self.res_model].browse(self.res_id)
        except KeyError:
            _logger.warning("Model %s not found, skipping", self.res_model)
            self.write({"state": "skipped", "error_message": f"Modèle {self.res_model} non trouvé"})
            return

        if not record.exists():
            _logger.info("Record %s,%s already deleted", self.res_model, self.res_id)
            self.write({"state": "skipped", "error_message": "Enregistrement déjà supprimé"})
            return

        # Verify write access before escalating to sudo
        try:
            record.check_access_rights("write")
            record.check_access_rule("write")
        except Exception as e:
            _logger.warning(
                "Access denied for %s on %s,%s: %s",
                self.env.user.login, self.res_model, self.res_id, e,
            )
            self.write({"state": "failed", "error_message": f"Accès refusé : {e}"})
            return

        # 🔴 Ce bloc portait le même défaut : « Suppression » se
        # contentait d'archiver dès que le modèle avait un champ `active`. La
        # primitive partagée supprime pour de bon, contrôle après coup, et lève
        # si l'enregistrement survit. Voir models/privacy_destruction_ops.py.
        applied = destruction_ops.destroy_record(record, self.destruction_method)

        # 🔴 La ligne garde la portée rendue par la primitive. Sans
        # elle, l'entrée de registre retombait sur la portée par défaut,
        # « enregistrement entier », qui affirme la disparition d'un
        # enregistrement qu'une anonymisation garde debout.
        self.write({
            "applied_label": applied.label,
            "applied_scope": applied.scope,
            "applied_fields": applied.fields or False,
        })

        # 🔴 Rien détruit, rien certifié sur la CAMPAGNE aussi, qui
        # est le chemin qui écrit le plus d'entrées au registre. Un calendrier
        # en méthode « Révision manuelle » ne touchait rien, la ligne passait à
        # « fait », la classification était archivée donc invisible aux
        # campagnes suivantes, et une entrée immuable était scellée. La ligne se
        # marque elle-même « Ignoré » : la boucle de la campagne relit son état
        # après l'appel et ne certifie pas ce qu'elle vient de dire non fait.
        if not applied.removed:
            self.write({"state": "skipped", "error_message": _(
                "%s : rien n'a été retiré, donc rien n'est certifié et la "
                "classification reste à traiter.", applied.label)})
            return

        # 🔴 Le nom se relit APRÈS l'anonymisation, sur la ligne et sur
        # la classification. Le socle recopie `res_name` dans l'entrée de
        # registre, la seule table qui refuse `write` et `unlink` : sur une
        # fiche devenue « Personne anonymisée », il y scellait le nom d'avant,
        # qui survivait ainsi à son propre effacement. Trois ponts le faisaient
        # déjà chacun pour son modèle ; le socle le fait pour tous.
        if applied.scope == "partial":
            destruction_ops.refresh_res_name(record, self, self.classification_id)

        # Deactivate the classification
        if self.classification_id:
            self.classification_id.write({"active": False})

    def action_skip(self):
        """Manually skip this line."""
        for line in self:
            if line.state != "pending":
                continue
            line.write({"state": "skipped", "error_message": "Ignoré manuellement"})
        return True
