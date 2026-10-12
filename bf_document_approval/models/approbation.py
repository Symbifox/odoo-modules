# -*- coding: utf-8 -*-
"""Qui devait se prononcer, qui l'a fait, et ce qui en découle.

Le besoin, tel qu'il a été formulé : « Monsieur Tremblay a décidé ça, mais
Madame Couture finalement a décidé que ça serait telle affaire ; ils ont voté,
ça a été approuvé. Puis quand c'est approuvé, les gens qui sont susceptibles
d'être touchés reçoivent une copie automatiquement. »

Trois choses, donc : plusieurs avis, un verrou tant qu'ils manquent, et une
diffusion qui part toute seule vers les bonnes personnes. La troisième existait
déjà — `project.document.distribution` sait accuser réception, exiger une
signature et se marquer périmée. Ce fichier ne réécrit rien de tout ça : il
nomme les approbateurs, il bloque, et il branche la diffusion sur le RACI qui
vit déjà dans la matrice.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

AVIS = [
    ("attente", "Pending"),
    ("approuve", "Approved"),
    ("refuse", "Rejected"),
]

GROUPE_GESTION = "project_knowledge_matrix.group_document_manager"

# Qui écrit quoi sur une ligne. Hors superutilisateur, une écriture
# qui touche autre chose que ces champs est refusée : `avis` et `date_avis` ne
# s'écrivent que par `_poser`, en sudo, après avoir vérifié qui parle.
#: Le motif est la parole de l'approbateur : lui seul l'écrit.
CHAMPS_APPROBATEUR = {"commentaire"}
#: Composer le tour de table : le responsable du document, un gestionnaire.
CHAMPS_COMPOSITION = {"user_id", "requis", "sequence"}
#: Ce qui ne bouge plus quand l'avis est donné (l'ordre d'affichage, si).
CHAMPS_FIGES = {"user_id", "requis"}


class ProjectDocumentApprover(models.Model):
    """L'avis d'une personne sur une version de document.

    Un avis appartient à la personne nommée. Avant 1.1.0, n'importe quel
    utilisateur interne pouvait le donner à sa place (et le fil disait alors
    « <approbateur> a approuvé »), l'écrire directement par RPC, ou retirer un
    approbateur requis qui avait refusé, puis publier. Désormais :

    * seule la personne nommée approuve ou refuse, par les boutons ;
    * `avis` et `date_avis` ne s'écrivent jamais directement ;
    * le tour de table se compose par qui répond du document
      (voir `_peut_composer`) ;
    * une ligne dont l'avis est donné ne se retire plus et ne change plus de
      titulaire ni de caractère requis : ni le responsable ni un gestionnaire
      ne peuvent effacer un refus pour publier. Pour reprendre le tour de
      table, on crée une nouvelle version ;
    * ce qui affaiblit le verrou sur une ligne en attente (retrait,
      remplacement, avis rendu facultatif) s'écrit au fil du document.

    Le superutilisateur (code serveur en sudo) passe, et le fil dit alors qui a
    consigné l'avis et pour qui.
    """

    _name = "project.document.approver"
    _description = "Document version approver"
    _order = "version_id, sequence, id"

    version_id = fields.Many2one(
        "project.document.version", string="Version", required=True,
        ondelete="cascade", index=True)
    document_id = fields.Many2one(
        "project.document", related="version_id.document_id", store=True,
        string="Document", index=True)
    sequence = fields.Integer(string="Order", default=10)
    user_id = fields.Many2one(
        "res.users", string="Approver", required=True,
        help="The person who must decide, not the one who writes.")
    requis = fields.Boolean(
        string="Required", default=True,
        help="Unchecked, the decision is requested but does not block "
             "publication.")
    avis = fields.Selection(AVIS, string="Decision", default="attente",
                            required=True, copy=False)
    date_avis = fields.Datetime(string="Decision date", readonly=True,
                                copy=False)
    commentaire = fields.Char(
        string="Reason", copy=False,
        help="Required for a rejection: a rejection without a reason "
             "cannot be acted on.")
    est_mon_avis = fields.Boolean(
        string="My decision", compute="_compute_est_mon_avis",
        help="True for the named person only: only they see the Approve "
             "and Reject buttons.")

    _sql_constraints = [
        ("un_avis_par_personne", "unique (version_id, user_id)",
         "This person is already an approver of this version."),
    ]

    @api.depends("user_id")
    @api.depends_context("uid")
    def _compute_est_mon_avis(self):
        for rec in self:
            rec.est_mon_avis = rec.user_id == self.env.user

    # ------------------------------------------------------------ les droits
    @api.model_create_multi
    def create(self, vals_list):
        lignes = super().create(vals_list)
        if not self.env.su:
            # Contrôlé sur les lignes créées, pas sur `vals` : un
            # `default_avis` passé dans le contexte ne figure pas dans `vals`.
            if lignes.filtered(lambda l: l.avis != "attente" or l.date_avis):
                raise AccessError(self._message_avis_par_les_boutons())
            if lignes.filtered(
                    lambda l: l.commentaire and l.user_id != self.env.user):
                raise AccessError(self._message_motif_a_soi())
            lignes.version_id._verifier_composition()
        return lignes

    def write(self, vals):
        if self.env.su or not self:
            return super().write(vals)
        changes = self._champs_changes(vals)
        if changes & {"avis", "date_avis"}:
            raise AccessError(self._message_avis_par_les_boutons())
        hors_champ = changes - CHAMPS_APPROBATEUR - CHAMPS_COMPOSITION
        if hors_champ:
            raise AccessError(_(
                "These fields of an approval line cannot be written "
                "directly: %(champs)s.",
                champs=", ".join(sorted(hors_champ))))
        if "commentaire" in changes and self.filtered(
                lambda l: l.user_id != self.env.user):
            raise AccessError(self._message_motif_a_soi())
        if changes & CHAMPS_COMPOSITION:
            self.version_id._verifier_composition()
            if changes & CHAMPS_FIGES:
                self._refuser_si_avis_donne()
        traces = self._traces_avant_ecriture(vals, changes)
        res = super().write(vals)
        self._tracer(traces)
        return res

    def unlink(self):
        if not self.env.su:
            self.version_id._verifier_composition()
            self._refuser_si_avis_donne()
        # Une boucle et non une compréhension : en Python 3.10 et 3.11, la compréhension a
        # son propre cadre, où `_()` ne trouve `self` que tant qu'elle le cite. Pas
        # `self.env._` non plus : sans langue au contexte (RPC), il écrit en anglais, alors
        # que `_()` reprend la langue de l'usager, comme les autres traces.
        traces = []
        for ligne in self.sudo():
            traces.append((ligne.document_id, _(
                "%(auteur)s removed %(qui)s from the approvers of version "
                "%(version)s.",
                auteur=self.env.user.name, qui=ligne.user_id.name,
                version=ligne.version_id.version_number or "")))
        res = super().unlink()
        self._tracer(traces)
        return res

    def _champs_changes(self, vals):
        """Les champs que `vals` change vraiment sur au moins une ligne.

        Le client web renvoie parfois une valeur inchangée (la poignée de tri
        renumérote toute la liste) : refuser un geste vide bloquerait
        l'approbateur qui enregistre son motif.
        """
        changes = set()
        for nom, valeur in vals.items():
            champ = self._fields.get(nom)
            if champ is None:
                changes.add(nom)
                continue
            for ligne in self:
                nouvelle = champ.convert_to_record(
                    champ.convert_to_cache(valeur, ligne), ligne)
                if ligne[nom] != nouvelle:
                    changes.add(nom)
                    break
        return changes

    def _refuser_si_avis_donne(self):
        donnes = self.filtered(lambda l: l.avis != "attente")
        if donnes:
            raise UserError(_(
                "%(qui)s has given their decision: the line can no longer "
                "be removed and no longer changes holder or required "
                "status. To start the approvals over, create a new "
                "version.",
                qui=", ".join(donnes.mapped("user_id.name"))))

    def _message_avis_par_les_boutons(self):
        return _(
            "A decision cannot be edited directly: the named person gives "
            "it with the Approve and Reject buttons.")

    def _message_motif_a_soi(self):
        return _(
            "The reason belongs to the person who decides: no one else "
            "writes it for them.")

    def _traces_avant_ecriture(self, vals, changes):
        """Ce qui, dans `vals`, affaiblit le verrou d'une ligne en attente."""
        traces = []
        auteur = self.env.user.name
        nouveau = self.env["res.users"].sudo().browse(
            self._fields["user_id"].convert_to_cache(vals["user_id"], self)
        ) if "user_id" in changes else None
        for ligne in self.sudo():
            version = ligne.version_id.version_number or ""
            if nouveau is not None and ligne.user_id != nouveau:
                traces.append((ligne.document_id, _(
                    "%(auteur)s replaced %(avant)s with %(apres)s among "
                    "the approvers of version %(version)s.",
                    auteur=auteur, avant=ligne.user_id.name,
                    apres=nouveau.name or "—", version=version)))
            if "requis" in changes and ligne.requis and not vals["requis"]:
                traces.append((ligne.document_id, _(
                    "%(auteur)s made the decision of %(qui)s on version "
                    "%(version)s optional: it no longer blocks "
                    "publication.",
                    auteur=auteur, qui=ligne.user_id.name, version=version)))
        return traces

    def _tracer(self, traces):
        """Écrit au fil du document, au nom de qui a fait le geste."""
        auteur = self.env.user.partner_id.id
        for document, corps in traces:
            if document:
                document.sudo().message_post(body=corps, author_id=auteur)

    # -------------------------------------------------------------- l'avis
    def _poser(self, avis, commentaire=None):
        self.ensure_one()
        auteur = self.env.user
        if not self.env.su and self.user_id != auteur:
            raise AccessError(_(
                "This decision belongs to %(qui)s: no one else can give "
                "it for them.", qui=self.user_id.name))
        motif = commentaire or self.commentaire
        if avis == "refuse" and not motif:
            raise UserError(_(
                "A rejection without a reason cannot be acted on: say "
                "what needs to change."))
        vals = {"avis": avis, "date_avis": fields.Datetime.now()}
        if commentaire:
            vals["commentaire"] = commentaire
        # L'identité est vérifiée : l'écriture passe en sudo, seule voie vers
        # `avis` et `date_avis`.
        self.sudo().write(vals)
        version = self.version_id.version_number or ""
        motif = " %s" % motif if motif else ""
        if auteur == self.user_id:
            corps = _(
                "%(qui)s has %(quoi)s version %(version)s.%(motif)s",
                qui=auteur.name,
                quoi=_("approved") if avis == "approuve" else _("rejected"),
                version=version, motif=motif)
        else:
            # Seul un code serveur en sudo arrive ici. Le fil ne prête pas
            # la parole : il dit qui a consigné l'avis, et pour qui.
            corps = _(
                "%(auteur)s recorded, on behalf of %(qui)s, the decision "
                "\"%(quoi)s\" on version %(version)s.%(motif)s",
                auteur=auteur.name, qui=self.user_id.name,
                quoi=_("approved") if avis == "approuve" else _("rejected"),
                version=version, motif=motif)
        self.version_id.document_id.sudo().message_post(
            body=corps, author_id=auteur.partner_id.id)
        return True

    def action_approuver(self):
        return self._poser("approuve")

    def action_refuser(self):
        return self._poser("refuse")


class ProjectDocumentVersionApprobation(models.Model):
    _inherit = "project.document.version"

    approver_ids = fields.One2many(
        "project.document.approver", "version_id", string="Approvers")
    approbation_attendue_count = fields.Integer(
        string="Decisions awaited", compute="_compute_approbation")
    approbation_refusee = fields.Boolean(
        string="Version rejected", compute="_compute_approbation")
    approbation_complete = fields.Boolean(
        string="Approval complete", compute="_compute_approbation",
        help="No required decision is missing, and none is a rejection.")
    approbateurs_modifiables = fields.Boolean(
        string="Can set the approvers",
        compute="_compute_approbateurs_modifiables",
        help="True for the document's owner (failing that, its author) "
             "and document managers.")

    @api.depends("approver_ids.avis", "approver_ids.requis")
    def _compute_approbation(self):
        for rec in self:
            # Le verrou se calcule sur toutes les lignes, pas sur celles que
            # la personne qui publie a le droit de lire.
            lignes = rec.sudo().approver_ids
            requis = lignes.filtered("requis")
            manquants = requis.filtered(lambda a: a.avis == "attente")
            rec.approbation_attendue_count = len(manquants)
            rec.approbation_refusee = bool(
                lignes.filtered(lambda a: a.avis == "refuse"))
            rec.approbation_complete = bool(
                not manquants and not rec.approbation_refusee)

    @api.depends("document_id.owner_id", "document_id.author_id")
    @api.depends_context("uid")
    def _compute_approbateurs_modifiables(self):
        for rec in self:
            rec.approbateurs_modifiables = rec._peut_composer()

    def _peut_composer(self):
        """Qui compose le tour de table : ajoute, retire, rend facultatif.

        Les gestionnaires des documents, et la personne qui répond du
        document : son responsable, à défaut son auteur, la même que le module
        hôte charge des activités du document. Pas n'importe quel rédacteur :
        il choisirait qui le juge, et pourrait retirer l'approbateur qui tarde.
        """
        if self.env.su or self.env.user.has_group(GROUPE_GESTION):
            return True
        for version in self.sudo():
            document = version.document_id
            if (document.owner_id or document.author_id) != self.env.user:
                return False
        return True

    def _verifier_composition(self):
        for version in self:
            if not version._peut_composer():
                document = version.sudo().document_id
                raise AccessError(_(
                    "The approvers of version %(version)s are set by "
                    "%(qui)s, who is accountable for the document, or by "
                    "a document manager.",
                    version=version.sudo().version_number or "",
                    qui=(document.owner_id or document.author_id).name
                    or _("its owner")))

    def _exiger_approbation(self, geste):
        """Refuse le geste tant que le tour de table n'est pas fini.

        Sans approbateur nommé, rien ne change : c'est le comportement d'avant,
        et une organisation qui n'a pas de tour de table à faire ne doit pas
        être forcée d'en inventer un.
        """
        for rec in self:
            lignes = rec.sudo().approver_ids
            if not lignes.filtered("requis"):
                continue
            if rec.approbation_refusee:
                refus = lignes.filtered(lambda a: a.avis == "refuse")
                # Hors de l'appel et hors de l'expression génératrice : logé dans les
                # arguments d'un autre `_()`, l'extracteur ne le voyait pas, et dans la
                # génératrice `_()` n'a pas de `self` pour lire la langue.
                sans_motif = _("no reason given")
                raise UserError(_(
                    "%(geste)s: refused. %(qui)s rejected this "
                    "version.\n\n%(motifs)s",
                    geste=geste, qui=", ".join(refus.mapped("user_id.name")),
                    motifs="\n".join(
                        "· %s" % (a.commentaire or sans_motif)
                        for a in refus)))
            if rec.approbation_attendue_count:
                attente = lignes.filtered(
                    lambda a: a.requis and a.avis == "attente")
                raise UserError(_(
                    "%(geste)s: refused. %(n)s decision(s) still "
                    "missing.\n\nWaiting for %(qui)s.",
                    geste=geste, n=rec.approbation_attendue_count,
                    qui=", ".join(attente.mapped("user_id.name"))))

    def action_approve(self):
        self._exiger_approbation(_("Approve this version"))
        return super().action_approve()

    def action_release(self):
        # ⚠️ `action_release` du module hôte approuve d'office quand personne
        # ne l'a fait. Une politique pouvait donc être publiée d'un clic sans
        # qu'aucun des approbateurs nommés se soit prononcé : c'est ce chemin-là
        # qu'il faut fermer, pas seulement `action_approve`.
        self._exiger_approbation(_("Publish this version"))
        return super().action_release()

    # ------------------------------------------------------------ diffusion
    def _partenaires_informes(self):
        """Les parties prenantes à informer, d'après la matrice du document.

        On ne tient pas une deuxième liste de destinataires : le RACI vit dans
        les éléments de matrice, et une liste recopiée est une liste qui se
        périme.
        """
        self.ensure_one()
        matrice = self.document_id.matrix_id
        if not matrice:
            return self.env["res.partner"].browse()
        elements = self.env["project.knowledge.item"].search(
            [("matrix_id", "=", matrice.id)])
        return elements.mapped("stakeholder_informed_ids")

    def action_diffuser_aux_informes(self):
        """Crée les distributions manquantes vers les personnes à informer."""
        self.ensure_one()
        if self.state not in ("approved", "released"):
            raise UserError(_(
                "A version that is not approved cannot be distributed."))
        Distribution = self.env["project.document.distribution"]
        deja = Distribution.search(
            [("version_id", "=", self.id)]).mapped("partner_id")
        cibles = self._partenaires_informes() - deja
        if not cibles:
            raise UserError(_(
                "No one new to inform: either the matrix names no "
                "informed stakeholder, or they have all already received "
                "this version."))
        creees = Distribution.create([{
            "version_id": self.id,
            "recipient_type": "partner",
            "partner_id": partenaire.id,
            "distribution_method": "email",
        } for partenaire in cibles])
        self.document_id.message_post(body=_(
            "Version %(version)s distributed to %(n)s informed "
            "stakeholder(s): %(qui)s.",
            version=self.version_number or "", n=len(creees),
            qui=", ".join(cibles.mapped("display_name"))))
        return {
            "type": "ir.actions.act_window",
            "name": _("Distributions for version %s") % (self.version_number or ""),
            "res_model": "project.document.distribution",
            "view_mode": "list,form",
            "domain": [("id", "in", creees.ids)],
        }
