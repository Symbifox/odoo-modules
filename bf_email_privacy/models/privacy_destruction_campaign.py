"""Exécution réelle de la destruction d'un courriel, et refus de certifier autre chose.

🔴 Pourquoi cette surcharge existe.

`privacy.destruction.campaign.line._execute_destruction()` traite la méthode
« delete » ainsi ::

    elif method == "delete":
        if hasattr(record, "active"):
            record.sudo().write({"active": False})
        else:
            record.sudo().unlink()

`bf.email` **a** un champ `active`. Sans cette surcharge, une campagne
approuvée archiverait la ligne, laisserait intacts le corps, les adresses, les
en-têtes et la copie RFC 2822, parfois des centaines de Mo, puis `action_execute`
inscrirait « Suppression » au registre **immuable**. Une attestation de
destruction pour des enregistrements simplement masqués.

Le défaut vit dans `privacy_consent` et vaut pour tout modèle archivable. On ne
le corrige pas ici : redéfinir « supprimer » pour des modèles qu'on n'a pas
examinés dépasse le mandat. On couvre `bf.email`, et le défaut est signalé.
"""
import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PrivacyDestructionCampaignLine(models.Model):
    _inherit = "privacy.destruction.campaign.line"

    def _execute_destruction(self):
        """Détruit vraiment un `bf.email`, et le vérifie avant de rendre la main.

        ⚠️ La vérification n'est pas de la ceinture et bretelles. `action_execute`
        crée l'entrée de registre dès que cette méthode rend sans lever : si la
        ligne survivait, le registre attesterait une destruction qui n'a pas eu
        lieu. Lever est le seul moyen d'empêcher la certification.
        """
        self.ensure_one()
        if self.res_model != "bf.email":
            return super()._execute_destruction()

        if not self.res_id:
            return
        record = self.env["bf.email"].with_context(
            active_test=False
        ).browse(self.res_id).exists()
        if not record:
            # Déjà partie : rien à détruire, et surtout rien à certifier.
            self.write({
                "state": "skipped",
                "error_message": _("Courriel déjà supprimé"),
            })
            return

        if self.destruction_method != "delete":
            raise UserError(_(
                "L'inbox unifiée ne se détruit que par suppression. Méthode "
                "demandée : « %s ». Anonymiser ou archiver laisserait le corps "
                "du message et sa copie RFC 2822 en base, ce que le registre "
                "attesterait à tort comme une destruction.",
                self.destruction_method or _("(vide)"),
            ))

        # `unlink` emporte la copie RFC 2822 : `models.unlink` balaie
        # `ir_attachment WHERE res_model=… AND res_id IN …` en SQL brut, donc
        # les pièces à `res_field` non nul partent avec la ligne.
        record.sudo().unlink()

        if self.env["bf.email"].with_context(
            active_test=False
        ).browse(self.res_id).exists():
            raise UserError(_(
                "Le courriel #%s existe toujours après la suppression. Rien "
                "n'est inscrit au registre : une destruction qui n'a pas eu "
                "lieu ne s'atteste pas.", self.res_id,
            ))
        return


class PrivacyDestructionCampaign(models.Model):
    _inherit = "privacy.destruction.campaign"

    def action_scan(self):
        """Refuse le balayage natif sur une campagne alimentée par le cron.

        Le balayage natif lit `privacy.document.classification` ; nos lignes
        sont déposées directement par `bf.email._privacy_propose_lines`. Le
        lancer ne trouverait rien, ferait repasser la campagne par « balayage »
        puis « révision », et donnerait à croire qu'elle est vide.
        """
        for campaign in self:
            if campaign.retention_calendar_id and campaign.retention_calendar_id in (
                self.env["bf.email"]._privacy_rule_unfiled()
                | self.env["bf.email"]._privacy_rule_filed()
            ):
                raise UserError(_(
                    "Cette campagne est alimentée par le balayage de rétention "
                    "de l'inbox unifiée, pas par les classifications "
                    "documentaires. Le balayage natif n'y trouverait rien. "
                    "Approuvez la campagne, puis exécutez-la."
                ))
        return super().action_scan()
