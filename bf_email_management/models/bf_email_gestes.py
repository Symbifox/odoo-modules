"""Les petits gestes qui manquaient à la boîte.

Aucun n'est une fonction : ce sont des raccourcis d'un travail qu'on faisait
déjà à la main, et c'est exactement ce qui les rend rentables.

- **Annuler la dernière action** (`z` chez Gmail). Traité, reporté et mis en
  sourdine se défaisaient en retrouvant la ligne, ce que personne ne fait.
- **Tout marquer comme lu** dans le dossier ouvert. Mesuré sur BF le
  2026-09-13 : 3 757 lignes non lues dont **2 245 déjà traitées**, donc un
  compteur qu'on ne peut pas faire descendre.
- **Mettre à la corbeille** depuis la boîte. Le navigateur IMAP savait le
  faire, la boîte non. ⚠️ Une ligne classée sur une fiche est REFUSÉE : le
  message appartient au dossier, et le sortir de là se fait par « Re-router ».
- **Rattacher un contact.** 7 647 reçus sur 12 432 n'ont pas de `partner_id`,
  et toutes les règles qui parlent du partenaire s'en trouvent aveugles.
- **Créer une règle depuis le courriel ouvert.** C'est ce qui règle les 685
  lignes d'un expéditeur qu'aucun motif ne peut deviner.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Plafond du « tout marquer comme lu » : au-delà, ce n'est plus un geste, c'est
# une migration, et elle mérite la vue liste et ses actions de lot.
MARQUER_LU_MAX = 2000


class BfEmailGestes(models.Model):
    _inherit = "bf.email"

    # ------------------------------------------------------------------
    # Annuler
    # ------------------------------------------------------------------
    def action_unsnooze(self):
        """Réveille un report : la ligne revient dans la boîte tout de suite."""
        self.write({"snoozed_until": False, "is_handled": False})
        return False

    # ------------------------------------------------------------------
    # Tout marquer comme lu
    # ------------------------------------------------------------------
    @api.model
    def inbox_mark_folder_read(self, folder):
        """Passe en « lu » les non-lus du dossier ouvert, et dit combien.

        ⚠️ Marquer lu n'est PAS traiter. Les deux axes existent séparément dans
        ce module depuis toujours, et c'est justement ce que cette action rend
        utilisable : on peut vider un compteur sans prétendre avoir traité
        quoi que ce soit.
        """
        domaine = self._inbox_folder_domain(folder) + [("status", "=", "new")]
        lignes = self.search(domaine, limit=MARQUER_LU_MAX)
        inscriptibles = lignes._filtered_access("write")
        nombre = len(inscriptibles)
        if inscriptibles:
            inscriptibles.write({"status": "read"})
        reste = self.search_count(domaine) - nombre
        return {"marked": nombre, "remaining": max(reste, 0)}

    # ------------------------------------------------------------------
    # Corbeille
    # ------------------------------------------------------------------
    def action_trash(self):
        """Met à la corbeille IMAP et retire la ligne de toute liste.

        ⚠️ Une ligne classée sur une fiche est refusée. Le message vit dans le
        chatter de cette fiche ; le jeter d'ici laisserait la fiche avec un
        message dont la ligne a disparu, et la boîte n'est pas le bon endroit
        pour décider du sort d'un dossier client.
        """
        classees = self.filtered(lambda r: r.res_model and r.res_id)
        if classees:
            raise UserError(_(
                "%s courriel(s) sont classés sur une fiche et ne se jettent "
                "pas d'ici : le message appartient à son dossier. Utilise "
                "« Re-router… » pour l'en sortir d'abord.", len(classees)))
        for rec in self:
            if rec.imap_uid and rec.imap_folder and rec.account_id:
                try:
                    rec._imap_writeback_move("Trash")
                except Exception:
                    # Le serveur peut refuser, ou la corbeille ne pas exister.
                    # La ligne sort quand même de la boîte : ce que l'usager a
                    # demandé, c'est de ne plus la voir.
                    _logger.info(
                        "bf.email #%s : corbeille IMAP refusée, la ligne est "
                        "quand même archivée", rec.id, exc_info=True)
        self.write({"is_handled": True, "active": False})
        return False

    # ------------------------------------------------------------------
    # Contact
    # ------------------------------------------------------------------
    def action_link_partner(self):
        """Rattache un contact connu, ou ouvre la fiche à créer.

        Sur un lot, on se contente de rattacher ce qui est connu : ouvrir
        quinze formulaires de création n'aiderait personne.
        """
        Partner = self.env["res.partner"]
        rattaches = 0
        inconnus = self.env["bf.email"]
        for rec in self:
            if rec.partner_id:
                continue
            adresse = rec._external_address()
            partner = rec._resolve_partner_by_email(adresse) if adresse else None
            if partner:
                rec.partner_id = partner.id
                rattaches += 1
            else:
                inconnus |= rec
        if len(self) == 1 and inconnus:
            rec = inconnus
            nom, adresse = rec._name_and_address()
            return {
                "type": "ir.actions.act_window",
                "res_model": "res.partner",
                "view_mode": "form",
                "views": [[False, "form"]],
                "target": "new",
                "context": {
                    "default_name": nom or adresse,
                    "default_email": adresse,
                    "bf_email_source_id": rec.id,
                },
            }
        return self._notification(
            _("Contacts rattachés"),
            _("%(lies)s rattaché(s), %(inconnus)s sans contact connu.",
              lies=rattaches, inconnus=len(inconnus)),
        )

    def _external_address(self):
        """L'adresse de l'autre : l'expéditeur si on reçoit, sinon le premier
        destinataire."""
        self.ensure_one()
        import re as _re
        brut = (self.email_from if self.direction == "in" else self.email_to) or ""
        trouve = _re.search(r"[\w\.\-\+']+@[\w\.\-]+", brut)
        return trouve.group(0).lower() if trouve else ""

    def _name_and_address(self):
        """(nom lisible, adresse) tirés de l'en-tête, pour préremplir."""
        self.ensure_one()
        from email.utils import parseaddr
        brut = (self.email_from if self.direction == "in" else self.email_to) or ""
        nom, adresse = parseaddr(brut)
        return (nom or "").strip(), (adresse or "").strip().lower()

    # ------------------------------------------------------------------
    # Règle depuis le courriel ouvert
    # ------------------------------------------------------------------
    def action_create_rule_here(self):
        """Ouvre une règle neuve, condition déjà écrite sur cet expéditeur.

        Le catalogue de recettes couvre les cas généraux ; celui-ci couvre le
        cas qu'aucun motif ne devine, et c'est le plus fréquent quand une
        boîte est encombrée par UN expéditeur.
        """
        self.ensure_one()
        adresse = self._external_address()
        if not adresse:
            raise UserError(_(
                "Ce courriel ne porte aucune adresse lisible : il n'y a rien "
                "sur quoi écrire une condition."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Nouvelle règle"),
            "res_model": "bf.email.rule",
            "view_mode": "form",
            "views": [[False, "form"]],
            "target": "new",
            "context": {
                "default_name": _("Courriels de %s", adresse),
                "default_user_id": self.env.uid,
                "default_condition_ids": [(0, 0, {
                    "field_name": "email_from",
                    "operator": "contains",
                    "value": adresse,
                })],
            },
        }

    def _notification(self, titre, message, kind="success"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": titre, "message": message,
                       "type": kind, "sticky": False},
        }
