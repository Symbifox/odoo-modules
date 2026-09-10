"""Avis à l'arrivée d'un courriel, servi dans Odoo par le bus.

Second transport de la même nouvelle, à côté de ``push_transport.py`` qui, lui,
sort du navigateur et va au téléphone. Les deux partent du même point
d'ingestion — ``bf_email_mobile._sync_account`` — pour qu'ils ne divergent pas,
et chacun garde son propre interrupteur.

⚠️ La charge utile ne porte NI l'expéditeur NI l'objet, seulement des
identifiants. ``bus.bus._sendone`` diffuse au **partenaire**, un partenaire peut
porter plus d'un utilisateur, et les règles d'enregistrement de ``bf.email`` ne
sont jamais consultées par le bus. Le client reçoit donc l'id et relit la ligne
par l'ORM, qui, lui, applique les règles : une ligne qu'on n'a pas le droit de
lire ne produit tout simplement pas d'avis. C'est le même raisonnement que
``bf.email._broadcast_change``, et c'est la raison pour laquelle le canal
``bf_email/changed`` existant ne transporte qu'un ``reason``.

Sept niveaux de réglage, du plus large au plus fin :

- ``bf_email.popup_enabled`` (ir.config_parameter) — l'instance. Absent vaut
  **non** : un locataire qui reçoit ce code au prochain ``-u`` ne change pas de
  comportement. Chez Blue Fox le paramètre est posé à 1 à la main.
- ``bf.email.account.popup_mode`` — la personne. Le compte appartient déjà à
  quelqu'un (``user_id``), donc le réglage par compte est du même coup le
  réglage par personne, sans second champ à tenir d'accord.
- ``bf.email.account.popup_sticky_folders`` — le dossier. Ce qui atterrit là
  tient les trente secondes pleines ; le reste passe en huit.
- ``bf.email.account.popup_snooze_minutes`` — le report du bouton « Reporter ».
- ``bf.email.account.popup_skip_bulk`` — le genre. Un courriel portant
  ``List-Unsubscribe`` ou venu d'un domaine d'envoi connu (``is_bulk``) reste
  dans la boîte et ne fait pas surface. Mesuré sur une boîte réelle : un peu
  plus d'un entrant sur cinq porte ce signal. Le critère est l'en-tête et non
  la catégorie ``marketing``, qui se corrige à la main et se tromperait sur un
  vrai client.
- ``bf.email.account.popup_color`` — la teinte de la barre, pour reconnaître la
  boîte d'arrivée sans lire le nom du compte.
- ``bf.email.bf_no_popup`` — la ligne. Posé par une règle qui a reconnu un
  émetteur dont les avis n'ont jamais rien à dire.

Et par-dessus tous, le mode « ne pas déranger » (``bf.dnd``),
qui ne fait pas taire mais RETIENT : ce qui n'a pas fait surface est noté dans
``bf.dnd.held`` et rendu en un seul résumé à la fin du mode.

LE PLAFOND DE TRENTE SECONDES, ET SON RENVERSEMENT
--------------------------------------------------

⚠️ Le contrat a changé, et ce qui suit décrit ce qui reste vrai côté serveur.
La règle d'origine était « un avis n'occupe pas l'écran plus de trente
secondes, toutes fenêtres confondues » ; elle interdisait donc au survol de
geler le décompte, puisque geler c'est allonger. Arbitré depuis : **pointer
un avis est un geste délibéré**, il gèle et remet le décompte à neuf sans
limite, et le minuteur ne court que sur la tête de file.
Le plafond est désormais « ``ttl_ms`` par avis, quand c'est son tour », et il
est tenu par le client.

Ce que le serveur garde, et qui n'a rien à voir avec le survol :

- ``sent_ms`` — l'horloge du serveur au moment de l'envoi. Elle sert de **date
  de péremption à l'arrivée** : un avis reçu plus de ``ttl_ms`` après son envoi
  ne s'affiche jamais. C'est ce qui jette les rejeux du bus, et rien d'autre.
- ``ttl_ms`` — la durée accordée à cet avis-ci, plafonnée à
  ``POPUP_TTL_MAX_MS``. Le client la consomme quand l'avis atteint la tête de
  file, pas à l'affichage.

⚠️ Ce que le nouveau contrat abandonne, en toute connaissance : deux fenêtres
ouvertes n'éteignent plus le même avis au même instant. Chacune a sa propre
file et son propre survol, donc sa propre horloge. C'était le prix du gel au
survol ; le rejeu, lui, reste couvert par ``sent_ms``.

Le couple portait aussi ceci, qui ne bouge pas :

``bus.bus`` conserve ses messages **24 heures** (``bus.gc_retention_seconds``)
et les rejoue à la reconnexion, ``last_notification_id`` survivant en
localStorage. Un navigateur rouvert le lendemain matin recevait donc d'un coup
tous les avis de la veille. Avec l'échéance dans la charge utile, un avis
rejoué est arrivé expiré et ne s'affiche jamais.
"""
import logging
import time
from datetime import timezone

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

POPUP_CHANNEL = "bf_email/popup"
POPUP_ENABLED_PARAM = "bf_email.popup_enabled"
# Au-delà de ce nombre d'avis d'un même genre dans une seule passe, un résumé
# remplace la pile. Une reprise après panne ramène un lot entier d'un coup ;
# empiler cinquante toasts revient à n'en montrer aucun.
BATCH_POPUP_MAX = 5
TRUTHY = ("1", "true", "yes", "on")

# Le plafond, en millisecondes. Aucun avis ne vit plus longtemps que ça, quel
# que soit son mode et quel que soit le nombre de fenêtres ouvertes.
POPUP_TTL_MAX_MS = 30000
# Huit secondes pour l'éphémère plutôt que les quatre du défaut d'Odoo : trois
# boutons à lire et à viser, ça ne se fait pas en quatre secondes.
POPUP_TTL_MS = {"transient": 8000, "sticky": POPUP_TTL_MAX_MS}
# Identifiants joints à un résumé pour que le client puisse nommer les
# expéditeurs. Ce ne sont QUE des identifiants : c'est lui qui relit les lignes
# par l'ORM, donc les règles d'enregistrement s'appliquent comme ailleurs.
BATCH_PREVIEW_IDS = 8


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
    # Durée de vie
    # ------------------------------------------------------------------
    @api.model
    def _now_ms(self):
        """L'horloge murale du serveur, en millisecondes depuis l'époque.

        ``time.time()`` plutôt que ``fields.Datetime.now()`` : c'est la même
        base que le ``Date.now()`` du navigateur, donc la soustraction faite
        côté client a un sens sans conversion.
        """
        return int(time.time() * 1000)

    @api.model
    def _ttl_ms(self, mode):
        """Combien de temps cet avis-ci a le droit de rester à l'écran."""
        return min(POPUP_TTL_MS.get(mode, POPUP_TTL_MS["transient"]),
                   POPUP_TTL_MAX_MS)

    # ------------------------------------------------------------------
    # Envoi
    # ------------------------------------------------------------------
    @api.model
    def _sendone(self, partner, payload):
        """Un message sur le canal du partenaire. Ne lève jamais.

        Envoyé dans la transaction, comme le fait le rappel d'agenda : une
        passe qui retombe ne doit avoir annoncé rien du tout.

        L'horodatage est posé ICI, au plus près de l'envoi, plutôt qu'une fois
        pour toute la passe : un lot de cinquante lignes peut prendre quelques
        secondes à parcourir, et ce sont autant de secondes que le dernier avis
        perdrait sur son plafond.
        """
        payload = dict(payload, sent_ms=self._now_ms())
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
        """« aucune », « transient » ou « sticky » pour cette ligne-ci.

        ⚠️ ``bf_no_popup`` est posé par une règle (``bf.email.rule``), donc
        dans ``create()``, avant que ``_sync_account`` ne relève les lignes
        fraîches. C'est un « pas d'avis », pas un « traité » : la ligne reste
        dans la boîte, elle ne fait simplement pas surface. Cinquième niveau
        de réglage, le seul qui regarde le CONTENU.
        """
        if rec.bf_no_popup:
            return "none"
        account = rec.account_id
        mode = account.popup_mode if account else "none"
        if mode not in ("transient", "sticky"):
            return "none"
        # Le genre, avant le dossier : une infolettre tombée dans un dossier
        # suivi n'a pas plus besoin d'interrompre qu'ailleurs. ``is_bulk`` est
        # un signal d'EN-TÊTE (``List-Unsubscribe``, domaine d'envoi connu),
        # donc il ne se corrige pas à la main comme ``category`` — c'est
        # exactement pourquoi c'est lui qu'on lit.
        if rec.is_bulk and account.popup_skip_bulk:
            return "none"
        folders = account._popup_sticky_folder_set()
        if folders and (rec.imap_folder or "").strip().lower() in folders:
            return "sticky"
        return mode

    @api.model
    def _notify_seen(self, emails):
        """Dire aux autres fenêtres d'écarter ces avis-ci.

        Groupé par propriétaire : une ligne s'annonce au partenaire de son
        ``user_id``, jamais ailleurs. Le canal est le même que les avis, avec
        un ``kind`` distinct — un second canal voudrait un second abonnement,
        et le client n'en tirerait rien de plus.

        ⚠️ Pas de contrôle d'``_instance_enabled`` ici : écarter un avis déjà
        affiché doit marcher même si quelqu'un vient d'éteindre le paramètre
        d'instance. Un interrupteur qui empêche de FERMER serait pire que pas
        d'interrupteur.
        """
        by_owner = {}
        for rec in emails:
            if rec.user_id and rec.user_id.partner_id:
                by_owner.setdefault(rec.user_id.partner_id, []).append(rec.id)
        for partner, ids in by_owner.items():
            self._sendone(partner, {"kind": "seen", "email_ids": ids})

    @api.model
    def _notify_new_emails(self, emails, wake=False):
        """Annoncer aux propriétaires les courriels fraîchement entrés.

        Groupé par propriétaire, puis par persistance : les collants partent un
        par un tant qu'ils sont peu nombreux, les éphémères pareil, et chaque
        groupe bascule en résumé au-delà de ``BATCH_POPUP_MAX``. Un résumé
        garde la persistance de son groupe, de sorte qu'un lot arrivé dans un
        dossier suivi ne s'efface pas en silence.

        ``wake`` marque les avis d'un report échu (voir
        ``bf.email._cron_imap_mirror``). Le client s'en sert pour dire « report
        échu » plutôt que « nouveau courriel » : le geste attendu est le même,
        mais la personne, elle, a déjà vu ce courriel une fois.
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

        Dnd = self.env["bf.dnd"]
        for owner, groups in by_owner.items():
            partner = owner.partner_id
            if not partner:
                continue
            # ⚠️ La garde est posée ICI, pas dans `_mode_for` : ce qui est tu
            # doit être NOTÉ, pas oublié. Le résumé de sortie n'a rien d'autre
            # pour savoir ce qu'il rend.
            if Dnd._active_for(owner):
                Dnd._hold_mail(
                    owner, groups["sticky"] + groups["transient"])
                continue
            for mode in ("sticky", "transient"):
                group = groups[mode]
                if not group:
                    continue
                sticky = mode == "sticky"
                ttl_ms = self._ttl_ms(mode)
                if len(group) > BATCH_POPUP_MAX:
                    self._sendone(partner, {
                        "kind": "batch",
                        "count": len(group),
                        "sticky": sticky,
                        "ttl_ms": ttl_ms,
                        "wake": wake,
                        # Le client nomme les expéditeurs en relisant ces
                        # lignes-là ; il n'y en a pas plus que ce qu'un résumé
                        # peut citer.
                        "email_ids": [rec.id for rec in group[:BATCH_PREVIEW_IDS]],
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
                        "ttl_ms": ttl_ms,
                        "wake": wake,
                    })


class BfEmail(models.Model):
    """Les deux gestes que l'avis pose sur la ligne qu'il annonce.

    Ils vivent ici plutôt qu'avec les autres actions de ``bf.email`` parce
    qu'ils appartiennent à ce transport-ci : ce sont ses boutons.

    ⚠️ Sans préfixe ``_``, ces méthodes sont appelables par XML-RPC comme par
    le client web, donc par n'importe quel compte connecté avec n'importe quel
    identifiant. C'est ``_mobile_browse`` qui refuse la ligne d'autrui, et il
    est réutilisé tel quel : un second contrôle écrit à part finirait par dire
    autre chose que le premier.

    Le travail lui-même est délégué aux méthodes du téléphone, pour la même
    raison. « Traité » doit vouloir dire exactement la même chose dans l'avis,
    dans la boîte de réception et dans l'app — y compris la recopie IMAP vers
    ``Archives/{AAAA}`` et le retrait de la notification déjà posée sur le
    téléphone.
    """
    _inherit = "bf.email"

    # ⚠️ Un champ à part, et pas une réutilisation d'`is_handled`. « Traité »
    # sort la ligne de la boîte et recopie le message vers les archives IMAP ;
    # ici on veut juste qu'elle n'interrompe pas. Le battement d'un service de
    # surveillance reste à lire, il ne mérite simplement pas de surgir sur un
    # écran partagé. Mesuré le 2026-09-09 : sur 107 courriels annoncés en
    # 24 h, 19 venaient d'un seul service qui alternait « Warning » et
    # « Recovered ».
    bf_no_popup = fields.Boolean(
        string="Pas d'avis à l'écran",
        default=False,
        index=True,
        help="La ligne reste dans la boîte de réception mais ne produit aucun "
             "avis. Posé par une règle automatique, ou à la main sur une "
             "ligne.",
    )

    # La teinte de la barre, lue par le client dans la MÊME lecture ORM que
    # l'objet et l'expéditeur. Un `related` plutôt qu'une clé de plus dans la
    # charge utile : le bus ne consulte aucune règle d'enregistrement, et un
    # réglage d'affichage n'est pas une raison de rouvrir ce chemin-là.
    popup_color = fields.Selection(
        related="account_id.popup_color",
        string="Couleur de l'avis",
        readonly=True,
    )

    @api.model
    def popup_mark_seen(self, email_ids):
        """« Vu » : écarter l'avis partout, sans toucher au message.

        Ce que le X du coin ne sait pas faire. Un avis s'affiche dans TOUTES
        les fenêtres ouvertes de la personne — le bus diffuse au partenaire —
        et le X n'en ferme qu'une. « Vu » repasse par le serveur pour que les
        autres l'entendent.

        ⚠️ Aucune écriture sur la ligne : ni ``is_handled``, ni ``status``, ni
        recopie IMAP. La ligne reste exactement où elle était, non lue et non
        traitée. Rien à persister non plus pour qu'elle ne revienne pas :
        ``_sync_account`` n'annonce que les lignes dont l'``id`` dépasse le
        repère de la passe, donc un courriel n'est annoncé qu'une fois. La
        seule réannonce possible est le réveil d'un report, et « Vu » ne
        reporte rien.
        """
        if isinstance(email_ids, int):
            email_ids = [email_ids]
        email_ids = list(email_ids or [])
        # ⚠️ Le contrôle vient AVANT ``_mobile_browse``, qui lève un
        # ``UserError`` sur une liste vide. Écarter un avis qui ne porte aucune
        # ligne — un résumé de lot — n'est pas une erreur, c'est un geste qui
        # n'a rien à demander au serveur.
        if not email_ids:
            return {"email_ids": [], "seen": True}
        records = self._mobile_browse(email_ids)
        self.env["bf.email.popup"]._notify_seen(records)
        return {"email_ids": records.ids, "seen": True}

    @api.model
    def popup_snooze(self, email_id, minutes=None):
        """Reporter depuis l'avis, et rendre l'échéance pour l'accusé.

        ``minutes`` absent vaut le réglage du compte
        (``popup_snooze_minutes``). Le courriel sort de la boîte comme le fait
        « Reporter » ailleurs, et le cron de miroir IMAP le réveille à
        l'échéance — l'avis repart alors avec lui, à cinq minutes près.
        """
        records = self._mobile_browse([email_id])
        records.ensure_one()
        if minutes is None:
            minutes = records.account_id.popup_snooze_minutes or 60
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            minutes = 60
        # Borné des deux côtés : zéro rendrait une échéance déjà passée, que
        # ``mobile_snooze`` refuse, et un nombre absurde ferait disparaître le
        # courriel pour des années sur un clic.
        minutes = max(1, min(minutes, 60 * 24 * 30))
        until_dt = fields.Datetime.add(fields.Datetime.now(), minutes=minutes)
        # ⚠️ ``datetime.timestamp()`` sur un objet NAÏF lit l'heure comme
        # locale ; les datetimes d'Odoo sont en UTC. Sans le ``tzinfo``, un
        # serveur réglé sur Montréal décalerait chaque report de quatre heures,
        # et le décalage suivrait l'heure avancée sans que rien ne le dise.
        until_ms = int(until_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        self.mobile_snooze([records.id], until_ms)
        return {
            "email_id": records.id,
            "minutes": minutes,
            "until": fields.Datetime.to_string(until_dt),
        }

    @api.model
    def popup_mark_handled(self, email_id):
        """« Traité » depuis l'avis — le même geste que dans la boîte."""
        records = self._mobile_browse([email_id])
        records.ensure_one()
        self.mobile_set_handled([records.id], handled=True)
        return {"email_id": records.id, "handled": True}
