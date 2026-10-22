"""Les comptes qui paient, et ce qu'il leur reste.

Le registre de consommation dit **quelle fonction** a dépensé (`origin`), jamais
**quel compte** a payé. C'est la dimension qui manquait, et elle ne se déduit
d'aucune ligne existante : un même locataire peut tirer sur l'abonnement de Blue
Fox, et un autre sur le sien.

Deux tables plutôt qu'une, et ce n'est pas de la décoration :

* `claude.account` porte l'identité, le forfait, les seuils et l'état de la
  sonde ;
* `claude.account.window` porte **une ligne par fenêtre réellement mesurée**.

⚠️ Le piège que ce découpage ferme. Une fenêtre absente du relevé, dans un
modèle à colonnes, vaudrait `0.0` en base, et un tableau de bord la lirait
« rien consommé » au lieu de « non mesuré ». C'est exactement l'erreur déjà
payée sur le registre, où des lignes rendues à zéro se lisaient « gratuit ».
Ici, une fenêtre non mesurée n'a pas de ligne, donc elle ne peut rien
prétendre.

La mesure elle-même ne peut pas vivre ici : les transcripts et les identifiants
sont sur l'hôte, et les comptes sont partagés par des conteneurs et des crons
qui ne touchent jamais Odoo. C'est `scripts/check_claude_token_budget.py` qui
relève, et qui verse par `enregistrer_releve`.
"""

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Les fenêtres que le relevé sait nommer. L'ordre est celui de l'affichage :
# la session d'abord, parce que c'est elle qui bloque dans l'heure.
# Le fuseau de RÉFÉRENCE de ces fenêtres. Ce n'est pas celui du lecteur, et
# c'est exprès : la question que cet écran répond est « quand ma semaine
# bascule », et la réponse ne doit pas changer selon qui regarde. 🔴 Une fiche
# d'usager réglée à Pacific/Auckland affichait la bascule du vendredi 03:00 comme un vendredi
# 19:00, soit seize heures plus tard qu'elle n'a lieu.
MONTREAL = ZoneInfo("America/Toronto")

# ⚠️ `%A` et `%B` suivent la locale du processus, et un serveur Odoo tourne en C :
# les jours sortiraient en anglais au milieu d'une phrase française.
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]

FENETRES = [
    ("five_hour", "Session (5 h)"),
    ("seven_day", "Semaine"),
    ("seven_day_opus", "Semaine Opus"),
    ("seven_day_sonnet", "Semaine Sonnet"),
    ("seven_day_oauth_apps", "Semaine applications"),
]


class ClaudeAccount(models.Model):
    _name = "claude.account"
    _description = "Compte Claude"
    _order = "name"

    name = fields.Char(string="Compte", required=True, index=True)
    active = fields.Boolean(default=True)
    # Le répertoire de configuration EST l'identité du compte : c'est lui que
    # CLAUDE_CONFIG_DIR pointe, et deux répertoires ne partagent jamais un
    # abonnement. C'est donc lui la clé, pas le nom, qui n'est qu'une étiquette.
    config_dir = fields.Char(
        string="Répertoire de configuration",
        required=True,
        index=True,
        help="Ce que CLAUDE_CONFIG_DIR pointe sur l'hôte, par exemple "
             "/home/gen/.claude. C'est la clé du compte.",
    )
    subscription_type = fields.Char(
        string="Forfait",
        readonly=True,
        help="Rendu par le relevé : pro, max, team, enterprise. Vide sur une "
             "clé d'API, où les fenêtres d'abonnement ne s'appliquent pas.",
    )
    note = fields.Text(string="Note", help="À quoi ce compte sert, et qui le paie.")

    window_ids = fields.One2many(
        "claude.account.window", "account_id", string="Fenêtres",
        readonly=True,
    )
    session_ids = fields.One2many(
        "claude.chat.session", "account_id", string="Fils",
    )

    # ── Les seuils, au compte ─────────────────────────────────────────
    # Ils vivent ici et pas dans la sonde : deux comptes n'ont ni le même
    # forfait ni le même usage, et le seuil qui va à l'un crie chez l'autre.
    seuil_haut = fields.Float(
        string="Seuil hebdomadaire (%)", default=80.0,
        help="Au-delà, la semaine est signalée comme approchant du plafond.",
    )
    seuil_session = fields.Float(
        string="Seuil de session (%)", default=90.0,
        help="Au-delà, la fenêtre de 5 h est signalée.",
    )
    seuil_bas = fields.Float(
        string="Seuil de solde dormant (%)", default=40.0,
        help="En deçà, et à l'approche de la bascule, il reste du forfait payé "
             "à dépenser. C'est la moitié la moins évidente de la mesure, et "
             "celle qui rapporte.",
    )
    preavis_h = fields.Float(
        string="Préavis de bascule (h)", default=24.0,
        help="Combien d'heures avant la remise à zéro le solde dormant se dit.",
    )

    # ── Ce que la sonde a rapporté ────────────────────────────────────
    dernier_releve = fields.Datetime(string="Dernier relevé", readonly=True)
    etat_sonde = fields.Selection(
        [
            ("jamais", "Jamais relevé"),
            ("ok", "Relevé obtenu"),
            ("session_morte", "Session éteinte"),
            ("jeton_perime", "Jeton d'accès périmé"),
            ("injoignable", "Injoignable"),
        ],
        string="État de la sonde",
        default="jamais",
        required=True,
        readonly=True,
        index=True,
    )
    message_sonde = fields.Char(string="Diagnostic", readonly=True)

    # ── Crédits achetés en surplus ────────────────────────────────────
    credits_actifs = fields.Boolean(
        string="Crédits en surplus", readonly=True,
        help="Le compte a des crédits achetés au-delà du forfait. Les deux "
             "champs qui suivent ne veulent rien dire quand c'est faux.",
    )
    credits_utilisation = fields.Float(
        string="Crédits utilisés (%)", readonly=True)
    credits_plafond = fields.Float(
        string="Plafond mensuel des crédits", readonly=True)

    # ⚠️ Les libellés disent ce qui est mesuré, pas une impression. « Dans les
    # clous » et « Rien à dire » ont été essayés et retirés le 2026-09-11 :
    # il a fallu demander ce que le premier voulait dire, ce qui était la
    # réponse. Un état se lit sans glossaire, ou il ne sert à rien.
    etat = fields.Selection(
        [
            ("muet", "Sans relevé utilisable"),
            ("ok", "Sous les seuils"),
            ("dormant", "Solde qui va se perdre"),
            ("plafond", "Plafond en vue"),
        ],
        string="État",
        compute="_compute_etat",
        search="_search_etat",
        help="Jugement porté sur le dernier relevé, aux seuils de ce compte. "
             "« Sous les seuils » : la semaine et la session de 5 h sont toutes "
             "deux en deçà de leur plafond, et la bascule n'est pas assez "
             "proche pour que le solde restant risque d'être perdu.",
    )

    _sql_constraints = [
        ("config_dir_unique", "unique(config_dir)",
         "Un répertoire de configuration ne peut désigner qu'un seul compte."),
    ]

    @api.constrains("seuil_haut", "seuil_session", "seuil_bas", "preavis_h")
    def _check_seuils(self):
        for compte in self:
            for champ in ("seuil_haut", "seuil_session", "seuil_bas"):
                valeur = compte[champ]
                if not 0.0 <= valeur <= 100.0:
                    raise ValidationError(_(
                        "%(champ)s vaut %(valeur)s : un seuil est un "
                        "pourcentage, donc entre 0 et 100.",
                        champ=compte._fields[champ].string, valeur=valeur))
            if compte.preavis_h < 0.0:
                raise ValidationError(
                    _("Le préavis de bascule ne peut pas être négatif."))
            # Un seuil bas au-dessus du seuil haut rendrait les deux avis
            # simultanés, donc ni l'un ni l'autre lisible.
            if compte.seuil_bas >= compte.seuil_haut:
                raise ValidationError(_(
                    "Le seuil de solde dormant (%(bas)s) doit rester sous le "
                    "seuil hebdomadaire (%(haut)s), sinon les deux avis se "
                    "déclenchent ensemble.",
                    bas=compte.seuil_bas, haut=compte.seuil_haut))

    # ------------------------------------------------------------------
    # Le jugement
    # ------------------------------------------------------------------
    def _juger(self):
        """Rend l'état d'un compte, sans rien écrire.

        ⚠️ Le jugement dépend de l'HEURE (la bascule approche, ou pas), donc il
        ne peut pas être stocké : une colonne calculée se figerait à l'instant
        du dernier `write` et vieillirait en silence. D'où le champ non stocké,
        et le `search=` obligatoire qui va avec — sans lui, un filtre sur ce
        champ rendrait TOUTE la table sans le dire.
        """
        self.ensure_one()
        if self.etat_sonde != "ok" or not self.window_ids:
            return "muet"
        for fenetre in self.window_ids:
            seuil = (self.seuil_session if fenetre.fenetre == "five_hour"
                     else self.seuil_haut)
            if fenetre.utilization >= seuil:
                return "plafond"
        semaine = self.window_ids.filtered(lambda w: w.fenetre == "seven_day")
        if semaine:
            semaine = semaine[0]
            # ⚠️ La garde porte sur `resets_at`, PAS sur `heures_restantes` :
            # un Float Odoo ne sait pas valoir False, donc une fenêtre sans
            # date de bascule rendrait 0.0, qui passe sous n'importe quel
            # préavis et ferait crier « solde dormant » à tort.
            if (semaine.resets_at
                    and semaine.heures_restantes <= self.preavis_h
                    and semaine.utilization < self.seuil_bas):
                return "dormant"
        return "ok"

    @api.depends("etat_sonde", "window_ids.utilization", "window_ids.resets_at",
                 "seuil_haut", "seuil_session", "seuil_bas", "preavis_h")
    def _compute_etat(self):
        for compte in self:
            compte.etat = compte._juger()

    def _search_etat(self, operator, value):
        if operator not in ("=", "!=", "in", "not in"):
            raise ValidationError(
                _("L'état ne se cherche que par égalité ou appartenance."))
        cherches = value if isinstance(value, (list, tuple)) else [value]
        negatif = operator in ("!=", "not in")
        trouves = [c.id for c in self.with_context(active_test=False).search([])
                   if (c._juger() in cherches) != negatif]
        return [("id", "in", trouves)]

    # ------------------------------------------------------------------
    # L'entrée de la sonde
    # ------------------------------------------------------------------
    @api.model
    def compte_par_repertoire(self, config_dir, nom=False):
        """Trouver ou ouvrir le compte que porte ce répertoire.

        Le compte se crée tout seul au premier relevé : rien n'est semé à
        l'installation, parce que la liste des comptes dépend de l'hôte et pas
        du locataire. Semer les comptes d'un poste sur l'Odoo d'un autre
        n'aurait aucun sens.
        """
        if not config_dir:
            return self.browse()
        compte = self.with_context(active_test=False).search(
            [("config_dir", "=", config_dir)], limit=1)
        if compte:
            return compte
        return self.create({
            "name": nom or config_dir.rstrip("/").rsplit("/", 1)[-1],
            "config_dir": config_dir,
        })

    @api.model
    def seuils_du_compte(self, config_dir):
        """Rendre les seuils d'un compte à la sonde, qui n'a pas le droit de les lire.

        La sonde tourne sous l'identité du robot, qui n'est PAS administrateur :
        les écrans sont réservés aux admins et c'est voulu. Elle a pourtant
        besoin des seuils, sinon ils vivraient en double, dans la ligne de cron
        et dans la fiche, et les deux dériveraient. La règle reste dans la
        sonde, les nombres restent au compte.

        Rend un dictionnaire vide pour un compte inconnu : à elle de retomber
        sur ses valeurs par défaut plutôt que de se taire.
        """
        try:
            compte = self.sudo().with_context(active_test=False).search(
                [("config_dir", "=", config_dir)], limit=1)
            if not compte:
                return {}
            return {
                "seuil_haut": compte.seuil_haut,
                "seuil_session": compte.seuil_session,
                "seuil_bas": compte.seuil_bas,
                "preavis_h": compte.preavis_h,
            }
        except Exception:  # noqa: BLE001
            _logger.warning("Seuils illisibles pour %s.", config_dir,
                            exc_info=True)
            return {}

    @api.model
    def enregistrer_releve(self, config_dir, nom=False, charge=None,
                           erreur=False):
        """Verser un relevé de la sonde. Rend l'id du compte, ou ``False``.

        **Jamais bloquant**, comme `journaliser_passe` : la sonde tourne au cron
        et un échec de comptabilité ne doit pas la faire crier à la panne.

        `erreur` porte le diagnostic quand il n'y a pas eu de relevé du tout.
        Un compte qu'on n'arrive plus à lire garde ses dernières fenêtres, mais
        son état dit pourquoi elles sont vieilles : les effacer ferait
        disparaître la seule chose qui reste à regarder.
        """
        try:
            compte = self.sudo().compte_par_repertoire(config_dir, nom=nom)
            if not compte:
                return False
            if erreur:
                compte.write({
                    "etat_sonde": self._etat_depuis_erreur(erreur),
                    "message_sonde": erreur[:255],
                    "dernier_releve": fields.Datetime.now(),
                })
                return compte.id
            compte._absorber(charge or {})
            return compte.id
        except Exception:  # noqa: BLE001
            _logger.warning("Relevé non enregistré pour %s.", config_dir,
                            exc_info=True)
            return False

    @api.model
    def _etat_depuis_erreur(self, erreur):
        texte = (erreur or "").lower()
        if "blanchi" in texte or "éteinte" in texte or "eteinte" in texte:
            return "session_morte"
        if "401" in texte or "périmé" in texte or "perime" in texte:
            return "jeton_perime"
        return "injoignable"

    def _absorber(self, charge):
        """Réécrire les fenêtres à partir d'une charge utile de /api/oauth/usage."""
        self.ensure_one()
        valeurs = {
            "etat_sonde": "ok",
            "message_sonde": False,
            "dernier_releve": fields.Datetime.now(),
        }
        if charge.get("subscription_type"):
            valeurs["subscription_type"] = charge["subscription_type"]
        extra = charge.get("extra_usage") or {}
        if isinstance(extra, dict):
            valeurs.update({
                "credits_actifs": bool(extra.get("is_enabled")),
                "credits_utilisation": extra.get("utilization") or 0.0,
                "credits_plafond": extra.get("monthly_limit") or 0.0,
            })
        self.write(valeurs)

        # Les fenêtres sont REMPLACÉES, pas fusionnées : une fenêtre que le
        # relevé ne porte plus a disparu côté serveur, et la garder ferait
        # juger sur du périmé.
        self.window_ids.unlink()
        lignes = []
        for cle, libelle in FENETRES:
            brut = charge.get(cle)
            if not isinstance(brut, dict):
                continue
            taux = brut.get("utilization")
            if taux is None:
                # Mesurée mais sans valeur : on ne fabrique pas un zéro.
                continue
            try:
                taux = float(taux)
            except (TypeError, ValueError):
                # ⚠️ Une fenêtre illisible ne doit pas emporter les autres :
                # laisser l'exception monter ferait perdre TOUT le relevé,
                # fenêtres saines comprises, pour une seule valeur difforme.
                _logger.warning(
                    "Fenêtre %s ignorée sur %s : utilisation illisible (%r).",
                    cle, self.config_dir, brut.get("utilization"))
                continue
            lignes.append({
                "account_id": self.id,
                "fenetre": cle,
                "utilization": taux,
                "resets_at": self._en_datetime(brut.get("resets_at")),
            })
        if lignes:
            self.env["claude.account.window"].create(lignes)

    @api.model
    def _en_datetime(self, iso):
        """ISO 8601 du relevé vers un Datetime Odoo (naïf, UTC).

        🔴 Ne PAS passer par `fields.Datetime.to_datetime` : elle attend le
        format serveur (« AAAA-MM-JJ hh:mm:ss ») et rend False sur un ISO 8601
        avec son « T » et son fuseau, ce que le relevé rend toujours. L'erreur
        ne se voit nulle part, elle vide juste toutes les dates de bascule, et
        avec elles la moitié « solde dormant » de la mesure.
        """
        if not iso:
            return False
        try:
            quand = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False
        # ⚠️ Odoo stocke du naïf en UTC. Une valeur portant encore son fuseau
        # passerait en base telle quelle et décalerait toutes les lectures.
        if quand.tzinfo is not None:
            quand = quand.astimezone(timezone.utc).replace(tzinfo=None)
        return quand


class ClaudeAccountPlanSwitch(models.Model):
    """La bascule de forfait, lue et écrite par le pont.

    Le pont tient la seule table de bascule et seul il sait quels comptes
    cette instance peut viser : Odoo ne fait que la montrer et la demander.
    Un pont sans route `/bascule` (HTTP 404) n'offre pas de bascule : le
    réglage se cache.
    """

    _inherit = "claude.account"

    @api.model
    def _plan_switch_call(self, payload):
        bridge = self.env["bf.ai.bridge"]
        return bridge.call("/bascule", dict(payload, tenant=bridge.tenant()), 5) or {}

    @api.model
    def _plan_switch_message(self, reponse):
        """Le refus du pont, dans la langue de la personne."""
        code = reponse.get("code")
        if code == "several":
            return _("Several plans are switched for this instance. Change them "
                     "on the bridge host, not here.")
        if code == "unreadable":
            return _("The bridge cannot read its plan switch table: fix it on the "
                     "server before changing it here.")
        if code == "refused":
            return _("The bridge does not open this plan to this instance.")
        return _("The bridge refused the plan switch: %s", str(reponse.get("error"))[:200])

    @api.model
    def _plan_switch_key(self, account, users):
        """La forme comparable d'une bascule : (répertoire, usagers triés)."""
        rep = account.config_dir if account else ""
        return [rep, sorted(users.ids) if rep else []]

    @api.model
    def _plan_switch_snapshot(self):
        """La bascule lue maintenant, en JSON, ou "" si elle ne se lit pas."""
        etat = self._plan_switch_read()
        if etat["error"]:
            return ""
        return json.dumps(self._plan_switch_key(etat["account"], etat["users"]))

    @api.model
    def _plan_switch_unchanged(self, snapshot, account, users):
        """Vrai si le formulaire renvoie ce qu'il avait lu à son ouverture."""
        try:
            lu = json.loads(snapshot) if snapshot else None
        except ValueError:
            lu = None
        return lu is not None and lu == self._plan_switch_key(account, users)

    @api.model
    def _plan_switch_read(self):
        """{"account": claude.account, "users": res.users, "error": str|False}."""
        vide = {"account": self.browse(), "users": self.env["res.users"],
                "error": False, "available": False}
        if not self.env.user.has_group("base.group_system"):
            return vide
        try:
            etat = self._plan_switch_call({"action": "get"})
        except ValueError as e:
            # Le statut, pas le corps cité après lui : un 500 qui citerait
            # « HTTP 404 » reste une panne.
            if str(e).startswith("Bridge HTTP 404:"):
                return vide   # pont sans bascule de forfait : rien à montrer
            _logger.info("Gen : bascule de forfait non lue", exc_info=True)
            return dict(vide, available=True,
                        error=_("The bridge did not answer: the plan switch "
                                "cannot be read or changed right now."))
        except Exception:  # noqa: BLE001 — un pont muet n'empêche pas d'ouvrir les réglages
            _logger.info("Gen : bascule de forfait non lue", exc_info=True)
            return dict(vide, available=True,
                        error=_("The bridge did not answer: the plan switch "
                                "cannot be read or changed right now."))
        if etat.get("error"):
            return dict(vide, error=self._plan_switch_message(etat), available=True)
        if not etat.get("targets"):
            return vide   # aucun forfait ouvert à cette instance : rien à montrer
        compte = self.search([("config_dir", "=", etat.get("config_dir") or "")], limit=1) \
            if etat.get("config_dir") else self.browse()
        uids = [int(u) for u in etat.get("users") or [] if str(u).isdigit()]
        usagers = self.env["res.users"].browse(uids).exists()
        # Ne jamais montrer « forfait de l'instance » ou
        # « tous » pour une bascule que cet écran ne sait pas représenter ; le
        # prochain enregistrement l'aurait réécrite.
        if etat.get("config_dir") and not compte:
            return dict(vide, available=True, error=_(
                "The plan is switched to an account this instance does not list "
                "(%s). Change it on the bridge host.",
                etat["config_dir"]))
        if uids and not usagers:
            return dict(vide, available=True, error=_(
                "The plan is switched for users who no longer exist here. "
                "Change it on the bridge host."))
        return {"account": compte, "users": usagers, "error": False, "available": True}

    @api.model
    def _plan_switch_write(self, account, users):
        """Demander la bascule au pont, seulement si elle change.

        ⚠️ Les réglages s'enregistrent d'un bloc : un pont muet à l'ouverture
        rendait des champs vides, et enregistrer un autre réglage aurait
        retiré la bascule. On relit donc l'état avant d'écrire.
        """
        if not self.env.user.has_group("base.group_system"):
            return
        actuel = self._plan_switch_read()
        voulu_dir = account.config_dir if account else ""
        actuel_dir = actuel["account"].config_dir if actuel["account"] else ""
        if actuel["error"]:
            if voulu_dir or users:
                raise UserError(actuel["error"])
            return
        if voulu_dir == actuel_dir and set(users.ids) == set(actuel["users"].ids):
            return
        try:
            reponse = self._plan_switch_call({
                "action": "set", "config_dir": voulu_dir,
                "users": users.ids if voulu_dir else []})
        except Exception as e:  # noqa: BLE001 — pont lent ou en erreur HTTP
            _logger.warning("Gen : bascule de forfait non écrite", exc_info=True)
            raise UserError(_("The bridge did not answer: the plan switch "
                              "cannot be read or changed right now.")) from e
        if reponse.get("error"):
            raise UserError(self._plan_switch_message(reponse))
        _logger.info("Gen : bascule de forfait vers %r pour %s (par uid %s)",
                     voulu_dir or "le forfait de l'instance", users.ids or "tous",
                     self.env.uid)


class ClaudeAccountWindow(models.Model):
    _name = "claude.account.window"
    _description = "Fenêtre d'abonnement Claude"
    _order = "account_id, fenetre"

    account_id = fields.Many2one(
        "claude.account", string="Compte", required=True, ondelete="cascade",
        index=True,
    )
    fenetre = fields.Selection(
        FENETRES, string="Fenêtre", required=True, index=True)
    utilization = fields.Float(
        string="Utilisée (%)", required=True,
        help="Part de la fenêtre déjà consommée, telle que le serveur la rend.",
    )
    resets_at = fields.Datetime(
        string="Remise à zéro",
        help="Instant exact où la fenêtre repart à zéro, en UTC.",
    )
    bascule_montreal = fields.Char(
        string="Bascule (Montréal)",
        compute="_compute_bascule_montreal",
        help="La remise à zéro dite en heure de Montréal, quel que soit le "
             "fuseau de la fiche d'usager qui lit l'écran.",
    )
    heures_restantes = fields.Float(
        string="Reste (h)", compute="_compute_heures_restantes",
        help="Heures d'ici la remise à zéro. Non stocké : ça bouge tout seul. "
             "⚠️ Vaut 0 quand la remise à zéro est inconnue, parce qu'un Float "
             "ne sait pas valoir « non mesuré » : lire `resets_at` avant de "
             "s'en servir pour juger.",
    )

    @api.depends("resets_at")
    def _compute_bascule_montreal(self):
        for fenetre in self:
            if not fenetre.resets_at:
                fenetre.bascule_montreal = False
                continue
            # resets_at est naïf en UTC, comme tout Datetime Odoo.
            quand = fenetre.resets_at.replace(tzinfo=timezone.utc).astimezone(
                MONTREAL)
            fenetre.bascule_montreal = (
                f"{JOURS[quand.weekday()]} {quand.day} "
                f"{MOIS[quand.month - 1]} à {quand:%H:%M}")

    @api.depends("resets_at")
    def _compute_heures_restantes(self):
        maintenant = fields.Datetime.now()
        for fenetre in self:
            if not fenetre.resets_at:
                fenetre.heures_restantes = 0.0
                continue
            fenetre.heures_restantes = (
                fenetre.resets_at - maintenant).total_seconds() / 3600.0
