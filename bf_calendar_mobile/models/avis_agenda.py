"""L'avis « agenda » : une rencontre a changé, le téléphone relit.

L'agenda de l'appli mobile ne se relisait qu'au retour à l'écran et à la minute ;
une rencontre créée, déplacée ou acceptée sur le poste n'y paraissait qu'au
battement suivant.

🔴 **L'avis ne porte RIEN** : ni titre, ni heure, ni identifiant. Il réveille, et
le téléphone relit par ses routes authentifiées. Même règle que le réveil du
coupe-circuit au départ d'un employé : un endpoint UnifiedPush est une URL, et ce qui y passe
ne doit rien apprendre à qui la connaît.

⚠️ **Une fois par personne et par transaction, au plus une fois par
[INTERVALLE_S] secondes** : la synchro Nextcloud réécrit des dizaines
d'événements d'un coup, et chacun pousserait sinon. Les personnes visées sont
rassemblées dans ``cr.postcommit.data`` et l'envoi part APRÈS la validation,
dans un fil : rien ne part pour une
écriture annulée, et l'écriture n'attend pas le réseau.

Seuls les appareils en Mobile 3.22.0 ou plus le reçoivent : un plus ancien ne
connaît pas ce type, et il est annoncé « toujours chiffré ».
"""
import logging
import threading
import time

import odoo.modules.module

from odoo import SUPERUSER_ID, api, models

from odoo.addons.bf_email_management.models.push_transport import _truthy

_logger = logging.getLogger(__name__)

TYPE_AVIS = "agenda"
VERSION_MIN = (3, 22, 0)
# 18.0.3.8.1 : l'avis a SON interrupteur. Il ne montre rien au téléphone (il ne fait
# que relire), donc il n'a pas à suivre `bf_email.push_enabled`, qu'une instance éteint
# quand ses courriels sont déjà notifiés par un autre canal. Vide : il suit
# l'interrupteur des courriels, comme en 3.8.0.
PARAM_AVIS = "bf_calendar_mobile.avis_agenda"
INTERVALLE_S = 20
CLE_POSTCOMMIT = "bf_calendar_mobile.avis_agenda"

# Ce qui change ce que l'agenda du téléphone montre. Le reste (description,
# rappels, pastilles d'OdJ) ne déplace rien dans la grille.
CHAMPS_VISIBLES = frozenset({
    "name", "start", "stop", "start_date", "stop_date", "allday", "duration",
    "location", "videocall_location", "partner_ids", "attendee_ids", "user_id",
    "active", "show_as", "privacy", "recurrency", "x_nc_calendar_id", "color",
})

# Dernier avis par (base, usager), dans ce processus. Un travailleur de plus
# peut doubler un avis : le téléphone relit deux fois, rien de pire.
_DERNIER_AVIS = {}
# Les usagers dont un avis de fin de fenêtre est déjà prévu.
_EN_ATTENTE = set()
_VERROU = threading.Lock()


def _version(texte):
    try:
        return tuple(int(x) for x in (texte or "").split("-")[0].split(".")[:3])
    except ValueError:
        return ()


def _a_pousser(base, uids, maintenant=None):
    """Partager les usagers entre « tout de suite » et « en fin de fenêtre ».

    Rend ``(maintenant, plus_tard)`` : la liste des usagers à prévenir tout de
    suite (marqués), et ``{uid: délai}`` pour ceux dont un avis vient de partir.
    🔴 Un second changement dans la fenêtre n'est pas jeté : créer puis corriger,
    accepter puis déplacer, laisseraient sinon l'écran sur l'avant-dernier état.
    Un seul avis de fin de fenêtre par usager à la fois.
    Pure à l'horloge près.
    """
    maintenant = time.monotonic() if maintenant is None else maintenant
    tout_de_suite, plus_tard = [], {}
    with _VERROU:
        for uid in sorted(uids):
            dernier = _DERNIER_AVIS.get((base, uid))
            if dernier is None or maintenant - dernier >= INTERVALLE_S:
                _DERNIER_AVIS[(base, uid)] = maintenant
                tout_de_suite.append(uid)
            elif (base, uid) not in _EN_ATTENTE:
                _EN_ATTENTE.add((base, uid))
                plus_tard[uid] = INTERVALLE_S - (maintenant - dernier)
    return tout_de_suite, plus_tard


def _fin_de_fenetre(base, uid, maintenant=None):
    """L'avis différé part : il compte comme le dernier, et libère l'attente."""
    maintenant = time.monotonic() if maintenant is None else maintenant
    with _VERROU:
        _EN_ATTENTE.discard((base, uid))
        _DERNIER_AVIS[(base, uid)] = maintenant


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    def _bf_avis_destinataires(self):
        """Les usagers internes actifs que ces rencontres concernent."""
        partenaires = self.sudo().mapped("partner_ids") | self.sudo().mapped("user_id.partner_id")
        return partenaires.mapped("user_ids").filtered(lambda u: u.active and not u.share)

    @api.model
    def _bf_avis_agenda_pour(self, usagers):
        """Retenir ces usagers pour l'avis d'après la validation."""
        if not usagers:
            return
        donnees = self.env.cr.postcommit.data
        if CLE_POSTCOMMIT not in donnees:
            donnees[CLE_POSTCOMMIT] = set()
            base = self.env.cr.dbname
            retenus = donnees[CLE_POSTCOMMIT]

            def _apres():
                # Pendant une passe d'essais (`-i … --test-enable`), rien ne part : un
                # fil qui obtiendrait le curseur d'essai le partagerait d'un autre fil.
                if getattr(odoo.modules.module, "current_test", False):
                    return
                uids, plus_tard = _a_pousser(base, set(retenus))
                if uids:
                    threading.Thread(target=_envoyer, args=(base, uids), daemon=True).start()
                for uid, delai in plus_tard.items():
                    try:
                        minuterie = threading.Timer(delai, _envoyer_en_fin_de_fenetre, args=(base, uid))
                        minuterie.daemon = True
                        minuterie.start()
                    except Exception:  # noqa: BLE001 — plus de fil possible : l'attente se libère
                        _fin_de_fenetre(base, uid)
                        _logger.warning("Avis « agenda » : avis de fin de fenêtre non programmé.",
                                        exc_info=True)

            self.env.cr.postcommit.add(_apres)
        donnees[CLE_POSTCOMMIT].update(usagers.ids)

    @api.model_create_multi
    def create(self, vals_list):
        events = super().create(vals_list)
        events._bf_avis_agenda_pour(events._bf_avis_destinataires())
        return events

    def write(self, vals):
        if not CHAMPS_VISIBLES.intersection(vals):
            return super().write(vals)
        # Avant ET après : une personne retirée des participants doit voir la
        # rencontre disparaître, une personne ajoutée la voir paraître.
        avant = self._bf_avis_destinataires()
        resultat = super().write(vals)
        self._bf_avis_agenda_pour(avant | self._bf_avis_destinataires())
        return resultat

    def unlink(self):
        usagers = self._bf_avis_destinataires()
        resultat = super().unlink()
        self.env["calendar.event"]._bf_avis_agenda_pour(usagers)
        return resultat


class CalendarAttendee(models.Model):
    _inherit = "calendar.attendee"

    def write(self, vals):
        resultat = super().write(vals)
        # Une réponse (acceptée, refusée) change la pastille de l'événement chez
        # l'organisateur et chez celui qui répond.
        if "state" in vals:
            evenements = self.mapped("event_id")
            evenements._bf_avis_agenda_pour(evenements._bf_avis_destinataires())
        return resultat


class BfEmailUnifiedPush(models.AbstractModel):
    _inherit = "bf.email.unifiedpush"

    def _webpush_types(self):
        # Annoncé « toujours chiffré » : l'avis est né avec des appareils qui ont
        # remis leurs clés, et l'appli refuse un « agenda » en clair.
        return super()._webpush_types() + [TYPE_AVIS]

    @api.model
    def _bf_appareils_avis(self, usager):
        """Les appareils à prévenir, selon l'interrupteur de l'avis."""
        valeur = (self.env["ir.config_parameter"].sudo().get_param(PARAM_AVIS) or "").strip()
        if not valeur:
            return self._devices(usager)
        if not _truthy(valeur, defaut=False):
            return self.env["bf.email.mobile.device"]
        return self.env["bf.email.mobile.device"].sudo().search([
            ("user_id", "=", usager.id), ("active", "=", True), ("push_endpoint", "!=", False),
        ])

    @api.model
    def _bf_avis_agenda(self, usagers):
        """Pousser l'avis aux appareils à jour de ces usagers. Rend le nombre reçu."""
        recus = 0
        for usager in usagers:
            appareils = self._bf_appareils_avis(usager).filtered(
                lambda a: _version(getattr(a, "app_version", "")) >= VERSION_MIN)
            if appareils:
                recus += self._envoyer_a(appareils, {"type": TYPE_AVIS}) or 0
        return recus


def _envoyer_en_fin_de_fenetre(base, uid):
    _fin_de_fenetre(base, uid)
    _envoyer(base, [uid])


def _envoyer(base, uids):
    """Dans un fil, après la validation : un curseur à soi, jamais celui de la requête."""
    from odoo.modules.registry import Registry
    try:
        with Registry(base).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            usagers = env["res.users"].browse(uids).exists()
            env["bf.email.unifiedpush"]._bf_avis_agenda(usagers)
    except Exception:  # noqa: BLE001 — un avis manqué ne casse rien, l'écran relit à la minute
        _logger.warning("Avis « agenda » : envoi échoué (%s usager(s)).", len(uids), exc_info=True)
