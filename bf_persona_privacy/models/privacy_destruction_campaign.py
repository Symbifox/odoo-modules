"""Exécution réelle de la destruction d'un persona, d'un indicateur ou d'une règle de copie.

La destruction elle-même vit dans `contact.persona.unlink()` (indicateurs et
règles par l'ORM, fil par `mail.thread`) : tout chemin de suppression y passe.
Ici, on ajoute ce qui est propre à une campagne : revérifier que le persona est
encore dû, et refuser de certifier ce qui n'a pas eu lieu.

⚠️ **On ne passe pas par l'exécuteur du socle** : il contrôle le droit
d'écriture du responsable de la vie privée sur la fiche, et ce responsable n'a
aucune raison d'être aussi gestionnaire persona. Il échouerait en « Accès
refusé » sur chaque ligne. L'approbation de la campagne est le contrôle.

⚠️ **Cette surcharge RELAIE à `super()`** pour les modèles qu'elle ne possède
pas : plusieurs ponts surchargent déjà cette méthode et ne cohabitent que par
ce relais.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

PERSONA_MODELS = ("contact.persona", "contact.persona.kpi", "contact.cc.rule")


def _retired_classification_vals(cls):
    """Archive la classification et retire ce qui nomme ou décrit la personne."""
    vals = {"active": False, "subject_partner_id": False,
            "notes": f"[Persona détruit le {fields.Date.today()}]"}
    if "res_name" in cls._fields:
        vals["res_name"] = f"{cls.res_model} #{cls.res_id}"
    return vals


_SIGN_LABELS = {"inbound": "reçu", "outbound": "envoyé", "invoice": "facture"}


class PrivacyDestructionCampaignLine(models.Model):
    _inherit = "privacy.destruction.campaign.line"

    # Le responsable approuve sans rôle persona : sans ce champ, il ne verrait
    # que « Persona #N ». NON stocké : il disparaît avec le persona, et le socle
    # ne recopie que `res_name` au registre.
    bf_persona_review = fields.Char(
        string="À vérifier (persona)",
        compute="_compute_bf_persona_review",
        groups="privacy_consent.group_privacy_officer",
        help="Le contact et ses derniers signes de vie, relus à l'ouverture. "
             "Rien de ceci n'entre au registre.",
    )

    @api.depends("res_model", "res_id")
    def _compute_bf_persona_review(self):
        Persona = self.env["contact.persona"].sudo().with_context(active_test=False)
        for line in self:
            line.bf_persona_review = False
            # Seulement ce qui reste à décider : chaque ligne refait la mesure.
            if line.res_model != "contact.persona" or not line.res_id \
                    or line.state != "pending" \
                    or line.campaign_id.state not in ("draft", "review", "approved"):
                continue
            persona = Persona.browse(line.res_id).exists()
            if not persona:
                line.bf_persona_review = _("déjà supprimé")
                continue
            signs = persona._privacy_last_signs()
            facts = ", ".join(f"{_SIGN_LABELS.get(k, k)} {v}" for k, v in sorted(signs.items()))
            line.bf_persona_review = _(
                "%(contact)s — derniers signes : %(facts)s ; créé le %(created)s%(unpaid)s",
                contact=persona.partner_id.display_name,
                facts=facts or _("aucun"),
                created=fields.Date.to_date(persona.create_date),
                unpaid=_(" ; FACTURE IMPAYÉE") if persona._privacy_has_unpaid_invoice() else "",
            )

    def _execute_destruction(self):
        """Détruit vraiment, et lève plutôt que de laisser certifier ce qui n'a pas eu lieu."""
        self.ensure_one()
        if self.res_model not in PERSONA_MODELS:
            return super()._execute_destruction()
        if not self.res_id:
            # Sans identifiant, le socle certifierait « #0 » : rien à détruire.
            self.write({"state": "skipped", "error_message": _("Ligne sans enregistrement")})
            return
        Model = self.env[self.res_model].sudo().with_context(active_test=False)
        record = Model.browse(self.res_id).exists()
        if not record:
            self.write({"state": "skipped", "error_message": _("Enregistrement déjà supprimé")})
            return
        if self.destruction_method != "delete":
            raise UserError(_(
                "Un persona ne se détruit que par suppression. Méthode demandée : "
                "« %(method)s ». Anonymiser laisserait la cote de payeur dans "
                "l'historique du fil ; archiver ne détruit rien. Le registre "
                "attesterait à tort une destruction.",
                method=self.destruction_method or _("(vide)"),
            ))
        # Une ligne sans règle (ou dont on a vidé la règle) relève de PER-001 :
        # sinon elle sauterait la revérification du silence et des impayés.
        rule = self.retention_calendar_id or record.env["contact.persona"]._privacy_rules()
        if record._name == "contact.persona" and not record._privacy_still_due(rule):
            # La campagne a pu attendre des semaines : la personne a réécrit, ou
            # une facture est restée impayée. Le registre n'attesterait pas une
            # échéance fausse.
            self.write({"state": "skipped", "error_message": _(
                "N'est plus dû sous cette règle à l'exécution (échange récent, ou "
                "facture impayée) : non détruit.")})
            return

        classifications = self.env["privacy.document.classification"].sudo().with_context(
            active_test=False).search(self._bf_persona_classification_domain(record))
        # Le socle recopie `res_name` au registre IMMUABLE après notre retour. Une
        # ligne née d'une classification porte le nom de la personne : on le
        # remplace par le numéro.
        self.res_name = self._bf_persona_anonymous_name(record)
        record.unlink()
        if Model.browse(self.res_id).exists():
            raise UserError(_(
                "%(model)s #%(id)s existe toujours après la suppression. Rien n'est "
                "inscrit au registre : une destruction qui n'a pas eu lieu ne "
                "s'atteste pas.", model=self.res_model, id=self.res_id,
            ))
        # Toutes les classifications du persona et de ce qui en dépendait, pas
        # seulement celle de la ligne. Archivées, elles ne doivent plus désigner
        # la personne : le registre renvoie à la campagne, la campagne à la
        # ligne, la ligne à la classification.
        for cls in classifications:
            cls.write(_retired_classification_vals(cls))
        return None

    def _bf_persona_anonymous_name(self, record):
        labels = {
            "contact.persona": _("Persona #%s"),
            "contact.persona.kpi": _("Indicateur de persona #%s"),
            "contact.cc.rule": _("Règle de copie de persona #%s"),
        }
        return labels[record._name] % record.id

    def _bf_persona_classification_domain(self, record):
        if record._name == "contact.persona":
            return ["|", "|",
                    "&", ("res_model", "=", "contact.persona"), ("res_id", "=", record.id),
                    "&", ("res_model", "=", "contact.persona.kpi"),
                    ("res_id", "in", record.kpi_ids.ids or [0]),
                    "&", ("res_model", "=", "contact.cc.rule"),
                    ("res_id", "in", record.cc_rule_ids.ids or [0])]
        return [("res_model", "=", record._name), ("res_id", "=", record.id)]


class PrivacyDestructionCampaign(models.Model):
    _inherit = "privacy.destruction.campaign"

    def action_scan(self):
        """Refuse le balayage natif sur une campagne alimentée par le cron du pont."""
        rules = self.env["contact.persona"]._privacy_rules()
        for campaign in self:
            if campaign.retention_calendar_id and campaign.retention_calendar_id in rules:
                raise UserError(_(
                    "Cette campagne est alimentée par le balayage de rétention des "
                    "personas, pas par les classifications documentaires. Le "
                    "balayage natif n'y trouverait rien. Approuvez la campagne, "
                    "puis exécutez-la."
                ))
        return super().action_scan()
