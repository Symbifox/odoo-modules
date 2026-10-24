"""Signaler une réponse de Gen.

Google Play exige, de toute appli qui génère du contenu par IA, que la personne
puisse signaler une réponse offensante sans quitter l'appli, et que le
développeur se serve des signalements pour régler son filtrage et sa modération.
Symbifox Mobile le fait par `/bf_claude_chat/mobile/v1/report`.

**Le signalement vit sur l'instance de la personne**, à côté de la conversation :
les administrateurs de l'instance le voient (Gen > Administration >
Réponses signalées), comme ils voient déjà toutes les conversations. Le module
n'envoie RIEN hors de l'instance. Un hébergeur qui veut être avisé lit, depuis
l'hôte, l'identifiant, le motif et la date des signalements neufs, et rien
d'autre : ni le texte de la réponse, ni les précisions, ni le nom de la personne.

Le modèle n'a AUCUN droit pour un usager ordinaire : il se crée par la route
mobile, qui vérifie que la réponse est bien à l'appelant, puis écrit en sudo.
Rien ne s'y lit ni ne s'y écrit par RPC hors des administrateurs.
"""

import re

import psycopg2
import psycopg2.errors

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import format_date, mute_logger

MOTIFS = [
    ("offensive", "Offensive or inappropriate"),
    ("false", "False or misleading"),
    ("dangerous", "Dangerous"),
    ("other", "Other"),
]

# Précisions : au-delà, la route refuse (400). L'app borne la saisie au même nombre.
PRECISIONS_MAX = 1000

# Ce que PostgreSQL ne sait pas écrire dans un texte : le caractère nul, et une
# moitié de paire de substitution restée seule (`json.loads` accepte
# « \ud800 » ; une paire complète, elle, y devient un seul caractère).
_ILLISIBLES = re.compile("[\x00\ud800-\udfff]")


def nettoyer_precisions(texte):
    """[texte] sans ce que psycopg2 refuserait (ValueError, donc 500) : le
    caractère nul et les substituts isolés sont retirés, le reste est gardé."""
    return _ILLISIBLES.sub("", texte)

# Plafond par personne : un signalement est un geste rare. Au-delà, la route
# rend 429 (l'app garde le signalement en file et réessaie plus tard).
PLAFOND_PAR_HEURE = 20

# Conservation : passé ce délai, les précisions (le texte libre,
# seule donnée qui peut porter un renseignement personnel au-delà du nom) sont
# effacées. Le motif, l'état, la date et la personne restent : la modération
# compte les signalements, elle n'a pas besoin du texte d'il y a un an.
CONSERVATION_PRECISIONS_MOIS = 12


class ClaudeChatFlag(models.Model):
    _name = "claude.chat.flag"
    _description = "Gen Answer Report"
    _order = "create_date desc, id desc"
    _rec_name = "reason"

    # `set null` : une personne peut supprimer sa conversation (et ses
    # messages avec elle). Le signalement reste, sans le texte : c'est son
    # choix, et le motif garde sa valeur pour la modération.
    message_id = fields.Many2one(
        "claude.chat.message", string="Answer", ondelete="set null",
        index=True, readonly=True,
    )
    session_id = fields.Many2one(
        related="message_id.session_id", string="Conversation", readonly=True,
    )
    answer = fields.Text(
        related="message_id.content", string="Answer Text", readonly=True,
    )
    # Celle qui signale, qui est aussi la propriétaire de la conversation : on
    # ne signale que ses propres réponses. Gardée pour le plafond et pour qu'un
    # administrateur sache à qui en parler, même la conversation supprimée.
    user_id = fields.Many2one(
        "res.users", string="Reported By", required=True, index=True,
        ondelete="cascade", readonly=True,
    )
    reason = fields.Selection(MOTIFS, string="Reason", required=True, readonly=True)
    comment = fields.Text(string="Details", readonly=True)
    state = fields.Selection(
        [("new", "New"), ("reviewed", "Reviewed")],
        string="Status", default="new", required=True, index=True,
    )

    _sql_constraints = [
        # Un seul signalement par réponse et par personne : le rejeu de la file
        # hors ligne et le second geste rendent le premier au lieu d'en créer un.
        ("une_fois_par_reponse", "unique(message_id, user_id)",
         "This answer is already reported."),
    ]

    @api.depends("reason", "create_date")
    @api.depends_context("lang", "tz")
    def _compute_display_name(self):
        # « Offensante ou inappropriée · 2026-10-23 » plutôt que la clé brute
        # du motif, dans la langue de qui regarde.
        libelles = dict(self._fields["reason"]._description_selection(self.env))
        for flag in self:
            morceaux = [libelles.get(flag.reason) or ""]
            if flag.create_date:
                jour = fields.Datetime.context_timestamp(flag, flag.create_date).date()
                morceaux.append(format_date(self.env, jour))
            flag.display_name = " · ".join(m for m in morceaux if m)

    @api.constrains("comment")
    def _check_comment(self):
        for flag in self:
            if flag.comment and len(flag.comment) > PRECISIONS_MAX:
                raise ValidationError(_("The details are too long."))

    def action_mark_reviewed(self):
        self.write({"state": "reviewed"})

    def action_mark_new(self):
        self.write({"state": "new"})

    @api.model
    def _cron_purger_precisions(self):
        """Cron quotidien : vide les précisions des signalements de plus de
        CONSERVATION_PRECISIONS_MOIS mois. Rend le nombre de signalements
        touchés. Privée : seul le cron l'appelle (pas une porte RPC)."""
        limite = fields.Datetime.subtract(
            fields.Datetime.now(), months=CONSERVATION_PRECISIONS_MOIS)
        vieux = self.sudo().search([("create_date", "<", limite),
                                    ("comment", "!=", False)])
        vieux.write({"comment": False})
        return len(vieux)

    @api.model
    def _signaler(self, message, user, reason, comment=""):
        """Le signalement de [message] par [user]. Rend (flag, déjà).

        `(vide, True)` : un envoi simultané du même signalement l'a créé, mais
        il n'est pas lisible d'ici (voir plus bas). `(vide, False)` : plafond.

        L'appelant a déjà vérifié que la réponse est à [user] ; ici, on refuse
        ce qui n'est pas une réponse de Gen arrivée à son terme, on garde le
        premier signalement s'il existe, et on applique le plafond. Écrit en
        sudo : le modèle n'a aucun droit pour un usager ordinaire.
        """
        Flag = self.sudo()
        deja = Flag.search([("message_id", "=", message.id),
                            ("user_id", "=", user.id)], limit=1)
        if deja:
            return deja, True
        recents = Flag.search_count([
            ("user_id", "=", user.id),
            ("create_date", ">=", fields.Datetime.subtract(fields.Datetime.now(), hours=1)),
        ])
        if recents >= PLAFOND_PAR_HEURE:
            return Flag.browse(), False
        try:
            # Deux envois du même signalement en même temps (l'écran et le
            # rejeu de la file) : le second bute sur la contrainte unique.
            # Il ne relit PAS le premier : en REPEATABLE READ, l'instantané de
            # sa transaction ne le voit pas (la recherche rendrait vide, donc
            # un 429 à tort). Il dit seulement « déjà », sans fiche.
            # `mute_logger` : l'INSERT refusé écrirait sinon « bad query » au
            # journal, avec les précisions de la personne dedans.
            with mute_logger("odoo.sql_db"), self.env.cr.savepoint():
                flag = Flag.create({
                    "message_id": message.id,
                    "user_id": user.id,
                    "reason": reason,
                    "comment": (comment or "").strip() or False,
                })
        except psycopg2.errors.UniqueViolation:
            # Seule la contrainte unique veut dire « déjà ». Une clé étrangère
            # refusée (la réponse supprimée entre le contrôle et l'INSERT)
            # remonte telle quelle : l'appelant la rend en 404.
            return Flag.browse(), True
        return flag, False
