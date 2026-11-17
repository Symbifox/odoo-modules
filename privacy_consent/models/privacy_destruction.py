import hashlib
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import privacy_destruction_ops as destruction_ops

_logger = logging.getLogger(__name__)


class PrivacyDestructionRequest(models.Model):
    """Demande de destruction de données (Art. 23 LPRPSP).

    Gère la destruction de :
    - Enregistrements de consentement (anonymisation/archivage)
    - Documents classifiés contenant des RP
    - Identifiants de projet (effacement sécurisé) - si project_knowledge_matrix est installé
    - Données Nextcloud (activité pour suppression manuelle)
    """

    _name = "privacy.destruction.request"
    _description = "Demande de destruction de données"
    _inherit = ["mail.thread", "mail.activity.mixin", "privacy.framework.mixin"]
    _order = "scheduled_date, id"
    _rec_name = "certificate_number"

    # Request type
    request_type = fields.Selection(
        selection=[
            ("consent", "Consentement"),
            ("document", "Document"),
            ("erasure_right", "Droit à l'effacement"),
            ("campaign", "Campagne"),
        ],
        string="Type de demande",
        default="consent",
        required=True,
        tracking=True,
    )

    consent_id = fields.Many2one(
        comodel_name="privacy.consent",
        string="Consentement",
        required=False,
        # 🔴 `set null`, PAS `cascade`. Tant que « Suppression » se contentait
        # d'archiver, le consentement n'était jamais supprimé et la cascade ne
        # se déclenchait pas. Depuis la 18.0.5.0.0 elle se déclencherait, et elle
        # emporterait la demande de destruction elle-même — au beau milieu de
        # `action_execute`, donc AVANT l'écriture de l'état, la génération du
        # certificat et l'entrée au registre. La destruction aurait lieu et
        # effacerait sa propre trace.
        #
        # La demande est la pièce d'audit de la destruction : elle doit
        # survivre à ce qu'elle détruit. Le lien tombe à NULL, et
        # `consent_snapshot` (voir `action_execute`) garde le nom.
        ondelete="set null",
        index=True,
    )
    policy_id = fields.Many2one(
        comodel_name="privacy.retention.policy",
        string="Politique de rétention",
        required=False,
        ondelete="restrict",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Sujet",
        store=True,
        index=True,
        tracking=True,
    )
    purpose_id = fields.Many2one(
        related="consent_id.purpose_id",
        string="Finalité",
        store=True,
    )

    # Document destruction fields
    classification_ids = fields.Many2many(
        comodel_name="privacy.document.classification",
        string="Documents classifiés",
        help="Documents contenant des RP à détruire",
    )
    retention_calendar_id = fields.Many2one(
        comodel_name="privacy.retention.calendar",
        string="Règle de conservation",
        help="Règle du calendrier de conservation applicable",
    )
    campaign_id = fields.Many2one(
        comodel_name="privacy.destruction.campaign",
        string="Campagne",
        ondelete="set null",
    )
    register_entry_ids = fields.One2many(
        comodel_name="privacy.destruction.register",
        inverse_name="destruction_request_id",
        string="Entrées du registre",
    )
    document_count = fields.Integer(
        string="Documents ciblés",
        compute="_compute_document_count",
    )

    # Schedule
    trigger_date = fields.Datetime(
        string="Date de déclenchement",
        required=True,
        help="Date de début de la période de rétention (expiration ou révocation)",
    )
    scheduled_date = fields.Date(
        string="Date de destruction prévue",
        required=True,
        index=True,
        help="Date à laquelle la destruction doit être exécutée",
    )

    # State
    state = fields.Selection(
        selection=[
            ("pending", "En attente"),
            ("approved", "Approuvé"),
            ("executed", "Exécuté"),
            ("cancelled", "Annulé"),
        ],
        string="État",
        default="pending",
        required=True,
        tracking=True,
        index=True,
    )

    # Execution
    executed_at = fields.Datetime(
        string="Exécuté le",
        tracking=True,
    )
    executed_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Exécuté par",
        tracking=True,
    )
    destruction_method_used = fields.Selection(
        selection=[
            ("anonymize", "Anonymisé"),
            ("delete", "Supprimé"),
            ("secure_wipe", "Effacement sécurisé"),
            ("archive", "Archivé"),
            ("manual", "Manuel"),
        ],
        string="Méthode utilisée",
    )

    # Certificate
    certificate_number = fields.Char(
        string="Numéro de certificat",
        readonly=True,
        copy=False,
        index=True,
    )
    verification_hash = fields.Char(
        string="Empreinte de vérification",
        readonly=True,
        help="Empreinte SHA256 pour la vérification d'intégrité du certificat",
    )
    certificate_content = fields.Text(
        string="Contenu certifié",
        readonly=True,
        copy=False,
        help="Chaîne exacte sur laquelle l'empreinte de vérification a été calculée. "
             "Sans elle, l'empreinte affichée au certificat n'est pas rejouable.",
    )

    # Résultat RÉEL de l'exécution.
    # ⚠ Ne jamais reconstruire ce qui a été détruit depuis `classification_ids` :
    # une destruction réussie ARCHIVE la classification, donc le many2many ne rend
    # plus que les documents SAUTÉS. Le certificat listait exactement l'inverse de
    # la vérité. Ces deux champs figent le résultat au moment de l'exécution.
    destroyed_classification_ids = fields.Many2many(
        comodel_name="privacy.document.classification",
        relation="privacy_destruction_destroyed_cls_rel",
        column1="request_id",
        column2="classification_id",
        string="Documents réellement détruits",
        readonly=True,
        copy=False,
        context={"active_test": False},
    )
    skipped_classification_ids = fields.Many2many(
        comodel_name="privacy.document.classification",
        relation="privacy_destruction_skipped_cls_rel",
        column1="request_id",
        column2="classification_id",
        string="Documents non détruits",
        readonly=True,
        copy=False,
        context={"active_test": False},
        help="Documents ciblés que l'exécution n'a pas pu détruire (modèle absent, "
             "droits insuffisants, enregistrement déjà supprimé).",
    )
    destroyed_count = fields.Integer(
        string="Documents détruits",
        readonly=True,
        copy=False,
    )
    skipped_count = fields.Integer(
        string="Documents sautés",
        readonly=True,
        copy=False,
    )
    execution_log = fields.Text(
        string="Journal d'exécution",
        readonly=True,
        copy=False,
        help="Ce que l'exécution a réellement fait, ligne par ligne, y compris les échecs.",
    )
    # 🔴 Le journal dit tout, y compris les refus ; celui-ci ne garde que
    # ce qui a VRAIMENT été détruit ou anonymisé. `action_execute` refuse de
    # certifier quand il est vide : un certificat de destruction qui ne détruit
    # rien est faux, et le registre ne se corrige pas.
    applied_effects = fields.Text(
        string="Effets réels",
        readonly=True,
        copy=False,
        help="Ce qui a réellement été détruit ou anonymisé. Vide = rien à certifier.",
    )
    anonymized_fields = fields.Char(
        string="Champs anonymisés (consentement)",
        readonly=True,
        copy=False,
        help="Champs vidés par l'anonymisation du consentement visé. Repris par "
             "le registre comme « Éléments détruits », sous une portée partielle.",
    )
    # 🔴 La portée partielle promise
    # ne s'écrivait QUE depuis le consentement, dont l'anonymiseur lève
    # toujours. Donc aucune demande n'écrivait jamais de portée partielle :
    # cinq pièces jointes anonymisées donnaient une entrée en portée
    # « entière ». Les champs vidés sur les DOCUMENTS se relèvent maintenant
    # aussi, modèle par modèle.
    document_anonymized_fields = fields.Char(
        string="Champs anonymisés (documents)",
        readonly=True,
        copy=False,
        help="Ce que l'anonymisation a retiré des documents classifiés, par "
             "modèle. Repris par le registre comme « Éléments détruits ».",
    )
    # 🔴 Le certificat nommait « Enregistrement de
    # consentement » dans les catégories détruites dès qu'un consentement était
    # VISÉ, sans égard à ce qui lui était arrivé. Ces deux champs disent ce qui
    # a été fait, et ce qui a été refusé, et le certificat les lit.
    consent_applied = fields.Char(
        string="Effet sur le consentement",
        readonly=True,
        copy=False,
        help="Ce que l'exécution a réellement fait du consentement visé. "
             "Vide = rien, et le certificat ne le compte donc pas comme détruit.",
    )
    consent_refused_reason = fields.Text(
        string="Consentement non détruit, motif",
        readonly=True,
        copy=False,
        help="Pourquoi le consentement visé n'a rien perdu : refus motivé de "
             "son anonymiseur, ou méthode qui ne retire rien (archivage). "
             "Imprimé au certificat, section « NON DÉTRUIT ».",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
        store=True,
    )

    notes = fields.Text(string="Notes")

    # External data destruction tracking
    credentials_destroyed = fields.Integer(
        string="Identifiants détruits",
        default=0,
        help="Nombre d'identifiants de projet effacés de manière sécurisée",
    )
    nextcloud_activity_id = fields.Many2one(
        comodel_name="mail.activity",
        string="Activité de suppression Nextcloud",
        help="Activité créée pour la suppression manuelle du dossier Nextcloud",
    )
    nextcloud_folder_path = fields.Char(
        string="Chemin du dossier Nextcloud",
        help="Chemin vers le dossier client dans Nextcloud pour suppression",
    )

    def _compute_document_count(self):
        for record in self:
            record.document_count = len(record.classification_ids)

    @api.onchange("consent_id")
    def _onchange_consent_id(self):
        """Auto-fill partner from consent."""
        if self.consent_id and self.consent_id.subject_partner_id:
            self.partner_id = self.consent_id.subject_partner_id

    # Valid state transitions (from → allowed to states).
    # Enforced in write() so direct ORM writes cannot skip approval/execution.
    _STATE_TRANSITIONS = {
        "pending": {"approved", "cancelled"},
        "approved": {"executed", "cancelled"},
        "executed": set(),
        "cancelled": set(),
    }

    def write(self, vals):
        """Enforce valid state transitions (Art. 3.2 LPRPSP accountability)."""
        if "state" in vals and not self.env.context.get("skip_state_validation"):
            new_state = vals["state"]
            for record in self:
                allowed = self._STATE_TRANSITIONS.get(record.state, set())
                if new_state != record.state and new_state not in allowed:
                    raise UserError(
                        f"Transition d'état invalide : « {record.state} » → "
                        f"« {new_state} ». Utilisez les actions d'approbation / "
                        f"d'exécution / d'annulation."
                    )
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        """Generate certificate number on creation and auto-fill partner."""
        for vals in vals_list:
            if not vals.get("certificate_number"):
                vals["certificate_number"] = self.env["ir.sequence"].next_by_code(
                    "privacy.destruction.request"
                ) or "DEST-NEW"
            # Auto-fill partner from consent if not provided
            if not vals.get("partner_id") and vals.get("consent_id"):
                consent = self.env["privacy.consent"].browse(vals["consent_id"])
                if consent.exists() and consent.subject_partner_id:
                    vals["partner_id"] = consent.subject_partner_id.id
            # Auto-fill company from consent if not provided
            if not vals.get("company_id") and vals.get("consent_id"):
                consent = self.env["privacy.consent"].browse(vals["consent_id"])
                if consent.exists() and consent.company_id:
                    vals["company_id"] = consent.company_id.id
        return super().create(vals_list)

    def _compute_display_name(self):
        for record in self:
            record.display_name = record.certificate_number or f"Demande #{record.id}"

    def action_approve(self):
        """Approve destruction request (Privacy Manager+ only)."""
        if not self.env.su and not self.env.user.has_group(
            "privacy_consent.group_privacy_manager"
        ):
            raise UserError(
                "Seul un gestionnaire ou responsable de la vie privée peut approuver "
                "une demande de destruction."
            )
        for request in self:
            if request.state != "pending":
                raise UserError("Seules les demandes en attente peuvent être approuvées.")
            # Prevent two approved requests targeting the same classified documents
            if request.classification_ids:
                conflicting = self.search([
                    ("id", "!=", request.id),
                    ("state", "=", "approved"),
                    ("classification_ids", "in", request.classification_ids.ids),
                ], limit=1)
                if conflicting:
                    raise UserError(
                        f"Une demande déjà approuvée ({conflicting.certificate_number}) "
                        f"cible un ou plusieurs de ces documents. "
                        f"Exécutez-la ou annulez-la avant d'approuver celle-ci."
                    )
            request.write({"state": "approved"})
            request.message_post(
                body="Demande de destruction approuvée.",
                message_type="notification",
            )
        return True

    def action_execute(self):
        """Execute destruction according to policy or calendar rule (Privacy Officer+ only)."""
        if not self.env.su and not self.env.user.has_group(
            "privacy_consent.group_privacy_officer"
        ):
            raise UserError(
                "Seul le responsable de la vie privée peut exécuter "
                "une destruction de données."
            )
        for request in self:
            if request.state != "approved":
                raise UserError("Seules les demandes approuvées peuvent être exécutées.")

            # Determine method from policy, calendar, or default
            if request.policy_id:
                method = request.policy_id.destruction_method
            elif request.retention_calendar_id:
                method = request.retention_calendar_id.destruction_method or "delete"
            else:
                method = "anonymize"

            # ⚠ Instantané AVANT l'exécution. Depuis que « Suppression »
            # supprime vraiment, `request.consent_id` rend un
            # recordset vide dès que le consentement a été détruit — et
            # l'entrée de registre, construite APRÈS, perdait en silence le
            # nom et l'identifiant de ce qu'elle certifiait.
            # ⚠ `purpose_id` est un `related` STOCKÉ sur `consent_id` : il
            # retombe à False dès que le lien passe à NULL. La finalité fait
            # donc partie de l'instantané, sinon `pi_categories` sortirait vide
            # de toute destruction réussie — le même piège que
            # `destroyed_classification_ids` a déjà corrigé côté documents.
            consent_snapshot = {
                "id": request.consent_id.id,
                "name": request.consent_id.display_name,
                "pi_categories": request.purpose_id.name or "",
            } if request.consent_id else None

            # 🔴 La garde ci-dessous lit TROIS
            # compteurs STOCKÉS (`applied_effects`, `skipped_count`,
            # `destroyed_count`), et leur `readonly` ne vaut qu'à l'écran : un
            # `destroyed_count` posé par RPC avant l'exécution ferait certifier
            # une demande qui n'a rien touché. On les remet à zéro ici, donc ce
            # que la garde lit ensuite a été écrit par CETTE exécution, et par
            # elle seule. Même raison pour les deux listes de résultat, que le
            # certificat imprime et que le registre lit : seules les CIBLES
            # (`classification_ids`) sont conservées.
            # ⚠ Durcissement, pas une brèche connue : exécuter demande l'état
            # « approuvée » et le groupe du responsable de la vie privée. Mais
            # c'est l'invariant porteur du lot, et un invariant qui repose sur
            # un champ inscriptible ne tient que par convention.
            request.write({
                "execution_log": False,
                "applied_effects": False,
                "anonymized_fields": False,
                "document_anonymized_fields": False,
                "consent_applied": False,
                "consent_refused_reason": False,
                "destroyed_count": 0,
                "skipped_count": 0,
                "destroyed_classification_ids": [(5, 0, 0)],
                "skipped_classification_ids": [(5, 0, 0)],
            })

            request._execute_destruction(method)

            # 🔴 Rien détruit, rien certifié. Le cas mesuré : un « droit
            # à l'oubli » sur une fiche sans classification et sans consentement
            # ne touchait aucun champ de la fiche, et rendait quand même un
            # certificat intitulé « CERTIFICAT DE DESTRUCTION » plus une entrée de
            # registre scellée. La levée annule aussi ce qui aurait été fait à
            # moitié : ou bien la demande a un effet ET une pièce, ou bien ni
            # l'un ni l'autre.
            # ⚠ « Manuelle » et « Archivage » sont exemptées : elles ne
            # prétendent rien détruire. La première programme une revue
            # humaine, la seconde met de côté en conservant tout. Elles
            # n'écrivent plus RIEN au registre (règle retenue du
            # 2026-11-15, voir `_privacy_removed_something`), donc leur
            # exécution ne peut plus rien attester de faux ; la refuser
            # bloquerait une mise de côté parfaitement légitime, jour après
            # jour, par le cron.
            # ⚠ Et un document SAUTÉ n'est pas « rien » : depuis la garde du socle le
            # certificat porte une section « Documents NON détruits » qui le dit,
            # et la demande garde son journal. Ce qu'on refuse, c'est la demande
            # qui n'a RIEN tenté : ni document ciblé, ni consentement, ni
            # identifiant. C'est le cas mesuré du « droit à l'oubli » sur une
            # fiche sans classification.
            # 🔴 `destroyed_count` fait partie du test depuis l'origine : un
            # document dont la CIBLE avait déjà disparu entre
            # dans les détruits (branche « DÉJÀ ABSENT ») sans entrer dans les
            # effets, et sans être sauté. La demande n'avait donc ni effet ni
            # saut, la garde levait, et le `rollback` de la levée annulait une
            # destruction qui venait de réussir, le cas d'un pont qui
            # supprime vraiment avant de relayer à `super()`, ou d'une cible
            # supprimée à la main avant l'exécution. Le droit n'était jamais
            # honoré et le cron rejouait l'échec chaque jour. Un document
            # ciblé dont la cible avait disparu EST une tentative.
            if (method not in ("manual", "archive")
                    and not request.applied_effects
                    and not request.skipped_count
                    and not request.destroyed_count):
                raise UserError(_(
                    "La demande %(num)s n'a rien détruit et n'a rien tenté : "
                    "aucun document ciblé, aucun consentement, aucun "
                    "identifiant. Rien ne sera certifié et rien ne sera inscrit "
                    "au registre, parce qu'un certificat de destruction qui ne "
                    "détruit rien est faux et qu'il ne se corrige "
                    "pas.\n\nJournal d'exécution :\n%(journal)s",
                    num=request.certificate_number or request.id,
                    journal=request.execution_log or _("(vide)"),
                ))

            # ⚠ L'ORDRE COMPTE. Le certificat était scellé AVANT ce write, donc il
            # tombait sur `executed_at` vide et `destruction_method_used` vide, et
            # son empreinte portait sur une chaîne où figuraient des valeurs de
            # repli. On fige l'état d'abord, on certifie ensuite.
            request.write({
                "state": "executed",
                "executed_at": fields.Datetime.now(),
                "executed_by_id": self.env.user.id,
                "destruction_method_used": method,
            })

            # Generate certificate
            request._generate_certificate()

            # 🔴 Règle retenue du 2026-11-15 : une exécution qui
            # n'a RIEN retiré n'écrit RIEN au registre. Trois chemins
            # écrivaient encore une entrée sans avoir rien retiré :
            # « Archivage », « Manuelle », et la demande en « Anonymisation »
            # dont TOUS les documents ont été refusés (fiche porteuse d'une
            # pièce comptable ou d'un compte usager, donc les deux refus que ce
            # lot ajoute). La dernière passait la garde ci-dessus par son
            # `skipped_count`, n'avait touché à rien, et scellait une entrée en
            # portée « entière » : sur un
            # chemin que ce lot a créé.
            # Un registre dont certaines entrées n'attestent aucune destruction
            # perd la propriété qui le rend probant, « une entrée, une
            # destruction ». La trace reste au journal d'exécution, au fil de la
            # demande et à l'activité de revue, qui sont auditables ET
            # corrigeables, contrairement à la table scellée.
            if request._privacy_removed_something():
                self.env["privacy.destruction.register"].create_from_destruction_request(
                    request, consent_snapshot=consent_snapshot,
                )
            else:
                request._privacy_log(_(
                    "REGISTRE : aucune entrée. Cette exécution n'a rien "
                    "retiré. La trace reste ici, au fil de la demande et à "
                    "l'activité de revue, qui se corrigent ; le registre, "
                    "lui, ne se corrige pas."))
                request.message_post(
                    body=_(
                        "Aucune entrée au registre de destruction : cette "
                        "exécution n'a RIEN retiré. Le journal d'exécution "
                        "dit ce qui a été fait, et il se corrige ; une entrée "
                        "de registre, non."),
                    message_type="notification",
                )

            request.message_post(
                body=f"Destruction exécutée avec la méthode : {method}",
                message_type="notification",
            )
        return True

    def _execute_destruction(self, method):
        """Execute the actual destruction based on method.

        For consent-type requests:
        1. Consent record handling (anonymize/archive)
        2. Credential destruction (secure wipe)
        3. Nextcloud activity creation (for manual deletion)

        For document-type requests:
        Delegates to _execute_document_destruction().

        ⚠ ``erasure_right`` DOIT passer par la destruction documentaire. Jusqu'en
        18.0.4.9.0 ce type n'était pas dans la liste de routage ci-dessous, alors
        que le gabarit du certificat imprimait, lui, le tableau « Documents
        détruits » POUR ce type (report/privacy_destruction_certificate.xml). Une
        demande d'effacement laissait donc les pièces jointes intactes et
        certifiait par écrit leur destruction. Les deux listes doivent rester
        identiques : toute modification ici se reporte au gabarit.
        """
        self.ensure_one()

        # Destruction documentaire. Pour une demande d'effacement, elle ne suffit
        # pas : le droit à l'effacement porte sur la PERSONNE, donc on enchaîne
        # ensuite sur le volet consentement ci-dessous.
        if self.classification_ids and self.request_type in (
            "document", "campaign", "erasure_right",
        ):
            self._execute_document_destruction(method)
            if self.request_type != "erasure_right":
                return

        consent = self.consent_id
        partner = self.partner_id

        # 1. Handle consent record
        if consent:
            if method == "anonymize":
                # 🔴 Cette branche écrivait elle-même « [ANONYMISÉ] »
                # dans les notes et archivait le consentement. Or
                # `subject_partner_id` est OBLIGATOIRE : la personne restait
                # désignée par une clé étrangère vivante, et le registre scellait
                # pourtant une anonymisation. Mesuré sur une base réelle, plusieurs
                # attestations dans ce cas.
                #
                # Le consentement déclare maintenant son refus motivé
                # (`_privacy_anonymize`), et on CONSIGNE ce refus au lieu
                # d'abandonner la demande entière : les documents anonymisés plus
                # haut restent acquis, et la garde d'`action_execute` refusera de
                # certifier s'il ne s'est rien passé du tout.
                try:
                    with self.env.cr.savepoint():
                        applied = destruction_ops.destroy_record(consent, "anonymize")
                except UserError as exc:
                    # ⚠ Le motif est AUSSI gardé dans un champ : le certificat
                    # n'imprime pas le journal, et sans ce champ il taisait un
                    # refus qu'il comptait pourtant parmi les catégories
                    # détruites.
                    self.consent_refused_reason = str(exc)
                    self._privacy_log(_("CONSENTEMENT NON anonymisé — %s", exc))
                else:
                    self.anonymized_fields = applied.fields
                    self.consent_applied = applied.label
                    self._privacy_log(
                        _("CONSENTEMENT %(nom)s : %(fait)s",
                          nom=consent.display_name, fait=applied.label),
                        effet=True,
                    )

            elif method in ("delete", "secure_wipe"):
                # 🔴 Troisième copie du même défaut, sur l'objet le plus
                # sensible du module : `privacy.consent` porte un champ `active`,
                # donc « Suppression » l'archivait — preuve, notes et pièces
                # comprises — pendant que le registre certifiait sa destruction.
                #
                # ⚠ Les preuves partent D'ABORD. Elles pointent le consentement
                # par un Many2one `ondelete="cascade"` : l'unlink du parent les
                # emporterait sans effacer les fichiers du filestore, qui
                # survivraient à la « destruction » sans que rien ne le dise.
                if hasattr(consent, "evidence_ids") and consent.evidence_ids:
                    for evidence in consent.evidence_ids:
                        destruction_ops.destroy_record(evidence, "delete")
                # ⚠ Le nom se lit AVANT l'unlink : après, le recordset est vide.
                nom_consentement = consent.display_name
                applied = destruction_ops.destroy_record(consent, method)
                self.consent_applied = applied.label
                self._privacy_log(
                    _("CONSENTEMENT %(nom)s : %(fait)s",
                      nom=nom_consentement, fait=applied.label),
                    effet=True,
                )

            elif method == "archive":
                # Par la primitive, comme les autres : elle archive et nomme ce
                # qu'elle a fait, sans prétendre détruire. ⚠ `consent_applied`
                # reste VIDE : l'archivage ne retire rien, donc le certificat
                # ne doit pas compter le consentement parmi les catégories
                # détruites. Il le dira sous « NON DÉTRUIT », avec le motif.
                # 🔴 Et le journal ne consigne PAS d'effet réel : cette branche était le TROISIÈME écrivain du
                # registre, et le seul qui ne lisait pas `removed`. Elle
                # remplissait `applied_effects`, donc la garde de
                # certification passait et une entrée immuable se scellait
                # pour une opération dont la primitive dit qu'elle n'a RIEN
                # retiré. Sans effet, `_privacy_removed_something()` est faux
                # et le registre ne reçoit rien ; la garde de certification
                # exempte « Archivage », qui ne prétend rien détruire.
                applied = destruction_ops.destroy_record(consent, "archive")
                self.consent_refused_reason = _(
                    "Méthode « Archivage » : le consentement est mis de côté, "
                    "rien n'a été retiré.")
                self._privacy_log(
                    _("CONSENTEMENT %(nom)s : %(fait)s",
                      nom=consent.display_name, fait=applied.label),
                )

        # 2. Destroy credentials (if project_knowledge_matrix is installed)
        credentials_count = self._destroy_partner_credentials(partner)
        self.credentials_destroyed = credentials_count
        if credentials_count:
            self._privacy_log(
                _("IDENTIFIANTS effacés : %s", credentials_count), effet=True)

        # 3. Create Nextcloud deletion activity
        self._create_nextcloud_deletion_activity(partner)

        # 4. Manual method - create additional review activity
        if method == "manual":
            self.activity_schedule(
                "mail.mail_activity_data_todo",
                note=f"Révision manuelle de destruction requise pour {partner.name}",
                user_id=self.env.user.id,
            )

    def _privacy_removed_something(self):
        """Cette exécution a-t-elle VRAIMENT retiré quelque chose ?

        🔴 **Règle retenue.** C'est la condition pour
        écrire au registre immuable. Deux sources, et deux seulement :

        * ``applied_effects``, que seuls les gestes qui ont retiré quelque
          chose alimentent (``_privacy_log(..., effet=True)``) : un document
          anonymisé ou supprimé, un consentement supprimé ou anonymisé, des
          identifiants effacés. « Archivage » et « Manuelle » n'y entrent pas,
          la primitive déclarant ``removed=False`` ;
        * ``destroyed_count``, pour le seul cas où la cible a disparu AVANT
          l'exécution (branche « DÉJÀ ABSENT »). L'enregistrement n'existe
          plus, l'obligation est honorée, et la raison dite plus haut dit pourquoi refuser cela annulerait une destruction
          réussie. L'entrée atteste alors une absence vraie.

        ⚠ Ce qui continue d'écrire : une demande qui a détruit ne serait-ce
        qu'un document, même si d'autres ont été sautés. Le certificat nomme
        l'échec dans sa section « Documents NON détruits » depuis la garde du socle, et
        une destruction partielle est une destruction.
        """
        self.ensure_one()
        return bool(self.applied_effects) or bool(self.destroyed_count)

    def _privacy_log(self, ligne, effet=False):
        """Ajoute une ligne au journal d'exécution, et au besoin aux effets réels.

        ⚠ Lecture, modification, écriture : le volet consentement s'exécute
        APRÈS le volet documentaire, qui a déjà écrit son journal. Un `write`
        sec effacerait ce qu'il vient d'y mettre.
        """
        self.ensure_one()
        vals = {"execution_log": "\n".join(
            p for p in (self.execution_log, ligne) if p)}
        if effet:
            vals["applied_effects"] = "\n".join(
                p for p in (self.applied_effects, ligne) if p)
        self.write(vals)

    def _execute_document_destruction(self, method):
        """Execute destruction for classified documents.

        Iterates over classification_ids and destroys/anonymizes each linked
        document record based on the chosen method.

        Validates that the target model allows write access before using sudo()
        to perform the actual destruction.

        ⚠ Chaque sortie de boucle est CONSIGNÉE, pas seulement journalisée côté
        serveur. Un document sauté (modèle absent, droits insuffisants) laissait
        auparavant sa classification active, et le certificat — qui lisait
        ``classification_ids`` — le présentait comme détruit. Le résultat réel est
        désormais figé dans ``destroyed_classification_ids`` /
        ``skipped_classification_ids``, seule source du certificat et du registre.
        """
        self.ensure_one()
        # Validate method
        valid_methods = {"anonymize", "delete", "secure_wipe", "archive", "manual"}
        if method not in valid_methods:
            raise UserError(f"Méthode de destruction invalide : {method}")

        destroyed = self.env["privacy.document.classification"]
        skipped = self.env["privacy.document.classification"]
        log_lines = []
        # 🔴 Ce qui a VRAIMENT été détruit, pour la garde de
        # certification. Un document « déjà absent » n'y entre pas : il n'y a
        # rien à certifier d'une destruction que cette demande n'a pas faite.
        effets = []
        # 🔴 Les champs vidés, par modèle, pour que l'entrée de
        # registre déclare la portée partielle promise. Sans eux,
        # cinq pièces jointes anonymisées s'inscrivaient en portée
        # « entière », dont l'aide dit « affirme que l'enregistrement visé
        # n'existe plus ».
        champs_par_modele = {}

        def _skip(classification, reason):
            nonlocal skipped
            skipped |= classification
            log_lines.append(
                f"SAUTÉ — {classification.display_name or classification.id} "
                f"({classification.res_model or 'modèle absent'},"
                f"{classification.res_id or '-'}) : {reason}"
            )

        for classification in self.classification_ids:
            if not classification.res_model or not classification.res_id:
                _skip(classification, "aucun enregistrement cible renseigné")
                continue
            try:
                record = self.env[classification.res_model].browse(classification.res_id)
            except KeyError:
                _logger.warning(
                    "Model %s not found during destruction", classification.res_model
                )
                _skip(classification, f"modèle {classification.res_model} absent du registre")
                continue
            if not record.exists():
                _logger.info(
                    "Record %s,%s already deleted", classification.res_model, classification.res_id
                )
                classification.write({"active": False})
                destroyed |= classification
                log_lines.append(
                    f"DÉJÀ ABSENT — {classification.display_name or classification.id} "
                    f"({classification.res_model},{classification.res_id}) : "
                    f"enregistrement déjà supprimé, classification archivée"
                )
                continue

            # Verify current user would have write access to the target model
            # before escalating to sudo. This prevents privilege escalation
            # through the destruction workflow.
            try:
                record.check_access_rights("write")
                record.check_access_rule("write")
            except Exception:
                _logger.warning(
                    "User %s lacks write access to %s,%s — skipping destruction",
                    self.env.user.login, classification.res_model, classification.res_id,
                )
                _skip(
                    classification,
                    f"droits d'écriture insuffisants pour {self.env.user.login}",
                )
                continue

            # 🔴 Ce bloc portait le même défaut, dans sa seconde
            # copie : `delete` et `secure_wipe` archivaient dès que le modèle
            # avait un champ `active`, et le certificat annonçait « détruit ».
            # La primitive partagée supprime vraiment et contrôle après coup.
            #
            # ⚠ Point de sauvegarde par document : un `unlink` retenu par une
            # clé étrangère lève une `IntegrityError` qui AVORTE la transaction.
            # Sans lui, la première pièce récalcitrante emportait la boucle
            # entière — y compris l'écriture du journal d'exécution, qui est
            # justement ce qui devait dire pourquoi.
            try:
                with self.env.cr.savepoint():
                    applied = destruction_ops.destroy_record(record, method)
            except Exception as exc:  # noqa: BLE001 — consigné, puis on continue
                _logger.warning(
                    "Destruction de %s,%s en échec : %s",
                    classification.res_model, classification.res_id, exc,
                )
                _skip(classification, f"destruction en échec : {exc}")
                continue

            # 🔴 Rien détruit, rien certifié ici aussi, et pas
            # seulement à la certification de la demande. `archive` et `manual`
            # ne retirent RIEN : les compter parmi les détruits archivait la
            # classification, qui devenait invisible aux campagnes suivantes,
            # et le journal écrivait « DÉTRUIT (aucune opération
            # automatique) ». Le document tombe donc parmi les NON détruits,
            # avec le motif, et sa classification reste active.
            if not applied.removed:
                _skip(classification, "%s : rien n'a été retiré, donc rien "
                                      "n'est certifié" % applied.label)
                continue

            # 🔴 Le nom se relit APRÈS l'anonymisation. La
            # classification fige `res_name` au classement, et le certificat
            # comme les vues le reprennent : sur une fiche anonymisée, le nom
            # d'avant survivait à son propre effacement.
            if applied.scope == "partial":
                destruction_ops.refresh_res_name(record, classification)
                champs_par_modele.setdefault(classification.res_model, set()).update(
                    c.strip() for c in (applied.fields or "").split(",") if c.strip()
                )

            # Deactivate classification
            classification.write({"active": False})
            destroyed |= classification
            ligne = (
                f"DÉTRUIT ({applied.label}) — "
                f"{classification.display_name or classification.id} "
                f"({classification.res_model},{classification.res_id})"
            )
            log_lines.append(ligne)
            effets.append(ligne)

        self.write({
            "destroyed_classification_ids": [(6, 0, destroyed.ids)],
            "skipped_classification_ids": [(6, 0, skipped.ids)],
            "destroyed_count": len(destroyed),
            "skipped_count": len(skipped),
            "execution_log": "\n".join(log_lines) or "Aucun document ciblé.",
            "applied_effects": "\n".join(effets),
            "document_anonymized_fields": "; ".join(
                "%s : %s" % (modele, ", ".join(sorted(champs)))
                for modele, champs in sorted(champs_par_modele.items()) if champs
            ),
        })

        # Un échec partiel ne doit pas se lire seulement dans le journal du serveur :
        # il remonte au fil de la demande, où le responsable de la vie privée le voit.
        if skipped:
            self.message_post(
                body=(
                    f"⚠ Destruction partielle : {len(destroyed)} document(s) détruit(s), "
                    f"{len(skipped)} non détruit(s). Voir le journal d'exécution."
                ),
                message_type="notification",
            )

        # Volet « personne » : identifiants + activité Nextcloud.
        # ⚠ Sauté pour `erasure_right` : _execute_destruction enchaîne sur le volet
        # consentement, qui refait exactement ces deux gestes. Sans cette garde, une
        # demande d'effacement compterait ses identifiants deux fois et créerait deux
        # activités de suppression Nextcloud pour le même dossier.
        if self.partner_id and self.request_type != "erasure_right":
            credentials_count = self._destroy_partner_credentials(self.partner_id)
            self.credentials_destroyed = credentials_count
            self._create_nextcloud_deletion_activity(self.partner_id)

    def _destroy_partner_credentials(self, partner):
        """Securely destroy all credentials linked to partner's projects.

        This performs a cryptographic wipe of sensitive data:
        - Overwrites encrypted passwords with random data
        - Overwrites API keys with random data
        - Removes key files
        - Marks credentials as destroyed

        Returns the number of credentials destroyed.
        """
        if not partner:
            return 0

        # Check if project_knowledge_matrix is installed
        if 'project.credential' not in self.env:
            _logger.info(
                "project_knowledge_matrix not installed, skipping credential destruction"
            )
            return 0

        Credential = self.env['project.credential']
        Project = self.env['project.project']

        # Find all projects for this partner
        projects = Project.search([('partner_id', '=', partner.id)])
        if not projects:
            _logger.info("No projects found for partner %s", partner.name)
            return 0

        # Find all credentials for these projects
        credentials = Credential.search([('project_id', 'in', projects.ids)])
        if not credentials:
            _logger.info(
                "No credentials found for partner %s projects", partner.name
            )
            return 0

        count = len(credentials)
        _logger.info(
            "Destroying %d credentials for partner %s", count, partner.name
        )

        # Secure destruction: overwrite with junk before deletion
        # This prevents forensic recovery of the encrypted data
        import secrets
        junk_data = secrets.token_hex(64)  # Random 128-char string

        for cred in credentials:
            # Log the destruction (without sensitive data)
            cred.message_post(
                body=(
                    f"Identifiant détruit selon la demande de destruction "
                    f"{self.certificate_number}. "
                    f"Raison : Demande d'effacement du sujet de données (Loi 25 / RGPD)."
                ),
                message_type="notification",
            )

            # Overwrite sensitive fields with junk data
            cred.sudo().write({
                'password_encrypted': junk_data,
                'api_key_encrypted': junk_data,
                'key_file': False,
                'key_filename': False,
                'url': '[DÉTRUIT]',
                'username': '[DÉTRUIT]',
                'domain': '[DÉTRUIT]',
                'notes': f'[DÉTRUIT le {fields.Date.today()} - Réf : {self.certificate_number}]',
                'state': 'revoked',
            })

        # Now delete the credentials entirely
        credentials.sudo().unlink()

        _logger.info(
            "Successfully destroyed %d credentials for partner %s",
            count, partner.name
        )
        return count

    def _create_nextcloud_deletion_activity(self, partner):
        """Create an activity for manual Nextcloud folder deletion.

        Nextcloud data must be deleted manually since it's an external system.
        This creates a tracked activity assigned to the responsible user.
        """
        if not partner:
            return

        # Determine the Nextcloud folder path (convention-based)
        folder_path = f"/Clients/{partner.name}"
        self.nextcloud_folder_path = folder_path

        # Find responsible user (DPO or fallback to current user)
        responsible_user = self._get_dpo_user() or self.env.user

        # Create the activity
        activity_type = self.env.ref(
            'mail.mail_activity_data_todo',
            raise_if_not_found=False
        )
        if not activity_type:
            activity_type = self.env['mail.activity.type'].search(
                [('name', 'ilike', 'todo')], limit=1
            )

        activity_vals = {
            'activity_type_id': activity_type.id if activity_type else False,
            'summary': f"Supprimer le dossier Nextcloud / Delete Nextcloud folder",
            'note': f"""
<p><strong>Action requise / Action Required:</strong></p>
<p>Veuillez supprimer le dossier client dans Nextcloud suite à une demande de destruction de données.</p>
<p>Please delete the client folder in Nextcloud following a data destruction request.</p>

<ul>
    <li><strong>Client:</strong> {partner.name}</li>
    <li><strong>Chemin / Path:</strong> <code>{folder_path}</code></li>
    <li><strong>Certificat / Certificate:</strong> {self.certificate_number}</li>
</ul>

<p><strong>Étapes / Steps:</strong></p>
<ol>
    <li>Connectez-vous à Nextcloud / Log in to Nextcloud</li>
    <li>Naviguez vers / Navigate to: <code>{folder_path}</code></li>
    <li>Vérifiez le contenu / Verify contents</li>
    <li>Supprimez le dossier / Delete the folder</li>
    <li>Videz la corbeille / Empty the trash</li>
    <li>Marquez cette activité comme terminée / Mark this activity as done</li>
</ol>
""",
            'user_id': responsible_user.id,
            'res_model_id': self.env['ir.model']._get('privacy.destruction.request').id,
            'res_id': self.id,
            'date_deadline': fields.Date.today(),
        }

        activity = self.env['mail.activity'].create(activity_vals)
        self.nextcloud_activity_id = activity.id

        _logger.info(
            "Created Nextcloud deletion activity for partner %s, folder: %s",
            partner.name, folder_path
        )

    def _get_dpo_user(self):
        """Get the Data Protection Officer user if configured."""
        # Try to find a user with DPO in their name or a specific group
        dpo_group = self.env.ref(
            'privacy_consent.group_privacy_officer',
            raise_if_not_found=False
        )
        if dpo_group:
            dpo_users = self.env['res.users'].search([
                ('groups_id', 'in', [dpo_group.id]),
                ('active', '=', True),
            ], limit=1)
            if dpo_users:
                return dpo_users[0]
        return False

    def _generate_certificate(self):
        """Generate destruction certificate.

        ⚠ La chaîne certifiée est PERSISTÉE. Sans elle, l'empreinte SHA-256
        affichée au certificat n'était vérifiable par personne : le contenu
        embarquait ``fields.Datetime.now()``, donc deux appels sur le même
        enregistrement rendaient deux empreintes différentes. Le certificat
        promettait un contrôle d'intégrité impossible à jouer.
        """
        self.ensure_one()

        # Build certificate content
        cert_content = self._build_certificate_content()

        # Generate hash for integrity
        verification_hash = hashlib.sha256(cert_content.encode()).hexdigest()

        self.write({
            "certificate_content": cert_content,
            "verification_hash": verification_hash,
        })

    def verify_certificate_integrity(self):
        """Rejouer l'empreinte du certificat contre le contenu certifié.

        Rend True si l'empreinte stockée correspond au contenu stocké, False si
        elle a divergé, et None quand la demande est antérieure à la persistance
        du contenu (auquel cas il n'y a rien à rejouer — ne pas le lire comme un
        échec d'intégrité).
        """
        self.ensure_one()
        if not self.verification_hash or not self.certificate_content:
            return None
        recomputed = hashlib.sha256(self.certificate_content.encode()).hexdigest()
        return recomputed == self.verification_hash

    def _privacy_certificate_categories(self):
        """La liste des catégories atteintes, UNE seule fois pour deux sorties.

        🔴 Le correctif C2 n'atteignait pas le
        document livré : `certificate_content` n'est affiché par aucune vue et
        imprimé par aucun rapport, donc la catégorie corrigée et la section
        « NON DÉTRUIT » ne vivaient que dans la chaîne HACHÉE. Le PDF, lui,
        imprimait « Référence du consentement » puis « Méthode :
        Anonymisation », sans un mot du refus. La seule atténuation était
        `execution_log`, imprimé en bas de page.

        ⚠ La liste vit ICI, et non recopiée dans le gabarit QWeb : deux sources
        pour la même affirmation divergent à la première correction, et c'est
        exactement ce que ce constat décrit. Le texte haché et le PDF lisent donc la
        même méthode.
        """
        self.ensure_one()
        # ⚠ Cette liste était STATIQUE : elle certifiait la destruction
        # d'identifiants de projet et de fichiers Nextcloud même quand rien de tel
        # n'avait été touché — et sur cette instance `project.credential` n'existe
        # pas, donc la ligne était fausse à 100 %. On n'énumère plus que ce qui a
        # effectivement été atteint.
        categories = []
        # 🔴 La catégorie se lit sur l'EFFET, pas sur la cible. Un
        # consentement VISÉ dont l'anonymisation a été refusée reste actif,
        # notes intactes : le certificat ne peut pas le compter parmi les
        # données détruites. `consent_applied` n'est renseigné que par une
        # opération qui a vraiment retiré quelque chose, donc il vaut aussi pour
        # une suppression réussie, après laquelle `consent_id` est vide.
        if self.consent_applied:
            categories.append(
                "- Enregistrement de consentement / Consent record"
            )
        if self.destroyed_count:
            categories.append(
                f"- Documents classifiés ({self.destroyed_count}) / "
                f"Classified documents ({self.destroyed_count})"
            )
        if self.credentials_destroyed:
            categories.append(
                f"- Identifiants de projet ({self.credentials_destroyed}) : mots de passe, "
                f"clés API, fichiers de clés / Project credentials "
                f"({self.credentials_destroyed})"
            )
        if self.nextcloud_activity_id:
            categories.append(
                "- Fichiers Nextcloud : SUPPRESSION MANUELLE EN ATTENTE, non encore "
                "effectuée / Nextcloud files: MANUAL DELETION PENDING, not yet performed"
            )
        if not categories:
            categories.append(
                "- Aucune catégorie de données n'a été atteinte par cette exécution. / "
                "No data category was reached by this execution."
            )
        # Le stockage objet n'est relié à aucun chemin de destruction : `privacy_consent`
        # ne dépend pas de `bf_securetransfer` et n'appelle jamais `_purge_s3()`.
        # Tant que la portée de la purge n'est pas tranchée, le certificat le DIT
        # au lieu de laisser croire le contraire par omission.
        categories.append(
            "- Stockage objet (transferts sécurisés S3) : NON TOUCHÉ par cette "
            "destruction. / Object storage (secure transfers, S3): NOT AFFECTED."
        )
        return categories

    def _build_certificate_content(self):
        """Build certificate content for hash and PDF."""
        self.ensure_one()

        # Build consent section if applicable
        consent_section = ""
        if self.consent_id:
            consent_section = f"""
DÉTAILS DU CONSENTEMENT / CONSENT DETAILS
------------------------------------------
Référence du consentement / Consent Reference: {self.consent_id.display_name}
Statut original / Original Status: {self.consent_id.status}
Finalité / Purpose: {self.purpose_id.name or 'N/A'}
"""

        # Build policy section if applicable
        policy_section = ""
        if self.policy_id:
            policy_section = f"""
POLITIQUE DE RÉTENTION / RETENTION POLICY
------------------------------------------
Politique / Policy: {self.policy_id.display_name}
Jours de rétention / Retention Days: {self.policy_id.retention_days}
Déclencheur / Trigger: {self.policy_id.trigger_on}
"""

        # Build external data section
        external_data_section = f"""
DESTRUCTION DES DONNÉES EXTERNES / EXTERNAL DATA DESTRUCTION
--------------------------------------------------------------
Identifiants détruits / Credentials Destroyed: {self.credentials_destroyed}
Dossier Nextcloud / Nextcloud Folder: {self.nextcloud_folder_path or 'N/A'}
Activité Nextcloud créée / Nextcloud Activity Created: {'Oui / Yes' if self.nextcloud_activity_id else 'Non / No'}
"""

        # Résultat mesuré de l'exécution documentaire.
        outcome_section = ""
        if self.destroyed_count or self.skipped_count:
            outcome_section = f"""
RÉSULTAT DE L'EXÉCUTION / EXECUTION OUTCOME
---------------------------------------------
Documents détruits / Documents destroyed: {self.destroyed_count}
Documents NON détruits / Documents NOT destroyed: {self.skipped_count}
"""
            if self.skipped_count:
                outcome_section += (
                    "⚠ Destruction PARTIELLE — voir le journal d'exécution.\n"
                    "⚠ PARTIAL destruction — see execution log.\n"
                )

        # 🔴 Le certificat DIT ce qui a été refusé. Il n'imprime ni
        # le journal d'exécution ni les effets réels, donc la ligne
        # « CONSENTEMENT NON anonymisé » ne paraissait nulle part, et le
        # responsable lisait une attestation muette sur le seul enregistrement
        # qui n'avait pas bougé.
        if self.consent_id and not self.consent_applied:
            outcome_section += f"""
NON DÉTRUIT / NOT DESTROYED
-----------------------------
Enregistrement de consentement / Consent record: {self.consent_id.display_name}
État / State: intact, aucun élément retiré / intact, nothing removed
Motif / Reason: {self.consent_refused_reason or 'voir le journal, see execution log'}
"""

        categories_section = "\n".join(
            self._privacy_certificate_categories()) + "\n"

        content = f"""
CERTIFICAT DE DESTRUCTION / DESTRUCTION CERTIFICATE
=====================================================
Numéro de certificat / Certificate Number: {self.certificate_number}
Date: {fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

SUJET / SUBJECT
----------------
Partenaire / Partner: {self.partner_id.name or 'N/A'}
{consent_section}
DÉTAILS DE LA DESTRUCTION / DESTRUCTION DETAILS
-------------------------------------------------
Méthode / Method: {self.destruction_method_used or (self.policy_id.destruction_method if self.policy_id else 'manual')}
Exécuté par / Executed By: {self.executed_by_id.name or self.env.user.name}
Exécuté le / Executed At: {self.executed_at or 'non exécuté / not executed'}
Date de déclenchement / Trigger Date: {self.trigger_date}
Date prévue / Scheduled Date: {self.scheduled_date}
{external_data_section}{outcome_section}
{policy_section}
CATÉGORIES DE DONNÉES DÉTRUITES / DATA CATEGORIES DESTROYED
-------------------------------------------------------------
{categories_section}
BASE LÉGALE / LEGAL BASIS
---------------------------
Cette destruction a été effectuée conformément à :
- La Loi 25 du Québec (Loi modernisant des dispositions législatives en
  matière de protection des renseignements personnels)
- Le droit à l'effacement / droit à l'oubli
- Les exigences de la politique de rétention des données

This destruction was performed in accordance with:
- Quebec Law 25
- Right to erasure / Right to be forgotten
- Data retention policy requirements

Ce certificat confirme que les données personnelles ont été traitées
conformément aux lois et règlements applicables en matière de vie privée.

This certificate confirms that personal data has been processed
in accordance with applicable privacy laws and regulations.
"""
        return content

    def action_cancel(self):
        """Cancel destruction request."""
        for request in self:
            if request.state == "executed":
                raise UserError("Impossible d'annuler une demande exécutée.")
            request.write({"state": "cancelled"})
            request.message_post(
                body="Demande de destruction annulée.",
                message_type="notification",
            )
        return True

    def action_view_certificate(self):
        """View/Download destruction certificate via QWeb report."""
        self.ensure_one()
        if self.state != "executed":
            raise UserError("Le certificat n'est disponible qu'après exécution de la destruction.")
        return self.env.ref(
            "privacy_consent.action_report_destruction_certificate"
        ).report_action(self)

    @api.model
    def create_partner_destruction_request(self, partner_id, reason=None):
        """Create a destruction request for all partner data.

        This is used when a partner exercises their right to erasure (right to be forgotten).
        It creates a single destruction request that will:
        - Destroy all credentials linked to their projects
        - Create activity for Nextcloud folder deletion
        - Archive/anonymize any consent records

        Args:
            partner_id: ID of the partner requesting data erasure
            reason: Optional reason for the request

        Returns:
            The created destruction request record
        """
        partner = self.env['res.partner'].browse(partner_id)
        if not partner.exists():
            raise UserError("Contact introuvable.")

        # Check if there's already a pending/approved request for this partner
        existing = self.search([
            ('partner_id', '=', partner_id),
            ('state', 'in', ['pending', 'approved']),
        ], limit=1)
        if existing:
            raise UserError(
                f"Une demande de destruction existe déjà pour {partner.name} : "
                f"{existing.certificate_number}"
            )

        # Find or create a generic retention policy
        policy = self.env['privacy.retention.policy'].search([
            ('destruction_method', '=', 'anonymize'),
        ], limit=1)

        vals = {
            'request_type': 'erasure_right',
            'partner_id': partner_id,
            'trigger_date': fields.Datetime.now(),
            'scheduled_date': fields.Date.today(),
            'notes': reason or f"Demande de droit à l'effacement de {partner.name}",
        }

        if policy:
            vals['policy_id'] = policy.id

        # Attach all classified documents for this partner (scoped to company)
        classifications = self.env['privacy.document.classification'].search([
            ('subject_partner_id', '=', partner_id),
            ('active', '=', True),
            ('company_id', 'in', [self.env.company.id, False]),
        ])
        if classifications:
            vals['classification_ids'] = [(6, 0, classifications.ids)]

        # Link to any consent if exists
        consent = self.env['privacy.consent'].search([
            ('subject_partner_id', '=', partner_id),
        ], limit=1)
        if consent:
            vals['consent_id'] = consent.id

        request = self.create(vals)
        default_reason = "Demande de droit à l'effacement"
        request.message_post(
            body=f"Demande de destruction créée pour le contact {partner.name}. "
                 f"Raison : {reason or default_reason}",
            message_type="notification",
        )

        _logger.info(
            "Created destruction request %s for partner %s",
            request.certificate_number, partner.name
        )
        return request

    @api.model
    def cron_process_scheduled_destructions(self):
        """Execute destruction requests already approved by a Privacy Officer.

        Auto-approval was removed in v18.0.3.1.0: Loi 25 accountability requires
        an explicit officer decision before any destruction. Pending requests
        past their scheduled_date now trigger an activity for manual review.
        """
        today = fields.Date.today()

        # Flag pending requests past their scheduled date (manual review required)
        pending = self.search([
            ("state", "=", "pending"),
            ("scheduled_date", "<=", today),
        ])
        for request in pending:
            # Avoid duplicate activities
            existing = request.activity_ids.filtered(
                lambda a: a.activity_type_id
                and a.activity_type_id.name == "To-Do"
            )
            if existing:
                continue
            request.activity_schedule(
                "mail.mail_activity_data_todo",
                summary="Demande de destruction en attente d'approbation",
                note=(
                    f"La demande {request.certificate_number} a atteint sa date "
                    f"prévue et requiert une approbation manuelle (Loi 25)."
                ),
            )

        # Execute already-approved requests (officer gate still applies inside)
        approved = self.search([
            ("state", "=", "approved"),
            ("scheduled_date", "<=", today),
        ])
        for request in approved:
            # ⚠ Point de sauvegarde PAR DEMANDE. Le cron n'a aucun commit : tout se
            # joue dans une seule transaction. Sans ce savepoint, une exécution qui
            # échoue à mi-chemin restait VALIDÉE en base (destruction à moitié
            # faite, demande toujours « approved », aucun certificat). Et un
            # `rollback()` nu aurait été pire : il aurait aussi annulé les
            # destructions déjà réussies aux itérations précédentes et les activités
            # de revue créées plus haut.
            try:
                with self.env.cr.savepoint():
                    request.action_execute()
            except Exception as e:
                _logger.exception(
                    "Cron failed to execute destruction %s: %s",
                    request.certificate_number, e,
                )
                # Le savepoint a défait l'exécution partielle ; ce message-ci
                # s'écrit donc dans une transaction saine et survit.
                request.message_post(
                    body=(
                        f"Échec de l'exécution : {e}. La destruction a été "
                        f"entièrement annulée, la demande reste à exécuter."
                    ),
                    message_type="notification",
                )

    @api.model
    def cron_create_destruction_requests(self):
        """Create destruction requests for consents that have exceeded retention."""
        Consent = self.env["privacy.consent"]
        Policy = self.env["privacy.retention.policy"]

        policies = Policy.search([("active", "=", True)])

        for policy in policies:
            # Determine trigger condition
            if policy.trigger_on == "expiration":
                domain = [
                    ("status", "=", "expired"),
                    ("purpose_id", "=", policy.purpose_id.id),
                ]
            elif policy.trigger_on == "withdrawal":
                domain = [
                    ("status", "=", "withdrawn"),
                    ("purpose_id", "=", policy.purpose_id.id),
                ]
            else:  # both
                domain = [
                    ("status", "in", ["expired", "withdrawn"]),
                    ("purpose_id", "=", policy.purpose_id.id),
                ]

            consents = Consent.search(domain)

            for consent in consents:
                # Check if destruction request already exists
                existing = self.search([
                    ("consent_id", "=", consent.id),
                    ("state", "!=", "cancelled"),
                ], limit=1)

                if existing:
                    continue

                # Determine trigger date
                if consent.status == "expired":
                    trigger_date = consent.expires_at or consent.write_date
                else:
                    trigger_date = consent.withdrawn_at or consent.write_date

                # Calculate scheduled date
                scheduled_date = (trigger_date + timedelta(days=policy.retention_days)).date()

                # Create destruction request
                self.create({
                    "consent_id": consent.id,
                    "policy_id": policy.id,
                    "trigger_date": trigger_date,
                    "scheduled_date": scheduled_date,
                })
