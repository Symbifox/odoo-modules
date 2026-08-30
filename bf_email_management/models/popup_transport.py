"""Avis à l'arrivée d'un courriel, servi dans Odoo par le bus.

Second transport de la même nouvelle, à côté de ``push_transport.py`` qui, lui,
sort du navigateur et va au téléphone. Les deux partent du même point
d'ingestion — ``bf_email_mobile._sync_account`` — pour qu'ils ne divergent pas,
et chacun garde son propre interrupteur.

⚠️ La charge utile ne porte NI l'expéditeur NI l'objet, seulement un
identifiant. ``bus.bus._sendone`` diffuse au **partenaire**, un partenaire peut
porter plus d'un utilisateur, et les règles d'enregistrement de ``bf.email`` ne
sont jamais consultées par le bus. Le client reçoit donc l'id et relit la ligne
par l'ORM, qui, lui, applique les règles : une ligne qu'on n'a pas le droit de
lire ne produit tout simplement pas d'avis. C'est le même raisonnement que
``bf.email._broadcast_change``, et c'est la raison pour laquelle le canal
``bf_email/changed`` existant ne transporte qu'un ``reason``.

Trois niveaux de réglage, du plus large au plus fin :

- ``bf_email.popup_enabled`` (ir.config_parameter) — l'instance. Absent vaut
  **non** : un locataire qui reçoit ce code au prochain ``-u`` ne change pas de
  comportement. Chez Blue Fox le paramètre est posé à 1 à la main.
- ``bf.email.account.popup_mode`` — la personne. Le compte appartient déjà à
  quelqu'un (``user_id``), donc le réglage par compte est du même coup le
  réglage par personne, sans second champ à tenir d'accord.
- ``bf.email.account.popup_sticky_folders`` — le dossier. Ce qui atterrit là
  reste à l'écran jusqu'à un geste ; le reste s'efface tout seul.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

POPUP_CHANNEL = "bf_email/popup"
POPUP_ENABLED_PARAM = "bf_email.popup_enabled"
# Au-delà de ce nombre d'avis d'un même genre dans une seule passe, un résumé
# remplace la pile. Une reprise après panne ramène un lot entier d'un coup ;
# empiler cinquante toasts revient à n'en montrer aucun.
BATCH_POPUP_MAX = 5
TRUTHY = ("1", "true", "yes", "on")


class BfEmailPopup(models.AbstractModel):
    _name = "bf.email.popup"
    _description = "Avis à l'arrivée d'un courriel (bus Odoo)"

    # ------------------------------------------------------------------
    # Interrupteurs
    # ------------------------------------------------------------------
    @api.model
    def _instance_enabled(self):
        """Vrai seulement si le paramètre d'instance dit oui.

        ⚠️ Pas de valeur par défaut passée à ``get_param`` : une clé absente
        rend ``False`` et une clé vide rend ``""``, et les deux doivent valoir
        « non ». Passer "0" en défaut donnerait le même résultat ici, mais
        laisserait croire que la clé vide vaut le défaut, ce qui est faux.
        """
        value = self.env["ir.config_parameter"].sudo().get_param(
            POPUP_ENABLED_PARAM)
        return str(value or "").strip().lower() in TRUTHY

    @api.model
    def _watching(self, owner):
        """Cette personne attend-elle un avis sur au moins un de ses comptes ?

        Sert à décider, AVANT la synchro, s'il vaut la peine de relever les
        lignes fraîches. Sans ce test, ``_sync_account`` paierait le relevé
        pour rien sur tout locataire où le paramètre d'instance est à non.
        """
        if not owner or not self._instance_enabled():
            return False
        return bool(self.env["bf.email.account"].sudo().search_count([
            ("user_id", "=", owner.id),
            ("active", "=", True),
            ("popup_mode", "!=", "none"),
        ]))

    # ------------------------------------------------------------------
    # Envoi
    # ------------------------------------------------------------------
    @api.model
    def _sendone(self, partner, payload):
        """Un message sur le canal du partenaire. Ne lève jamais.

        Envoyé dans la transaction, comme le fait le rappel d'agenda : une
        passe qui retombe ne doit avoir annoncé rien du tout.
        """
        try:
            self.env["bus.bus"].sudo()._sendone(
                partner, POPUP_CHANNEL, payload)
        except Exception:  # noqa: BLE001 - ne jamais casser l'ingestion
            _logger.warning(
                "bf.email popup: envoi bus en échec pour le partenaire %s",
                partner.id, exc_info=True,
            )

    @api.model
    def _mode_for(self, rec):
        """« aucune », « transient » ou « sticky » pour cette ligne-ci."""
        account = rec.account_id
        mode = account.popup_mode if account else "none"
        if mode not in ("transient", "sticky"):
            return "none"
        folders = account._popup_sticky_folder_set()
        if folders and (rec.imap_folder or "").strip().lower() in folders:
            return "sticky"
        return mode

    @api.model
    def _notify_new_emails(self, emails):
        """Annoncer aux propriétaires les courriels fraîchement entrés.

        Groupé par propriétaire, puis par persistance : les collants partent un
        par un tant qu'ils sont peu nombreux, les éphémères pareil, et chaque
        groupe bascule en résumé au-delà de ``BATCH_POPUP_MAX``. Un résumé
        garde la persistance de son groupe, de sorte qu'un lot arrivé dans un
        dossier suivi ne s'efface pas en silence.
        """
        if not self._instance_enabled():
            return
        by_owner = {}
        for rec in emails:
            if rec.direction != "in" or rec.is_handled or not rec.user_id:
                continue
            mode = self._mode_for(rec)
            if mode == "none":
                continue
            by_owner.setdefault(rec.user_id, {"sticky": [], "transient": []})
            by_owner[rec.user_id][mode].append(rec)

        for owner, groups in by_owner.items():
            partner = owner.partner_id
            if not partner:
                continue
            for mode in ("sticky", "transient"):
                group = groups[mode]
                if not group:
                    continue
                sticky = mode == "sticky"
                if len(group) > BATCH_POPUP_MAX:
                    self._sendone(partner, {
                        "kind": "batch",
                        "count": len(group),
                        "sticky": sticky,
                    })
                    continue
                # Le plus ancien d'abord : les toasts s'empilent dans l'ordre
                # d'arrivée, donc le plus récent doit partir en DERNIER pour
                # se retrouver en haut de la pile.
                group = sorted(group, key=lambda r: (r.date or r.create_date))
                for rec in group:
                    self._sendone(partner, {
                        "kind": "mail",
                        "email_id": rec.id,
                        "sticky": sticky,
                    })
