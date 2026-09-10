"""Réglages courriel portés par la personne, pas par un de ses comptes.

`bf.email.account` décrit une boîte IMAP ; une personne peut en avoir
plusieurs. Une absence, elle, concerne la personne. Ce qui suit vit donc ici,
y compris le mode « ne pas déranger », qui fait taire des
avis de deux modules et n'appartient à aucun compte.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# ⚠️ `_tz_get` est une FONCTION DE MODULE, pas une méthode de modèle.
# `self.env["res.partner"]._tz_get()` lève `AttributeError` — et comme la
# sélection n'est évaluée qu'au `fields_get`, la page ENTIÈRE des Préférences
# refuse de s'ouvrir, pas seulement ce champ. Signalé le 2026-09-09.
from odoo.addons.base.models.res_partner import _tz_get


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_absence_from_calendar = fields.Boolean(
        string="Détecter mes absences à l'agenda",
        help="Un événement de votre agenda dont le titre parle de vacances, "
             "de congé ou d'absence allume le répondeur pour sa durée, et "
             "l'éteint à la fin. Demande un « message type » : c'est lui qui "
             "est copié.\n\n"
             "C'est le défaut que ça corrige : un répondeur mal réglé se "
             "remarque, un répondeur qu'on a oublié d'éteindre répond pendant "
             "des semaines à des gens qui vous savent revenu.",
    )

    # ------------------------------------------------------------------
    # Ne pas déranger
    # ------------------------------------------------------------------
    bf_dnd_meetings = fields.Boolean(
        string="Me faire taire pendant mes rencontres",
        help="Une rencontre est un événement occupé, à plus d'un participant, "
             "qui n'occupe pas la journée entière. « Occupé » seul ne suffit "
             "pas : c'est ainsi qu'on bloque un créneau pour soi, et ça ne dit "
             "rien de la présence de quelqu'un d'autre.",
    )
    bf_dnd_manual_until = fields.Datetime(
        string="Ne pas déranger jusqu'à",
        help="L'interrupteur manuel. Il s'ajoute à l'armement par l'agenda, "
             "il ne le remplace pas.",
    )
    bf_dnd_manual_forever = fields.Boolean(
        string="Ne pas déranger, sans échéance",
        help="Le mode reste armé jusqu'à ce que vous l'éteigniez. "
             "⚠️ Un booléen plutôt qu'une date lointaine : « jusqu'au "
             "31 décembre 2099 » se lit comme un réglage accidentel, et le "
             "résumé de sortie n'arriverait jamais.",
    )
    bf_dnd_manual_off_until = fields.Datetime(
        string="Me déranger quand même jusqu'à",
        help="L'inverse : reprendre la main sur l'armement automatique, le "
             "temps d'une démonstration ou d'une garde. Prime sur tout le "
             "reste.",
    )
    bf_dnd_quiet_enabled = fields.Boolean(
        string="Heures calmes",
        help="Une plage quotidienne où aucun avis ne s'affiche.",
    )
    bf_dnd_quiet_tz = fields.Selection(
        selection=_tz_get,
        string="Fuseau des heures calmes",
        help="Le fuseau où la plage ci-dessous se lit. ⚠️ Volontairement "
             "distinct du fuseau de la fiche contact : celui-ci suit le lieu "
             "de travail réel, pas celui du compte. Écrit comme un nom de la "
             "base tzdata, jamais comme un décalage, pour suivre les "
             "changements d'heure tout seul.",
    )
    bf_dnd_quiet_start = fields.Float(
        string="Début des heures calmes",
        help="Heure locale du fuseau ci-dessus. Une plage qui passe minuit "
             "est normale : 22 h à 8 h se lit telle quelle.",
    )
    bf_dnd_quiet_end = fields.Float(string="Fin des heures calmes")

    # Témoin de bascule, écrit par le cron. ⚠️ Ne décide de rien : l'état se
    # recalcule à chaque lecture. Il sert uniquement à reconnaître qu'une
    # rencontre vient de commencer ou de finir, ce que personne n'annonce.
    bf_dnd_last_state = fields.Boolean(
        string="Dernier état connu", default=False, copy=False,
    )

    bf_dnd_active = fields.Boolean(
        string="En ce moment", compute="_compute_bf_dnd_now",
    )
    bf_dnd_reason = fields.Char(
        string="Pourquoi", compute="_compute_bf_dnd_now",
    )
    bf_dnd_until = fields.Datetime(
        string="Jusqu'à", compute="_compute_bf_dnd_now",
    )

    @api.depends("bf_dnd_meetings", "bf_dnd_manual_until",
                 "bf_dnd_manual_forever",
                 "bf_dnd_manual_off_until", "bf_dnd_quiet_enabled",
                 "bf_dnd_quiet_tz", "bf_dnd_quiet_start", "bf_dnd_quiet_end")
    def _compute_bf_dnd_now(self):
        """L'état, à l'instant où l'écran s'affiche.

        Non stocké : il dépend de l'heure, et un champ stocké dépendant de
        l'heure ment dès la minute suivante.
        """
        labels = {
            "manual": _("interrupteur manuel"),
            "meeting": _("rencontre en cours"),
            "quiet": _("heures calmes"),
        }
        Dnd = self.env["bf.dnd"]
        for user in self:
            state = Dnd._state_for(user)
            user.bf_dnd_active = state["active"]
            user.bf_dnd_reason = labels.get(state["reason"], "") or False
            user.bf_dnd_until = state["until"] or False

    # ------------------------------------------------------------------
    # Les boutons
    # ------------------------------------------------------------------
    def _bf_dnd_set(self, vals):
        """Écrire un réglage du mode, sur son propre compte.

        ⚠️ La bascule elle-même n'est PAS annoncée ici : ``write`` s'en charge
        déjà pour tous les chemins, formulaire des préférences compris. La
        poser aussi ici enverrait deux messages de bus pour un seul geste, et
        rendrait deux fois le résumé de sortie.

        ⚠️ ``sudo`` sur l'écriture, mais la borne est explicite juste avant :
        ``res.users`` n'accepte les champs de ``SELF_WRITEABLE_FIELDS`` que
        sur soi, et un bouton qui échouerait là se lirait comme un défaut du
        mode plutôt que comme un refus de droits.
        """
        self.ensure_one()
        if self.id != self.env.uid and not self.env.user._is_admin():
            raise AccessError(
                _("Le mode « ne pas déranger » se règle sur son propre "
                  "compte."))
        self.sudo().write(vals)
        return self.env["bf.dnd"]._state_for(self)

    def action_bf_dnd_for_minutes(self, minutes=None):
        """Se faire taire pour un moment. Sans argument, une demi-heure.

        ⚠️ La durée arrive par le CONTEXTE quand l'appel vient d'un bouton de
        formulaire : un ``context="{'minutes': 30}"`` ne devient pas un
        argument nommé, Odoo ne passe que le contexte. Un bouton qui aurait
        compté sur le paramètre aurait silencieusement posé trente minutes
        partout, ce qui ressemble à un réglage plutôt qu'à un défaut.
        """
        if minutes is None:
            minutes = self.env.context.get("minutes")
        try:
            minutes = int(minutes or 30)
        except (TypeError, ValueError):
            minutes = 30
        minutes = max(1, min(minutes, 60 * 24))
        self._bf_dnd_set({
            "bf_dnd_manual_until": fields.Datetime.now() + timedelta(
                minutes=minutes),
            "bf_dnd_manual_forever": False,
            "bf_dnd_manual_off_until": False,
        })
        return True

    def action_bf_dnd_forever(self):
        """Se faire taire jusqu'à nouvel ordre.

        ⚠️ On efface l'échéance en même temps : laisser les deux réglages
        rendrait l'écran ambigu, et éteindre plus tard n'aurait pas l'air
        d'avoir tout éteint.
        """
        self._bf_dnd_set({
            "bf_dnd_manual_forever": True,
            "bf_dnd_manual_until": False,
            "bf_dnd_manual_off_until": False,
        })
        return True

    def action_bf_dnd_until_meeting_end(self):
        """Jusqu'à la fin de la rencontre en cours, ou une heure à défaut."""
        self.ensure_one()
        event = self.env["bf.dnd"]._meeting_now(self)
        until = event.stop if event else (
            fields.Datetime.now() + timedelta(hours=1))
        self._bf_dnd_set({
            "bf_dnd_manual_until": until,
            "bf_dnd_manual_forever": False,
            "bf_dnd_manual_off_until": False,
        })
        return True

    def action_bf_dnd_off(self):
        """Éteindre le mode, y compris l'armement automatique, pour une heure.

        Éteindre l'interrupteur manuel ne suffit pas quand c'est l'agenda qui
        arme : sans le « me déranger quand même », le mode se rallumerait à la
        minute suivante et le geste passerait pour un défaut.
        """
        self.ensure_one()
        state = self.env["bf.dnd"]._state_for(self)
        vals = {"bf_dnd_manual_until": False, "bf_dnd_manual_forever": False}
        if state["active"] and state["reason"] in ("meeting", "quiet"):
            vals["bf_dnd_manual_off_until"] = (
                fields.Datetime.now() + timedelta(hours=1))
        self._bf_dnd_set(vals)
        return True

    @api.model
    def bf_dnd_state(self):
        """L'état du mode pour la personne connectée, pour la barre du haut.

        ⚠️ Ne lit QUE ``self.env.user`` et ne prend aucun identifiant : une
        méthode publique est appelable par RPC avec n'importe quel argument,
        donc celle-ci n'en accepte aucun plutôt que d'avoir à refuser le
        compte d'autrui.

        ``enabled`` est l'interrupteur d'instance : chez un locataire qui ne
        s'en sert pas, l'entrée du menu ne s'affiche pas du tout.
        """
        labels = {
            "manual": _("interrupteur manuel"),
            "meeting": _("rencontre en cours"),
            "quiet": _("heures calmes"),
        }
        Dnd = self.env["bf.dnd"]
        state = Dnd._state_for(self.env.user)
        return {
            "enabled": Dnd._instance_enabled(),
            "active": bool(state["active"]),
            "reason": labels.get(state["reason"], "") or False,
            "until": fields.Datetime.to_string(state["until"])
            if state["until"] else False,
        }

    # ------------------------------------------------------------------
    # Réglages personnels
    # ------------------------------------------------------------------
    BF_DND_SELF_FIELDS = [
        "bf_dnd_meetings", "bf_dnd_manual_until", "bf_dnd_manual_forever",
        "bf_dnd_manual_off_until",
        "bf_dnd_quiet_enabled", "bf_dnd_quiet_tz", "bf_dnd_quiet_start",
        "bf_dnd_quiet_end",
    ]

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            "bf_absence_from_calendar",
            "bf_dnd_active", "bf_dnd_reason", "bf_dnd_until",
            "bf_dnd_last_state",
        ] + self.BF_DND_SELF_FIELDS

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            "bf_absence_from_calendar",
        ] + self.BF_DND_SELF_FIELDS

    # ------------------------------------------------------------------
    # Annoncer une bascule décidée depuis le formulaire
    # ------------------------------------------------------------------
    def write(self, vals):
        """Le formulaire des préférences doit basculer aussi vite qu'un bouton.

        ⚠️ Le test porte sur les clés écrites, pas sur l'état : ``write`` est
        appelé à chaque connexion (``login_date``) et recalculer l'état du
        mode à ce moment-là ferait une requête d'agenda par ouverture de
        session.
        """
        touched = [f for f in self.BF_DND_SELF_FIELDS if f in vals]
        if not touched:
            return super().write(vals)
        Dnd = self.env["bf.dnd"]
        before = {u.id: Dnd._state_for(u)["active"] for u in self}
        result = super().write(vals)
        for user in self:
            state = Dnd._state_for(user)
            if bool(state["active"]) == bool(before.get(user.id)):
                continue
            user.sudo().write({"bf_dnd_last_state": state["active"]})
            Dnd._push_state(user, state)
            if not state["active"]:
                Dnd._release(user)
        return result
