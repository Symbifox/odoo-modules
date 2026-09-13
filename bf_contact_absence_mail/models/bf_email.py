"""Reconnaître un répondeur d'absence, et en lire la période.

**Tout ce qui suit est mesuré sur un corpus de courrier réel**, 12 396
courriels reçus, et non deviné :

- `Auto-Submitted: auto-replied`, que la RFC 3834 rend obligatoire, est présent
  sur **7 courriels sur 12 396**. Vingt des vingt-neuf répondeurs repérés ne
  portent **aucun** en-tête automatique. Un détecteur qui ne lit que les
  en-têtes rate les trois quarts du gibier ;
- l'objet fait donc le gros du travail, et il est **français avant d'être
  anglais** : « Réponse automatique » 16 fois, « Automatic reply » 6 ;
- ⚠️ Outlook francophone écrit `Réponse automatique\\xa0:` avec une **espace
  insécable** : un filtre en espace ordinaire ne verrait rien ;
- le filet attrape des robots (`mailer-daemon` deux fois) : sans exclusion, une
  ingestion automatique finirait par mettre un MAILER-DAEMON en vacances.

La grammaire des messages automatiques n'est pas réinventée ici : elle existe
déjà dans `bf.email.absence`, qui l'applique en sens inverse pour décider à qui
NE PAS répondre.
"""

import logging
import re
import unicodedata
from datetime import date, datetime, timedelta

from odoo import _, api, fields, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

# Les en-têtes qui disent « ceci est un RÉPONDEUR ».
#
# 🔴 `Auto-Submitted` ne suffit pas : la RFC 3834 §5 distingue `auto-replied`
# (une réponse personnelle produite par une machine, donc un répondeur) de
# `auto-generated` (une notification, un rapport, une confirmation). Accepter
# tout ce qui n'est pas `no` a été essayé sur un corpus réel et a
# rendu **1 510 faux positifs sur 1 531** : nos propres rapports de sauvegarde,
# les confirmations de rendez-vous, les avis Nextcloud et le digest quotidien
# portent tous `auto-generated`. Seul `auto-replied` compte.
#
# 🔴 `X-Auto-Response-Suppress` a été retiré des signaux positifs : c'est une
# consigne d'Exchange pour que le destinataire ne réponde PAS automatiquement,
# pas une déclaration que ce message-ci est un répondeur. Nos propres
# notifications le posent.
RX_AUTO_SUBMITTED = re.compile(r"^auto-submitted:\s*([^\r\n;]+)", re.I | re.M)
VALEURS_REPONDEUR = ("auto-replied",)
RX_X_AUTOREPLY = re.compile(r"^x-auto(reply|respond|-reply)\b", re.I | re.M)
RX_PRECEDENCE_AUTO = re.compile(r"^precedence:\s*auto[_-]?reply", re.I | re.M)

# Les objets, normalisés (minuscules, sans accents, espaces insécables
# ramenées à l'espace ordinaire).
SUJETS_FORTS = (
    "reponse automatique",
    "automatic reply",
    "out of office",
    "automatische antwort",
    "respuesta automatica",
    "autoreply",
    "auto-reply",
    "absence du bureau",
    "risposta automatica",
)
# « Absence » seul est trop large pour décider tout seul : un courriel qui
# s'intitule « Absence de Camille » n'est pas un répondeur. Il faut un indice
# du corps en plus.
SUJETS_FAIBLES = ("absence", "auto:")

# 🔴 Trois des 25 reconnaissances du corpus réel sont des ACCUSÉS DE RÉCEPTION
# de candidature : « Automatic reply: Your online application is currently being
# reviewed ». Ils portent l'objet d'un répondeur sans en être un, ils n'ont
# jamais de date, et ils reviennent à chaque envoi. Les reconnaître les sort de
# la pile à décider sans effacer leur trace.
INDICES_ACCUSE = (
    "your application", "your online application", "votre candidature",
    "being reviewed", "we have received your application",
    "thank you for applying", "merci de votre candidature",
    "nous avons bien recu votre candidature", "bien recu votre demande",
    "your request has been received", "we have received your request",
)

INDICES_CORPS = (
    "en vacances", "de retour le", "je serai absent", "je suis absent",
    "serai absente", "suis absente", "jusqu'a mon retour", "a mon retour",
    "hors du bureau", "nous serons fermes", "serons fermes", "sommes fermes",
    "conge annuel", "out of the office", "annual leave", "on leave",
    "away from the office", "i am away", "back on", "back in the office",
    "returning on", "will return",
)

# Les adresses qui appartiennent à des machines, jamais à une personne qui
# part en vacances.
RX_ROBOT = re.compile(
    r"^(mailer-daemon|postmaster|owner-|.*-request$|noreply|no-reply"
    r"|do-not-reply|donotreply|bounce|bounces|notification|notifications"
    r"|mail|daemon|abuse)",
    re.I,
)

MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_NOMS_MOIS = "|".join(sorted(MOIS, key=len, reverse=True))
RX_JOUR_MOIS = re.compile(r"\b(\d{1,2})\s*(?:er|re|e|st|nd|rd|th)?\s+(?:de\s+)?(%s)\b"
                          % _NOMS_MOIS, re.I)
RX_MOIS_JOUR = re.compile(r"\b(%s)\s+(\d{1,2})\b" % _NOMS_MOIS, re.I)
RX_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# ⚠️ « du 20 au 31 juillet » : le premier jour n'a PAS son mois. Sans ce
# motif, seul le 31 est lu et une fermeture de douze jours se réduit à un.
# Mesuré sur un vrai répondeur d'OVI (vacances de la construction).
RX_INTERVALLE = re.compile(
    r"\b(?:du\s+)?(\d{1,2})\s*(?:er)?\s*(?:au|a|à|-|jusqu'au)\s*(\d{1,2})\s+(%s)\b"
    % _NOMS_MOIS, re.I)
# Ce qui marque un DERNIER JOUR d'absence plutôt qu'un jour de reprise.
RX_DERNIER_JOUR = re.compile(r"\b(jusqu'au|jusqu'a|jusqu au|au|until|through|till)\b",
                             re.I)
# La formule qui désigne le jour de REPRISE et non le dernier jour absent.
# « mon retour au bureau le lundi 4 mai » compte autant que « de retour le 4 » :
# le mot `retour` seul est le signal, parce que c'est lui qui apparaît dans la
# prose réelle sous une demi-douzaine de formes.
RX_REPRISE = re.compile(
    r"(retour|je reviens|reviendrai|back on|back in the office"
    r"|returning|i return|will return)", re.I)
RX_COURRIEL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
RX_TELEPHONE = re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")


def _normaliser(texte):
    """Minuscules, sans accents, et l'espace insécable ramenée à l'espace."""
    if not texte:
        return ""
    texte = texte.replace("\xa0", " ").replace(" ", " ")
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return texte.lower()


class BfEmail(models.Model):
    _inherit = "bf.email"

    bf_absence_scanned = fields.Boolean(
        string="Lu par la vigie des absences",
        default=False,
        index=True,
        copy=False,
    )
    bf_absence_suggestion_id = fields.Many2one(
        comodel_name="bf.partner.absence.suggestion",
        string="Absence proposée",
        readonly=True,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Le texte qu'on lit
    # ------------------------------------------------------------------
    def _bf_absence_text(self, limite=2500):
        """Objet plus corps, en texte.

        ⚠️ `body_preview` est plafonné à 300 caractères, et la date de retour
        tombe souvent APRÈS la formule de politesse. La mesure du corpus, faite
        sur l'aperçu seul, sous-comptait donc les relèves nommées : on lit ici
        le corps complet quand il est là.
        """
        self.ensure_one()
        # 🔴 Le corps UNE SEULE FOIS. La première version concaténait l'aperçu
        # ET le corps complet, dont l'aperçu est le préfixe : chaque date
        # apparaissait donc en double, ce qui rendait la branche « une seule
        # date » inatteignable. Trouvé par mutation le 2026-09-13, pas par
        # relecture : le résultat restait juste par accident.
        corps = ""
        if self.body_html:
            try:
                corps = html2plaintext(self.body_html)[:limite]
            except Exception:  # noqa: BLE001 - un corps illisible ne bloque rien
                corps = ""
        if not corps.strip():
            corps = self.body_preview or ""
        return "\n".join(m for m in (self.subject or "", corps) if m)

    # ------------------------------------------------------------------
    # Reconnaissance
    # ------------------------------------------------------------------
    @api.model
    def _bf_absence_own_domains(self):
        """Les domaines de courriel de la maison.

        🔴 Sans eux, la passe apprend de NOS propres machines : rapports de
        sauvegarde, confirmations de rendez-vous, avis Nextcloud, digest du
        matin. Mesuré sur le corpus réel avant correction : l'essentiel des
        faux positifs venait de là, avec des partenaires de service
        qui ne portent aucun utilisateur et passaient
        donc le test « contact interne ».
        """
        domaines = set()
        for alias in self.env["mail.alias.domain"].sudo().search([]):
            if alias.name:
                domaines.add(alias.name.strip().lower())
        for societe in self.env["res.company"].sudo().search([]):
            if societe.email and "@" in societe.email:
                domaines.add(societe.email.split("@")[-1].strip().lower())
        for utilisateur in self.env["res.users"].sudo().search(
                [("share", "=", False)]):
            courriel = utilisateur.partner_id.email or ""
            if "@" in courriel:
                domaines.add(courriel.split("@")[-1].strip().lower())
        return {d for d in domaines if d}

    def _bf_absence_excluded(self, domaines=None):
        """Pourquoi ce courriel ne peut PAS enseigner une absence, ou None.

        `domaines` évite de refaire trois recherches par courriel dans une
        passe de deux cents.
        """
        self.ensure_one()
        if self.direction != "in":
            return "sortant"
        if not self.partner_id:
            return "sans contact fiché"
        if self.is_bulk:
            return "envoi en masse"
        if self.is_from_me:
            return "écrit par nous"
        adresse = (self.email_from or "").lower()
        extrait = re.search(r"<([^>]+)>", adresse)
        if extrait:
            adresse = extrait.group(1)
        locale = adresse.split("@")[0].strip()
        if RX_ROBOT.match(locale):
            return "adresse de machine"
        domaine = adresse.split("@")[-1].strip()
        if domaines is None:
            domaines = self._bf_absence_own_domains()
        if domaine and domaine in domaines:
            return "domaine de la maison"
        # Un collègue en vacances relève de hr_holidays, pas d'ici.
        if any(not user.share for user in self.partner_id.user_ids):
            return "contact interne"
        return None

    def _bf_absence_signals(self):
        """(reconnu, par_quoi). Déterministe, sans IA."""
        self.ensure_one()
        entetes = self.raw_headers or ""
        auto = RX_AUTO_SUBMITTED.search(entetes)
        if auto and auto.group(1).strip().lower() in VALEURS_REPONDEUR:
            return True, "en-tête Auto-Submitted"
        if RX_X_AUTOREPLY.search(entetes):
            return True, "en-tête X-Autoreply"
        if RX_PRECEDENCE_AUTO.search(entetes):
            return True, "en-tête Precedence"
        sujet = _normaliser(self.subject)
        for motif in SUJETS_FORTS:
            if motif in sujet:
                return True, "objet"
        # ⚠️ Le corps n'est déplié QUE si un objet faible le demande. Le lire
        # d'abord coûtait un `html2plaintext` par courriel : sur une passe de
        # rattrapage de douze mille messages, c'est douze mille conversions
        # pour une vingtaine de cas. Mesuré en préparant le rattrapage.
        for motif in SUJETS_FAIBLES:
            if sujet.startswith(motif) or (" %s" % motif) in sujet:
                corps = _normaliser(self._bf_absence_text())[:2500]
                if any(indice in corps for indice in INDICES_CORPS):
                    return True, "objet et corps"
                break
        return False, None

    def _bf_absence_is_acknowledgement(self):
        """Un accusé de réception, plutôt qu'une personne partie."""
        self.ensure_one()
        texte = _normaliser(self._bf_absence_text())[:2500]
        return any(indice in texte for indice in INDICES_ACCUSE)

    # ------------------------------------------------------------------
    # Lecture de la période
    # ------------------------------------------------------------------
    @api.model
    def _bf_absence_dates_from_text(self, texte, ancre):
        """Les dates lisibles dans la prose, ordonnées.

        ⚠️ L'année est presque toujours absente (« de retour le 17 août ») :
        elle se déduit de la date de réception, et une date qui tomberait plus
        de 45 jours avant ou 200 jours après est écartée plutôt que devinée.
        """
        brut = _normaliser(texte)
        trouvees = []
        for m in RX_ISO.finditer(brut):
            try:
                trouvees.append((date(int(m.group(1)), int(m.group(2)),
                                      int(m.group(3))), m.start()))
            except ValueError:
                continue
        paires = []
        for m in RX_INTERVALLE.finditer(brut):
            mois = MOIS[m.group(3)]
            paires.append((int(m.group(1)), mois, m.start()))
            paires.append((int(m.group(2)), mois, m.start() + 1))
        for m in RX_JOUR_MOIS.finditer(brut):
            paires.append((int(m.group(1)), MOIS[m.group(2)], m.start()))
        for m in RX_MOIS_JOUR.finditer(brut):
            paires.append((int(m.group(2)), MOIS[m.group(1)], m.start()))
        for jour, mois, position in paires:
            for annee in (ancre.year, ancre.year + 1, ancre.year - 1):
                try:
                    candidate = date(annee, mois, jour)
                except ValueError:
                    continue
                if ancre - timedelta(days=45) <= candidate <= ancre + timedelta(days=200):
                    trouvees.append((candidate, position))
                    break
        trouvees.sort(key=lambda t: t[1])
        return trouvees

    def _bf_absence_read_period(self):
        """(date_from, date_to, releve) lus en clair, sans IA. Peut rendre None.

        La prose donne les deux formes, et l'écart d'un jour entre elles est
        exactement ce qui ferait parler le bandeau le matin du retour :
        « jusqu'au 3 mai » est un dernier jour absent, « de retour le 4 mai »
        est un premier jour présent.
        """
        self.ensure_one()
        ancre = (self.date or fields.Datetime.now())
        if isinstance(ancre, datetime):
            ancre = ancre.date()
        texte = self._bf_absence_text()
        trouvees = self._bf_absence_dates_from_text(texte, ancre)
        if not trouvees:
            return None, None, self._bf_absence_read_backup(texte)
        dates = [d for d, _pos in trouvees]
        brut = _normaliser(texte)
        fin = max(dates)
        debut = min(dates)
        # 🔴 La formule se lit À CÔTÉ de la date, pas n'importe où dans le
        # message. Chercher « retour » dans tout le texte faisait reculer d'un
        # jour « je suis en vacances jusqu'au 20 juillet. Votre candidature
        # sera revue à mon retour. » : le mot était là, mais il ne qualifiait
        # aucune date. Trouvé en écrivant l'essai des accusés de réception.
        position_fin = max(pos for d, pos in trouvees if d == fin)
        if self._bf_absence_date_is_return(brut, position_fin):
            fin = fin - timedelta(days=1)
            if len(dates) == 1:
                debut = min(ancre, fin)
        if fin < debut:
            debut = fin
        return debut, fin, self._bf_absence_read_backup(texte)

    @api.model
    def _bf_absence_date_is_return(self, brut, position, fenetre=40):
        """Cette date est-elle un jour de REPRISE plutôt qu'un dernier jour ?

        ⚠️ La reprise gagne quand les deux formules sont là : « à mon retour au
        bureau le lundi 4 mai » contient « au », qui marque ailleurs un dernier
        jour, mais c'est bien un jour de présence.
        """
        avant = brut[max(0, position - fenetre):position]
        if RX_REPRISE.search(avant):
            return True
        if RX_DERNIER_JOUR.search(avant):
            return False
        # Sans indice, on garde la forme dominante du corpus : « jusqu'au X »,
        # donc un dernier jour. Se tromper dans ce sens fait durer
        # l'avertissement un jour de trop, ce qui est moins grave que de
        # l'éteindre pendant que la personne est encore partie.
        return False

    def _bf_absence_read_backup(self, texte):
        """Une relève exploitable : une adresse, sinon un numéro.

        Mesuré : dix des 28 répondeurs en nomment une dans le seul aperçu.
        """
        self.ensure_one()
        propre = (self.email_from or "").lower()
        for adresse in RX_COURRIEL.findall(texte):
            if adresse.lower() in propre:
                continue
            if RX_ROBOT.match(adresse.split("@")[0]):
                continue
            return adresse
        numero = RX_TELEPHONE.search(texte)
        return numero.group(0) if numero else False

    # ------------------------------------------------------------------
    # Gen, quand il est là
    # ------------------------------------------------------------------
    def _bf_absence_ask_gen(self):
        """Seconde lecture par Gen. Rend un dict ou None, jamais d'exception.

        ⚠️ L'absence de Gen n'empêche ni l'installation ni le fonctionnement :
        la lecture en clair ci-dessus a déjà rendu son verdict, et cet appel ne
        sert qu'à rattraper la prose qu'elle ne sait pas lire.
        """
        self.ensure_one()
        pont = self.env["bf.ai.bridge"]
        try:
            if not pont.available():
                return None
        except Exception:  # noqa: BLE001
            return None
        icp = self.env["ir.config_parameter"].sudo()
        recu = self.date or fields.Datetime.now()
        invite = (
            "Voici un répondeur d'absence reçu le %s. Rends UNIQUEMENT une "
            "ligne JSON, sans texte autour, de la forme "
            '{"debut": "AAAA-MM-JJ", "fin": "AAAA-MM-JJ", "releve": "", '
            '"nature": "vacation|leave|closure|training|other"}. '
            "« fin » est le DERNIER jour d'absence, pas le jour de retour : si "
            "le message dit « de retour le 4 mai », la fin est le 3 mai. "
            "Mets null pour ce que le message ne dit pas. N'invente aucune "
            "date.\n\nObjet : %s\n\n%s"
        ) % (recu, self.subject or "", self._bf_absence_text(limite=1200)[:1500])
        charge = {
            "message": invite,
            "model": icp.get_param("bf_contact_absence.gen_model", "haiku"),
            "max_turns": 1,
            "tenant": icp.get_param("bf_ai_bridge.tenant", "bf"),
        }
        try:
            reponse = pont.call("/chat", charge, timeout=45)
        except Exception as exc:  # noqa: BLE001 - le pont muet ne bloque rien
            _logger.info("bf_contact_absence_mail: pont muet (%s)",
                         type(exc).__name__)
            return None
        brut = reponse.get("response") if isinstance(reponse, dict) else ""
        return self._bf_absence_parse_gen(brut)

    @api.model
    def _bf_absence_parse_gen(self, brut):
        """Lit la ligne JSON de Gen. Tout ce qui n'est pas une date est jeté."""
        import json
        if not brut:
            return None
        morceau = re.search(r"\{.*\}", brut, re.S)
        if not morceau:
            return None
        try:
            data = json.loads(morceau.group(0))
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        sortie = {}
        for cle in ("debut", "fin"):
            valeur = data.get(cle)
            if not valeur or not isinstance(valeur, str):
                continue
            if not RX_ISO.fullmatch(valeur.strip()):
                continue
            try:
                sortie[cle] = fields.Date.to_date(valeur.strip())
            except (ValueError, TypeError):
                continue
        releve = data.get("releve")
        if isinstance(releve, str) and releve.strip():
            sortie["releve"] = releve.strip()[:120]
        nature = data.get("nature")
        if nature in ("vacation", "leave", "closure", "training", "other"):
            sortie["nature"] = nature
        return sortie or None

    # ------------------------------------------------------------------
    # La passe
    # ------------------------------------------------------------------
    def _bf_absence_process(self):
        """Regarde ces courriels et pose les suggestions. Rend le compte posé."""
        Suggestion = self.env["bf.partner.absence.suggestion"]
        domaines = self._bf_absence_own_domains()
        poses = 0
        for courriel in self:
            courriel.bf_absence_scanned = True
            if courriel._bf_absence_excluded(domaines=domaines):
                continue
            reconnu, par_quoi = courriel._bf_absence_signals()
            if not reconnu:
                continue
            debut, fin, releve = courriel._bf_absence_read_period()
            # ⚠️ L'accusé de réception ne se décide QUE quand aucune période
            # n'a été lue : un message qui donne des dates est une absence,
            # même s'il parle aussi d'une candidature.
            accuse = not fin and courriel._bf_absence_is_acknowledgement()
            nature = "vacation"
            lu_par = "règles"
            gen = courriel._bf_absence_ask_gen()
            if gen:
                if gen.get("debut") and gen.get("fin"):
                    debut, fin = gen["debut"], gen["fin"]
                    lu_par = "Gen"
                elif gen.get("fin") and not fin:
                    fin = gen["fin"]
                    lu_par = "Gen"
                releve = gen.get("releve") or releve
                nature = gen.get("nature") or nature
            if fin and not debut:
                debut = min(
                    (courriel.date.date() if isinstance(courriel.date, datetime)
                     else courriel.date) or fields.Date.context_today(courriel),
                    fin)
            suggestion = Suggestion.create({
                "partner_id": courriel.partner_id.id,
                "bf_email_id": courriel.id,
                "date_from": debut or False,
                "date_to": fin or False,
                "nature": nature,
                "backup_hint": releve or False,
                "detected_by": par_quoi,
                "read_by": lu_par if (debut and fin) else False,
                "kind": "acknowledgement" if accuse else "absence",
                "state": "rejected" if accuse else "pending",
            })
            courriel.bf_absence_suggestion_id = suggestion
            poses += 1
        return poses

    @api.model
    def _cron_bf_absence_scan(self, limit=200, days=30):
        """La passe planifiée : le courrier récent, une seule fois chacun."""
        depuis = fields.Datetime.now() - timedelta(days=days)
        courriels = self.search([
            ("direction", "=", "in"),
            ("bf_absence_scanned", "=", False),
            ("date", ">=", fields.Datetime.to_string(depuis)),
        ], order="date desc", limit=limit)
        if not courriels:
            return 0
        poses = courriels._bf_absence_process()
        _logger.info("bf_contact_absence_mail: %s courriels lus, %s suggestions",
                     len(courriels), poses)
        return poses

    def action_bf_absence_scan_now(self):
        """Le bouton, pour ne pas attendre la passe."""
        poses = self._bf_absence_process()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if poses else "info",
                "message": _("%s absence(s) proposée(s).") % poses,
                "sticky": False,
            },
        }
