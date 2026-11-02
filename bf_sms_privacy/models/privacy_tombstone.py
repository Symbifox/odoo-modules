"""La pierre tombale : ce qui empêche un import de ressusciter une ligne détruite.

🔴 **Le problème.** `bf_sms_archive` déduplique à l'import sur l'EXISTENCE
d'une ligne, pas sur une trace de destruction. `wizard/import_wizard.py`
précharge l'ensemble des `message_hash` (resp. `call_hash`) du propriétaire et
saute ce qu'il y retrouve. Une ligne détruite n'est plus dans cet ensemble : le
prochain fichier qui la contient la recrée.

Et le fichier existe encore. L'import ne détruit pas ce qu'il a lu, il le
DÉPLACE — `/mnt/sms-inbox/done/` côté disque, `<dossier surveillé>/done/` côté
Nextcloud. Le matériau d'origine est donc conservé indéfiniment à côté, et une
restauration complète depuis le téléphone reproduit le même effet.

C'est le piège vérifié en production sur `bf_email_privacy` : détruire puis
ré-ingérer chaque nuit, en écrivant une entrée de registre à chaque tour. Le
registre certifierait du vent. Là-bas, la parade est un plancher — le cron
refuse toute règle plus courte que la fenêtre de réconciliation IMAP. Ici, il
n'y a pas de fenêtre : le risque n'est pas périodique, il est ponctuel et
déclenché par un humain qui reverse un vieux fichier. Un plancher n'y peut
rien, une pierre tombale si.

⚠️ **Ce qu'on garde, et pourquoi c'est acceptable.** Un SHA-256 et une date.
Pas le numéro, pas le corps du message, pas le nom du contact. Le hachage est à
sens unique et sa seule fin est de FAIRE TENIR la destruction — art. 23
LPRPSP : on conserve le strict nécessaire à l'accomplissement de la fin, et la
fin ici est que ce qui a été détruit le reste. Le retirer rouvrirait la porte
qu'il ferme, donc il n'a pas d'échéance propre.
"""
from odoo import fields, models


class SmsArchivePrivacyTombstone(models.Model):
    _name = "sms.archive.privacy.tombstone"
    _description = "Pierre tombale — ligne de messagerie détruite"
    _order = "destroyed_on desc, id desc"
    _rec_name = "content_hash"

    content_hash = fields.Char(
        string="Empreinte",
        required=True,
        index=True,
        readonly=True,
        help="SHA-256 calculé par bf_sms_archive à l'ingestion "
             "(`message_hash` ou `call_hash`). Sert de clé de refus.",
    )
    res_model = fields.Selection(
        selection=[
            ("sms.archive.message", "Message SMS/MMS"),
            ("call.archive.call", "Journal d'appel"),
        ],
        string="Modèle",
        required=True,
        readonly=True,
    )
    destroyed_on = fields.Date(
        string="Détruit le",
        required=True,
        readonly=True,
        default=fields.Date.context_today,
    )
    campaign_id = fields.Many2one(
        comodel_name="privacy.destruction.campaign",
        string="Campagne",
        ondelete="set null",
        readonly=True,
        help="Campagne qui a exécuté la destruction, quand elle est connue.",
    )

    _sql_constraints = [
        (
            "hash_model_uniq",
            "UNIQUE(content_hash, res_model)",
            "Une empreinte ne se grave qu'une fois par modèle.",
        ),
    ]

    def _bf_blocked_hashes(self, res_model, hashes):
        """Sous-ensemble de `hashes` déjà gravé pour `res_model`.

        Lecture en `sudo` : le refus doit tenir quel que soit l'utilisateur qui
        importe, et le webhook VOIP.ms n'a rien à voir avec les droits vie
        privée.
        """
        hashes = [h for h in hashes if h]
        if not hashes:
            return set()
        rows = self.sudo().search_read(
            [("res_model", "=", res_model), ("content_hash", "in", hashes)],
            ["content_hash"],
        )
        return {row["content_hash"] for row in rows}

    def _bf_engrave(self, res_model, hashes, campaign=None):
        """Grave les empreintes manquantes. Idempotent."""
        hashes = {h for h in hashes if h}
        if not hashes:
            return self.browse()
        already = self._bf_blocked_hashes(res_model, hashes)
        missing = hashes - already
        if not missing:
            return self.browse()
        return self.sudo().create([
            {
                "content_hash": content_hash,
                "res_model": res_model,
                "campaign_id": campaign.id if campaign else False,
            }
            for content_hash in sorted(missing)
        ])
