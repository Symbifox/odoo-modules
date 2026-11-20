"""L'avis du casier : écrit d'abord, compris ensuite.

## Pourquoi en deux temps

Expedibox appelle une adresse et attend une réponse. Deux pièges se referment
si on fait tout dans l'appel :

1. **la latence est la nôtre, l'attente est la leur.** Relire la réservation
   chez Expedibox prend un aller-retour ; le faire pendant leur appel fait
   pendre leur file sur notre réseau ;
2. **un 500 perd l'avis.** S'ils ne rejouent pas — et leur spécification ne dit
   rien là-dessus —, un dépôt n'est jamais consigné et le résident n'est jamais
   prévenu. Silencieusement.

Donc la route écrit une ligne ici, répond 200, et c'est un passage planifié qui
relit la réservation chez Expedibox et agit. Rien ne se perd, rien ne pend.

## Ce que cette ligne porte, et ce qu'elle ne portera jamais

Elle porte une référence de réservation, un type d'événement, deux horodatages
et un identifiant de casier. Le corps reçu n'est **pas** conservé : il nomme le
destinataire, cite son courriel et son mobile, et porte les codes de dépôt et
de retrait, les trois images QR et le code d'ouverture de la porte. Le peu
qu'on en lit est lu, puis jeté.

🔴 **Le corps reçu ne fait pas foi non plus.** L'avis n'est pas signé (voir le
connecteur) : la ligne écrite dit « Expedibox PRÉTEND qu'il s'est passé ceci ».
Ce qui décide, c'est la relecture de la réservation avec notre jeton
d'application. Un avis dont la réservation ne confirme rien ne produit aucune
fiche de colis.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.tools import config

from . import expedibox_reservations as expedibox
from odoo.addons.bf_property_access_expedibox.models import expedibox_api as api_expedibox

_logger = logging.getLogger(__name__)

EVENTS = [
    ("new_reservation", "Réservation créée"),
    ("deposit", "Colis déposé"),
    ("pickup", "Colis retiré"),
    ("reminder", "Rappel"),
    ("reservation_ended", "Réservation terminée"),
    ("deposit_expired", "Dépôt échu"),
    ("cancelled", "Réservation annulée"),
    ("deleted", "Réservation supprimée"),
    ("other", "Autre"),
]
KNOWN_EVENTS = tuple(code for code, _label in EVENTS if code != "other")
# Les états d'un droit dont le résident est au casier (le même jeu que
# l'inscription : un retrait à faire en est un, la personne y est encore).
HELD_STATES = ("awaiting_consent", "to_issue", "issued", "to_remove")
# Au-delà, l'avis revient au responsable : si Expedibox ne confirme pas après
# cinq passages, ce n'est plus une panne, c'est quelque chose à regarder.
MAX_ATTEMPTS = 5
# Le journal technique des avis : court. Il ne sert qu'à prouver ce qui est
# arrivé et à rejouer ce qui a manqué.
RETENTION_DAYS = 90
# Ce que le casier écrit en texte libre, et qui vient d'une application de
# livreur : borné avant d'entrer dans la base.
MAX_FREE_TEXT = 120
IDENTIFIER = re.compile(r"^sfx-([0-9a-f]{1,8})-p(\d+)-pt(\d+)$")
# Les bornes d'un horodatage crédible.
MAX_PAST_DAYS, MAX_FUTURE_DAYS = 365, 2


def _clean(text):
    """Un texte venu d'ailleurs : sans caractère de contrôle, borné, ou rien."""
    if not isinstance(text, str):
        return False
    text = " ".join(text.split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:MAX_FREE_TEXT] or False


def parse_instant(value):
    """Un horodatage ISO 8601 d'Expedibox vers un Datetime naïf UTC, ou None.

    ⚠️ Leur ``timestamp`` porte un décalage (``Z`` ou ``+00:00``) et Odoo
    stocke du naïf UTC : sans la conversion, un avis du matin se rangeait à
    l'heure du serveur et le journal mentait de cinq heures.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(microsecond=0)
    else:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    # ⚠️ Un horodatage forgé (ou bogué) datait un colis en 2999 et l'épinglait
    # en tête du journal, portail de la résidente compris. Hors de cette
    # fenêtre, ce n'est pas un instant, c'est une erreur.
    maintenant = datetime.utcnow()
    if not (maintenant - timedelta(days=MAX_PAST_DAYS)
            <= moment <= maintenant + timedelta(days=MAX_FUTURE_DAYS)):
        return None
    return moment


class BfPropertyParcelExpediboxEvent(models.Model):
    _name = "bf.property.parcel.expedibox.event"
    # 🔴 `mail.activity.mixin` n'est pas un ornement : sans lui,
    # `activity_schedule` n'existe pas, chaque remise au responsable levait une
    # AttributeError avalée par la boucle, et un avis qui attendait une
    # personne tournait en rond sans jamais l'atteindre (essais du 2026-11-19).
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Avis d'un casier Expedibox"
    _order = "received_at desc, id desc"
    _rec_name = "reservation_ref"

    connector_id = fields.Many2one(
        "bf.property.access.connector", string="Connecteur", required=True,
        ondelete="cascade", index=True, readonly=True,
    )
    company_id = fields.Many2one(
        related="connector_id.company_id", store=True, string="Société"
    )
    reservation_ref = fields.Char(
        string="Réservation", required=True, index=True, readonly=True,
        help="La référence que porte la réservation chez Expedibox.",
    )
    event = fields.Selection(
        EVENTS, string="Événement", required=True, readonly=True, index=True
    )
    event_label = fields.Char(
        string="Événement reçu", readonly=True,
        help="Ce qu'Expedibox a nommé, quand ce n'est pas un des événements "
             "que ce module connaît. Un vocabulaire qui s'enrichit chez eux se "
             "lit ici au lieu de se perdre.",
    )
    occurred_at = fields.Datetime(
        string="Survenu le", required=True, readonly=True,
        help="L'heure qu'Expedibox donne à l'événement (UTC).",
    )
    received_at = fields.Datetime(
        string="Reçu le", required=True, readonly=True,
        default=fields.Datetime.now,
    )
    locker_ref = fields.Char(string="Casier", readonly=True, index=True)
    point_id = fields.Many2one(
        "bf.property.access.point", string="Point d'accès", readonly=True,
        ondelete="set null",
    )
    partner_id = fields.Many2one(
        "res.partner", string="Destinataire", readonly=True, ondelete="set null"
    )
    parcel_id = fields.Many2one(
        "bf.property.parcel", string="Colis", readonly=True, ondelete="set null"
    )
    state = fields.Selection(
        [
            ("received", "Reçu"),
            ("done", "Traité"),
            ("ignored", "Sans effet"),
            ("handed", "Remis au responsable"),
        ],
        string="État", default="received", required=True, readonly=True, index=True,
    )
    attempts = fields.Integer(string="Essais", default=0, readonly=True)
    message = fields.Text(string="Ce qui s'est passé", readonly=True)

    _sql_constraints = [
        # Un rejeu du MÊME avis (même réservation, même événement, même heure)
        # ne crée pas une seconde ligne, donc ne produit ni second colis ni
        # second avis au résident. Deux dépôts successifs dans la même
        # réservation, eux, n'ont pas la même heure : ils passent tous les deux.
        ("uniq_notice",
         "unique(connector_id, reservation_ref, event, occurred_at)",
         "Cet avis d'Expedibox est déjà enregistré."),
    ]

    # ── Réception ────────────────────────────────────────────────────────

    @api.model
    def _bf_receive(self, connector, event, occurred_at, reservation_ref, locker_ref):
        """Écrit l'avis. Rend la ligne, ou celle qui existait déjà.

        Appelée par la route, donc sous l'usager public : tout est en ``sudo``,
        et aucune valeur ne vient d'ailleurs que des quatre arguments.
        """
        values = {
            "connector_id": connector.id,
            "event": event if event in KNOWN_EVENTS else "other",
            "event_label": False if event in KNOWN_EVENTS else _clean(event),
            "occurred_at": occurred_at,
            "reservation_ref": reservation_ref,
            "locker_ref": locker_ref,
        }
        cle = [
            ("connector_id", "=", connector.id),
            ("reservation_ref", "=", reservation_ref),
            ("event", "=", values["event"]),
            ("occurred_at", "=", occurred_at),
        ]
        existing = self.sudo().search(cle, limit=1)
        if existing:
            return existing, False
        try:
            # 🔴 Deux rejeux simultanés d'Expedibox passaient tous deux le
            # `search` et le second mourait sur la contrainte d'unicité : 500
            # rendu à Expedibox, là où l'idempotence promettait un 200. Le
            # point de sauvegarde rend la course inoffensive.
            with self.env.cr.savepoint():
                return self.sudo().create(values), True
        except IntegrityError:
            self.env.cr.flush()
            return self.sudo().search(cle, limit=1), False

    @api.model
    def _bf_trigger_soon(self):
        """Demande au passage planifié de tourner tout de suite.

        Sous l'usager public, ``env.ref`` n'a pas le droit de lire
        ``ir.model.data`` : la recherche passe par ``sudo``.
        """
        cron_id = self.env["ir.model.data"].sudo()._xmlid_to_res_id(
            "bf_property_parcel_expedibox.cron_parcel_expedibox_notices",
            raise_if_not_found=False,
        )
        if cron_id:
            self.env["ir.cron"].sudo().browse(cron_id)._trigger()

    @api.model
    def _bf_recent_count(self, connector, seconds=60):
        """Combien d'avis ce connecteur a reçus dans la dernière minute.

        ⚠️ L'adresse est publique : son secret la protège d'un visiteur, pas
        d'un secret qui aurait fui. Un plafond empêche qu'une boucle remplisse
        la base avant que quiconque s'en aperçoive.
        """
        since = fields.Datetime.now() - timedelta(seconds=seconds)
        return self.sudo().search_count([
            ("connector_id", "=", connector.id),
            ("received_at", ">=", since),
        ])

    # ── Traitement ───────────────────────────────────────────────────────

    @api.model
    def _cron_notices(self, limit=200):
        """Relit chez Expedibox ce qui est arrivé, et agit.

        🔴 Un point de sauvegarde par avis : un avis qui échoue ne doit pas
        emporter ceux qui l'ont précédé dans la même passe. Et la trace de
        l'échec s'écrit APRÈS le retour en arrière, sinon elle serait annulée
        avec lui.
        """
        events = self.sudo().search(
            [("state", "=", "received"), ("attempts", "<", MAX_ATTEMPTS)],
            order="received_at asc, id asc", limit=limit,
        )
        done = 0
        for event in events:
            failure = None
            try:
                with self.env.cr.savepoint():
                    event._process_one()
                done += 1
            except api_expedibox.ExpediboxError as exc:
                failure = str(exc)
            except Exception as exc:  # noqa: BLE001 — jamais avalé : consigné
                # 🔴 Ni le message de l'exception au champ, ni au journal : une
                # ValidationError du journal des colis NOMME la personne, et ce
                # champ est lu par la Consultation (relecture du 2026-11-19).
                # Le type suffit à qualifier, l'id suffit à retrouver.
                _logger.error("Avis Expedibox %s : échec inattendu (%s)",
                              event.id, type(exc).__name__)
                failure = _("Échec inattendu du traitement (%s). Le détail est "
                            "au journal du serveur.") % type(exc).__name__
            if failure is None:
                # 🔴 Deux cents avis à trente secondes d'attente tenaient une
                # seule transaction jusqu'à cent minutes, sur l'un des deux
                # fils de cron. Chaque avis se clôt pour lui-même.
                if not (config["test_enable"] or self.env.registry.in_test_mode()):
                    self.env.cr.commit()
                continue
            attempts = event.attempts + 1
            event.write({"attempts": attempts, "message": failure[:2000]})
            if attempts >= MAX_ATTEMPTS:
                event._hand_over(_(
                    "Expedibox n'a pas confirmé cet avis après %(n)s essais : "
                    "%(why)s") % {"n": attempts, "why": failure})
            if not (config["test_enable"] or self.env.registry.in_test_mode()):
                self.env.cr.commit()
        return done

    def _process_one(self):
        """Un avis : relire, confirmer, rapprocher, agir.

        🔴 **Le verbe aussi vient de la relecture.** La première version ne
        relisait que pour savoir si la réservation EXISTE, puis agissait sur le
        mot reçu dans un corps non signé : avec l'adresse d'écoute, on pouvait
        donc marquer « remis » un colis encore dans le casier, ou faire
        consigner un dépôt qui n'a jamais eu lieu (relecture adverse du
        2026-11-19). Le corps ne sert plus qu'à nommer la réservation à relire ;
        ce sont `deposit_at`, `pickup_at` et `deposit_expired`, rendus par
        Expedibox, qui disent ce qui s'est passé et quand.
        """
        self.ensure_one()
        connector = self.connector_id.sudo()
        token = connector.expedibox_app_token
        if not token:
            return self._hand_over(_(
                "Le connecteur « %s » n'a pas de jeton d'application : l'avis "
                "ne peut pas être confirmé chez Expedibox.") % connector.name)
        status, reservation = expedibox.get_reservation(token, self.reservation_ref)
        if status in (404, 410) or (status == 200 and not reservation):
            return self._ignore(_(
                "Expedibox ne connaît plus cette réservation (%s) : aucun colis "
                "n'est consigné.") % status)
        if status != 200:
            # 401, 403, 400 : la configuration ou le compte, pas l'avis.
            return self._hand_over(_(
                "Expedibox a refusé la relecture de la réservation (code %s).")
                % status)
        locker = _clean(reservation.get("locker_id"))
        # ⚠️ Le casier retenu est celui de la RELECTURE, jamais celui du corps :
        # il est recopié dans « où il est rangé », que la résidente lit au
        # portail.
        if locker and locker != self.locker_ref:
            self.sudo().write({"locker_ref": locker})
        point = self._point_for(locker)
        if not point:
            return self._hand_over(_(
                "Un casier qu'aucun point d'accès ne réclame (%s) a envoyé un "
                "avis. Soit le point a été archivé, soit ce casier est géré "
                "ailleurs.") % (locker or _("sans identifiant")), to_connector=True)
        self.point_id = point.id
        geste, quand = self._confirmed(reservation)
        if not geste:
            # Expedibox ne confirme rien : l'avis est consigné et sans effet.
            return self._ignore(_(
                "Expedibox ne confirme ni dépôt, ni retrait, ni fin pour cette "
                "réservation : aucun effet."))
        if not point.active:
            return self._hand_over(_(
                "Le point d'accès « %s » est archivé : son casier envoie "
                "encore des avis. À consigner à la main, ou à débrancher au "
                "portail d'Expedibox.") % point.display_name)
        partner = self._partner_for(point, reservation, token)
        if not partner:
            # ⛔ La réservation NOMME la personne, avec son courriel et son
            # mobile. Si ce logiciel ne l'a pas inscrite, il n'a aucune raison
            # de garder son nom : la remise ne nomme que le casier et la porte.
            #
            # 🔴 Et elle n'a lieu que pour un geste qui aurait produit quelque
            # chose : un immeuble dont vingt résidents sont inscrits à la main
            # au casier produisait cinq activités par colis, dont une pour un
            # RETRAIT qui disait « un dépôt… ne correspond à aucun résident ».
            if geste == "deposit":
                return self._hand_over(_(
                    "Un dépôt au casier %(locker)s (porte %(door)s) ne "
                    "correspond à aucun résident inscrit par ce logiciel. Il a "
                    "été inscrit à la main, ou par un autre logiciel : le colis "
                    "est à consigner à la main si l'organisation le veut.")
                    % {"locker": locker or _("inconnu"),
                       "door": _clean(reservation.get("door")) or _("inconnue")})
            return self._ignore(_(
                "Réservation d'un destinataire que ce logiciel n'a pas inscrit : "
                "aucun effet."))
        self.partner_id = partner.id
        return self._apply(geste, quand, point, partner, reservation)

    def _confirmed(self, reservation):
        """Ce qu'Expedibox CONFIRME, et quand. Rend (geste, instant) ou (None, None).

        L'ordre compte : un retrait confirmé l'emporte sur le dépôt qui l'a
        précédé (une réservation retirée porte les deux dates), et une fin de
        dépôt ne vaut que si rien n'a été retiré.
        """
        depose = parse_instant(reservation.get("deposit_at"))
        retire = parse_instant(reservation.get("pickup_at"))
        if retire:
            return "pickup", retire
        if reservation.get("deposit_expired") is True and depose:
            return "expired", depose
        if depose:
            return "deposit", depose
        return None, None

    def _point_for(self, locker):
        """Le point d'accès de ce casier, sur CE connecteur. Archivé compris."""
        if not locker:
            return self.env["bf.property.access.point"].browse()
        return self.env["bf.property.access.point"].sudo().with_context(
            active_test=False
        ).search([
            ("connector_id", "=", self.connector_id.id),
            ("expedibox_locker_id", "=", locker),
        ], limit=1)

    def _partner_for(self, point, reservation, token):
        """La personne, par NOTRE identifiant de rapprochement, ou rien.

        🔴 Jamais par le nom, jamais par le courriel, jamais par le logement :
        ce sont des champs que n'importe qui peut remplir au casier, et s'en
        servir pour désigner un résident ferait d'un avis non signé une façon
        d'écrire dans le journal de quelqu'un d'autre.
        """
        identifier = reservation.get("recipient_custom_identifier")
        if not identifier and reservation.get("recipient_id"):
            status, identifier = expedibox.get_recipient(
                token, reservation["recipient_id"])
            if status != 200:
                identifier = None
        return self._partner_from_identifier(identifier, point)

    def _partner_from_identifier(self, identifier, point):
        empty = self.env["res.partner"].browse()
        if not isinstance(identifier, str):
            return empty
        match = IDENTIFIER.match(identifier.strip())
        if not match:
            return empty
        prefix = self.env["bf.property.access.grant"]._expedibox_prefix()
        if not identifier.strip().startswith(prefix):
            # Une autre base Symbifox branchée sur le même casier : ses
            # destinataires ne sont pas les nôtres.
            return empty
        partner_id, point_id = int(match.group(2)), int(match.group(3))
        if point_id != point.id:
            return empty
        partner = self.env["res.partner"].sudo().browse(partner_id).exists()
        if not partner:
            return empty
        # La personne doit encore être un résident de ce point : un identifiant
        # resté au casier après un départ ne rouvre pas un journal.
        # 🔴 Un droit RETIRÉ n'est pas un droit : sans ce filtre, une personne
        # partie était reconnue, la création du colis levait « n'a aucune
        # fraction dans ce syndicat », et son NOM finissait dans le message de
        # l'avis et au journal du serveur (relecture adverse du 2026-11-19).
        grant = self.env["bf.property.access.grant"].sudo().search([
            ("point_id", "=", point.id),
            ("partner_id", "=", partner.id),
            ("state", "in", HELD_STATES),
        ], limit=1)
        return partner if grant else empty

    # ── Effets ───────────────────────────────────────────────────────────

    def _apply(self, geste, quand, point, partner, reservation):
        self.ensure_one()
        if geste == "deposit":
            return self._apply_deposit(quand, point, partner, reservation)
        parcel = self._parcel_for()
        if geste == "pickup":
            if not parcel:
                return self._ignore(_(
                    "Retrait confirmé pour une réservation dont aucun colis "
                    "n'a été consigné ici."))
            return self._done(parcel._bf_expedibox_collected(quand), parcel=parcel)
        if geste == "expired":
            if not parcel or parcel.state != "held":
                return self._ignore(_("Rien en dépôt pour cette réservation."))
            parcel._message_log(body=_(
                "Expedibox déclare ce dépôt terminé ou échu : le casier le "
                "remet au livreur. L'état du colis n'est pas changé ici — "
                "c'est le concierge qui constate ce qui en est fait."))
            self._hand_to(point.user_id, _(
                "Le colis %(parcel)s n'a pas été retiré du casier "
                "%(locker)s : Expedibox déclare le dépôt terminé. À constater "
                "(remis, retourné à l'expéditeur).")
                % {"parcel": parcel.name, "locker": self.locker_ref or ""})
            return self._set("handed", _("Dépôt échu : remis au responsable du point."),
                             parcel=parcel)
        return self._ignore(_("Aucun effet pour cet événement."))

    def _apply_deposit(self, quand, point, partner, reservation):
        parcel = self._parcel_for()
        if parcel:
            # Idempotence : un rejeu, ou un second avis pour le même dépôt.
            return self._ignore(_("Ce dépôt est déjà consigné (%s).") % parcel.name,
                                parcel=parcel)
        suivi = self._announced_parcel(partner, reservation)
        if suivi:
            # 🔴 La résidente avait annoncé CE colis au portail, et le livreur
            # l'a déposé au casier : une seule fiche, pas deux. Sans cela, le
            # registre portait deux colis pour un, le suivi faisait partir un
            # second avis à la livraison, et l'annonce restait « en dépôt »
            # pour toujours — aucune purge ne prend une fiche ouverte
            # (relecture adverse, 2e tour).
            return self._adopt_announced(suivi, quand, point, reservation)
        grant = self.env["bf.property.access.grant"].sudo().search([
            ("point_id", "=", point.id),
            ("partner_id", "=", partner.id),
            ("state", "in", HELD_STATES),
        ], limit=1)
        door = reservation.get("door")
        where = _("Casier %(locker)s, porte %(door)s") % {
            "locker": self.locker_ref or point.expedibox_locker_id or "",
            "door": door or _("inconnue"),
        } if (self.locker_ref or point.expedibox_locker_id) else False
        values = {
            "organisation_id": point.organisation_id.id,
            "building_id": (grant.building_id or point.building_id).id or False,
            "unit_id": (grant.unit_id or point.unit_id).id or False,
            "partner_id": partner.id,
            "carrier": _clean(reservation.get("carrier")),
            "reference": _clean(reservation.get("tracking_number")),
            "parcel_type": "parcel",
            "storage_location": where,
            # L'heure du dépôt telle qu'Expedibox la confirme, jamais celle
            # qu'un corps non signé annonce.
            "date_received": quand,
            "state": "held",
            "expedibox_reservation_ref": self.reservation_ref,
        }
        reason = False
        # ⚠️ Deux conditions, pas une : le réglage du connecteur dit ce que le
        # casier fait d'habitude, mais la réservation, elle, dit ce qu'il a fait
        # POUR CE COLIS (`notify_receiver`). Une réservation déposée sans avis
        # sur un connecteur « le casier avise » ne prévenait personne.
        if (self.connector_id.expedibox_locker_notifies
                and reservation.get("notify_receiver") is not False):
            reason = _("le casier Expedibox avise lui-même le destinataire")
        parcel = self.env["bf.property.parcel"].sudo().with_context(
            # Le texto de `bf_property_sms` est sauté quand le casier a déjà
            # écrit à la personne : un seul avis par colis.
            bf_parcel_notice_elsewhere=reason,
        ).create(values)
        parcel._message_log(body=_(
            "Colis consigné depuis l'avis de dépôt du casier Expedibox."))
        return self._done(_("Colis %s consigné.") % parcel.name, parcel=parcel)

    def _announced_parcel(self, partner, reservation):
        """La fiche que la résidente a annoncée pour ce même colis, ou rien.

        Rapprochement par le numéro de suivi, pour la même personne et la même
        société : c'est le seul lien sûr entre ce que le marchand lui a écrit
        et ce que le livreur a tapé au casier.
        """
        Parcel = self.env["bf.property.parcel"]
        numero = _clean(reservation.get("tracking_number"))
        if not numero or "tracking_number" not in Parcel._fields:
            return Parcel.browse()
        return Parcel.sudo().search([
            ("state", "=", "expected"),
            ("partner_id", "=", partner.id),
            ("tracking_number", "=", numero),
            ("company_id", "=", self.company_id.id),
        ], limit=1)

    def _adopt_announced(self, parcel, quand, point, reservation):
        """L'annonce devient le colis déposé : une fiche, un avis."""
        door = _clean(reservation.get("door"))
        locker = self.locker_ref or point.expedibox_locker_id or ""
        values = {
            "expedibox_reservation_ref": self.reservation_ref,
            "state": "held",
            "date_received": quand,
            "carrier": _clean(reservation.get("carrier")) or parcel.carrier,
        }
        if locker:
            values["storage_location"] = _("Casier %(locker)s, porte %(door)s") % {
                "locker": locker, "door": door or _("inconnue")}
        if "tracking_notified" in parcel._fields:
            # Le casier a écrit au destinataire (ou le texto du journal est
            # parti juste ici) : le suivi n'a plus à aviser, ni à interroger.
            values["tracking_notified"] = True
            values["tracking_next_check"] = False
        parcel.sudo().write(values)
        parcel._message_log(body=_(
            "Le colis annoncé par le destinataire a été déposé au casier "
            "Expedibox : c'est la même fiche, elle passe en dépôt."))
        return self._done(_("Colis annoncé %s retrouvé au casier.") % parcel.name,
                          parcel=parcel)

    def _parcel_for(self):
        return self.env["bf.property.parcel"].sudo().search([
            ("expedibox_reservation_ref", "=", self.reservation_ref),
            ("organisation_id.company_id", "=", self.company_id.id),
        ], limit=1)

    # ── Verdicts ─────────────────────────────────────────────────────────

    def _set(self, state, message, parcel=None):
        values = {"state": state, "message": message}
        if parcel is not None:
            values["parcel_id"] = parcel.id if parcel else False
        self.sudo().write(values)
        return True

    def _done(self, message, parcel=None):
        return self._set("done", message, parcel=parcel)

    def _ignore(self, message, parcel=None):
        return self._set("ignored", message, parcel=parcel)

    def _hand_over(self, message, to_connector=False):
        """Remet l'avis à une personne, avec la raison. Jamais de silence.

        🔴 Si personne ne peut le recevoir (responsable supprimé ou archivé),
        l'avis **reste à traiter** : le marquer « remis » alors que personne
        n'a rien reçu ferait mentir l'état, et la purge l'emporterait trois
        mois plus tard.
        """
        self.ensure_one()
        # Le cache de l'ORM survit au retour en arrière : on relit le point
        # STOCKÉ, jamais celui qu'un essai précédent a posé en mémoire.
        point = self.sudo().read(["point_id"])[0]["point_id"]
        user = (self.connector_id.expedibox_notice_user_id
                if to_connector or not point
                else self.point_id.user_id)
        if not (user and user.active):
            user = self.connector_id.expedibox_notice_user_id
        if not self._hand_to(user, message):
            return self._set("received", _(
                "%s (personne à qui le remettre : le responsable des avis du "
                "connecteur est absent ou archivé)") % message)
        return self._set("handed", message)

    def _hand_to(self, user, message):
        if not (user and user.active):
            _logger.error("Avis Expedibox %s : aucun responsable actif à qui "
                          "le remettre", self.id)
            return False
        self.sudo().activity_schedule(
            "mail.mail_activity_data_todo",
            date_deadline=fields.Date.context_today(self),
            summary=_("Avis d'un casier Expedibox à regarder"),
            note=message,
            user_id=user.id,
        )
        return True

    # ── Purge ────────────────────────────────────────────────────────────

    @api.model
    def _cron_purge(self):
        """Le journal technique ne traîne pas.

        Un avis clos part après 90 jours (RETENTION_DAYS), ou plus tôt si l'organisation du
        point a choisi une conservation plus courte pour ses journaux. Les
        avis encore à traiter restent, quelle que soit leur date : les
        supprimer, ce serait perdre un colis.
        """
        now = fields.Datetime.now()
        # 🔴 Un avis « remis au responsable » dont l'activité est ENCORE
        # ouverte n'est pas clos : l'effacer emporterait l'activité avec lui
        # (le mixin supprime les activités de l'enregistrement effacé) et la
        # tâche disparaîtrait de la liste du responsable sans laisser de trace.
        closed = ["&", ("state", "in", ("done", "ignored", "handed")),
                  ("activity_ids", "=", False)]
        purged = self.sudo().search(
            closed + [("received_at", "<", now - timedelta(days=RETENTION_DAYS))])

        organisations = self.env["bf.property.organisation"].sudo().search(
            [("log_retention_days", ">", 0), ("log_retention_days", "<", RETENTION_DAYS)])
        for organisation in organisations:
            purged |= self.sudo().search(closed + [
                ("point_id.organisation_id", "=", organisation.id),
                ("received_at", "<", now - timedelta(
                    days=organisation.log_retention_days)),
            ])
        count = len(purged)
        purged.unlink()
        return count
