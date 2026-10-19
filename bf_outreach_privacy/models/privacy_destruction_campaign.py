"""Exécution réelle de la destruction d'une cible ou d'une interaction.

La destruction elle-même vit dans `bf.outreach.target.unlink()` (interactions
par l'ORM, refus inscrit avant, garde de la seule preuve, copie fédérée
locale) : tout chemin de suppression y passe, campagne, gestionnaire ou
demande d'effacement. Ici, on ajoute ce qui est propre à une campagne :
revérifier que la ligne est encore due, et refuser de certifier ce qui n'a pas
eu lieu.

⚠️ **On ne passe pas par l'exécuteur du socle** : il contrôle le droit
d'écriture du responsable de la vie privée sur la cible, et ce responsable n'a
aucune raison d'être aussi usager du Démarchage. Il échouerait en « Accès
refusé » sur chaque ligne. L'approbation de la campagne est le contrôle.

⚠️ **Cette surcharge RELAIE à `super()`** pour les modèles qu'elle ne possède
pas : plusieurs ponts surchargent déjà cette méthode et ne cohabitent que par
ce relais.
"""
from odoo import _, models
from odoo.exceptions import UserError

OUTREACH_MODELS = ("bf.outreach.target", "bf.outreach.touch")


class PrivacyDestructionCampaignLine(models.Model):
    _inherit = "privacy.destruction.campaign.line"

    def _execute_destruction(self):
        """Détruit vraiment, et lève plutôt que de laisser certifier ce qui n'a pas eu lieu."""
        self.ensure_one()
        if self.res_model not in OUTREACH_MODELS:
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
                "Le démarchage ne se détruit que par suppression. Méthode demandée : "
                "« %(method)s ». Anonymiser laisserait les notes et les interactions "
                "en base ; archiver ne détruit rien. Le registre attesterait à tort "
                "une destruction.",
                method=self.destruction_method or _("(vide)"),
            ))
        if record._name == "bf.outreach.target" and self.retention_calendar_id \
                and not record._privacy_still_due(self.retention_calendar_id):
            # La campagne a pu attendre des semaines : la cible a répondu, ou
            # son refus a été levé. Le registre n'attesterait pas une échéance fausse.
            self.write({"state": "skipped", "error_message": _(
                "N'est plus due sous cette règle à l'exécution (contact utile récent, "
                "ou refus levé) : non détruite.")})
            return

        classifications = self.env["privacy.document.classification"].sudo().with_context(
            active_test=False).search(self._bf_classification_domain(record))
        # Le socle recopie `res_name` au registre IMMUABLE après notre retour. Une
        # ligne née d'une classification porte le nom de la personne : on le
        # remplace par le numéro, sur la ligne comme sur les classifications.
        anonymous = (_("Cible de démarchage #%s", record.id) if record._name == "bf.outreach.target"
                     else _("Interaction de démarchage #%s", record.id))
        self.res_name = anonymous
        record.unlink()
        if Model.browse(self.res_id).exists():
            raise UserError(_(
                "%(model)s #%(id)s existe toujours après la suppression. Rien n'est "
                "inscrit au registre : une destruction qui n'a pas eu lieu ne "
                "s'atteste pas.", model=self.res_model, id=self.res_id,
            ))
        # Toutes les classifications de la fiche et de ses interactions, pas
        # seulement celle de la ligne.
        for cls in classifications:
            cls.write({"active": False, "res_name": f"{cls.res_model} #{cls.res_id}"}
                      if "res_name" in cls._fields else {"active": False})
        return None

    def _bf_classification_domain(self, record):
        if record._name == "bf.outreach.target":
            return ["|",
                    "&", ("res_model", "=", "bf.outreach.target"), ("res_id", "=", record.id),
                    "&", ("res_model", "=", "bf.outreach.touch"),
                    ("res_id", "in", record.touch_ids.ids or [0])]
        return [("res_model", "=", record._name), ("res_id", "=", record.id)]


class PrivacyDestructionCampaign(models.Model):
    _inherit = "privacy.destruction.campaign"

    def action_scan(self):
        """Refuse le balayage natif sur une campagne alimentée par le cron du pont."""
        rules = self.env["bf.outreach.target"]._privacy_rules()
        for campaign in self:
            if campaign.retention_calendar_id and campaign.retention_calendar_id in rules:
                raise UserError(_(
                    "Cette campagne est alimentée par le balayage de rétention du "
                    "démarchage, pas par les classifications documentaires. Le "
                    "balayage natif n'y trouverait rien. Approuvez la campagne, "
                    "puis exécutez-la."
                ))
        return super().action_scan()
