# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Ce que la vue calendrier demande au serveur.

Deux lectures, sans rien écrire :

- `work_hours` : les heures de travail de l'usager connecté sur la plage
  affichée, pour les ombrer dans la grille ;
- `duplicate_defaults` : les valeurs d'un événement à recopier dans un
  formulaire neuf (« Dupliquer » de la bulle). Rien n'est créé ici : la copie
  n'existe, et ses invités ne sont prévenus, qu'à l'enregistrement.
"""

import re
from datetime import datetime, time, timedelta
from urllib.parse import unquote

import pytz
from lxml import etree
from lxml import html as lxml_html
from markupsafe import escape

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

# Une semaine affichée, avec la seconde de fin que la vue y ajoute. Au-delà
# (mois, année), la grille n'ombre rien : FullCalendar répète un motif
# HEBDOMADAIRE, juste pour une semaine et faux pour un mois (jours fériés,
# changement d'heure).
PLAGE_MAX = timedelta(days=7, hours=1)

# Jamais recopiés par « Dupliquer » :
# - le lien vers la fiche d'origine (une copie qui pointerait vers la même
#   opportunité ou la même tâche se ferait passer pour elle) ;
# - le fil de discussion et les activités (one2many, exclus plus bas).
CHAMPS_JAMAIS = {"id", "res_id", "res_model", "res_model_id"}

# Jamais recopiés, sur TOUT modèle : la salle de visio (lien PUBLIC : le client B recevrait
# celle du client A) et ce qui identifie la fiche d'origine (UID de l'import
# .ics, jeton d'invitation), le sous-dossier Nextcloud d'une tâche.
CHAMPS_JAMAIS_PAR_NOM = {
    "videocall_location", "videocall_channel_id", "access_token", "event_identifier",
    "nc_task_subfolder",
}

# Jamais recopiés sur un modèle précis : l'organisateur d'une réservation
# (celui qui duplique l'organise, comme sur l'Agenda).
CHAMPS_JAMAIS_PAR_MODELE = {
    "resource.booking": {"user_id"},
}

# Un lien de visio ne passe pas non plus par le lieu ni par la description
# (sur une base réelle, le lieu de la plupart des rencontres à visio EST le
# lien Talk). Un lien de visio : celui de la fiche (`videocall_location`), ou
# une adresse de salle connue.
HOTES_VISIO = (
    "meet.google.com", "teams.microsoft.com", "teams.live.com", "zoom.us",
    "meet.jit.si", "whereby.com",
)
# Nextcloud Talk (`/call/<jeton>`, `/index.php/call/<jeton>`), visio d'Odoo.
CHEMIN_SALLE = re.compile(r"/call/[A-Za-z0-9]{4,}(?:/|$)|/calendar/join_videocall/", re.IGNORECASE)
# Une adresse dans un texte : en clair, ou encodée (`https%3A%2F%2F...` : copies
# d'invitations collées telles quelles, liens de redirection).
URL_DANS_UN_TEXTE = re.compile(
    r"https?://[^\s\"'<>]+"
    r"|https?%3A%2F%2F(?:[\w.~+\-]|%(?!2[027]|3[CcEe])[0-9A-Fa-f]{2})+"
    # Sans « https:// » (« meet.google.com/abc-defg-hij » tapé dans le lieu) :
    # seulement les hôtes connus.
    r"|(?<![\w.@/-])(?:[\w-]+\.)*(?:" + "|".join(re.escape(h) for h in HOTES_VISIO)
    + r")/[^\s\"'<>]*",
    re.IGNORECASE,
)
# Hôte et chemin de chaque adresse d'un texte décodé (y compris celle qu'une
# adresse de redirection porte en paramètre).
ADRESSE = re.compile(r"https?://([^/\s\"'<>?#&]+)([^\s\"'<>?#&]*)", re.IGNORECASE)
PONCTUATION_FINALE = ".,;:!?)]}"

# Ce qui n'existe qu'une fois par rencontre :
# un lien many2one vers l'un de ces modèles n'est jamais recopié, quel que soit
# le modèle dupliqué (OdJ, compte rendu, activité, tâche née d'une rencontre).
MODELES_UNE_FOIS_PAR_RENCONTRE = {"calendar.event", "meeting.agenda", "meeting.record"}

# Où « Dupliquer » ne peut qu'échouer ou tromper : une activité naît de sa
# fiche, un congé de son circuit d'approbation. Masqué dans la bulle, refusé ici.
MODELES_SANS_DUPLIQUER = {"mail.activity", "hr.leave"}

# Sur l'Agenda, une copie est UN événement. Recopier la récurrence ferait d'un
# « Dupliquer » sur une occurrence une série neuve entière à l'enregistrement.
# `event_tz` en fait partie : écrit sur un événement non récurrent, il fait
# refuser l'écriture (« récurrence avec Cet événement »).
CHAMPS_RECURRENCE = {
    "recurrency", "recurrence_id", "follow_recurrence", "recurrence_update",
    "rrule", "rrule_type", "rrule_type_ui", "interval", "count", "end_type",
    "until", "month_by", "day", "weekday", "byday", "event_tz",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
}

# Sur l'Agenda, seuls ces champs passent :
# liste REVUE, champ par champ. Un champ `copy=True` ajouté par un module
# installé plus tard n'est pas recopié tant qu'il n'est pas classé ici ou dans
# CHAMPS_EVENEMENT_EXCLUS ; l'essai `test_champs_copiables_tous_classes` le
# signale.
CHAMPS_EVENEMENT_RECOPIES = {
    # calendar
    "name", "start", "stop", "allday", "duration", "description", "location",
    "partner_ids", "categ_ids", "privacy", "show_as", "active",
    # calendar (rappels : bf_email_management y pose un défaut)
    "alarm_ids",
    # couleurs (calendar_nextcloud_sync, bf_color)
    "color", "color_hex",
    # bf_meeting : dispenses d'OdJ et de tableau de bord
    "bf_skip_agenda", "bf_skip_dashboard",
    # bf_nfc_room : la salle
    "bf_nfc_room_id",
}
CHAMPS_EVENEMENT_EXCLUS = CHAMPS_RECURRENCE | {
    # La salle de visio (Talk, lien public) appartient à la rencontre
    # d'origine ; la copie en reçoit une neuve. `event_identifier` (UID de
    # l'import .ics) et `access_token` (jeton d'invitation) l'identifient.
    "videocall_location", "videocall_channel_id", "videocall_source",
    "event_identifier", "access_token",
    # L'opportunité (une activité y serait posée au nom de l'organisateur
    # d'origine).
    "opportunity_id",
    # L'organisateur est celui qui duplique, et ce qui le suit avec lui :
    # son calendrier CalDAV, les responsables d'OdJ et de compte rendu (par
    # défaut, l'organisateur).
    "user_id", "bf_dav_calendar_id", "bf_agenda_responsible_id", "bf_minutes_responsible_id",
    # L'état propre à la rencontre d'origine : statut (une rencontre annulée
    # donnerait une copie annulée), étapes ignorées du tableau de bord.
    "bf_event_status", "bf_dashboard_skipped_steps",
}


def _decode(texte):
    """Une adresse décodée, même encodée deux fois (redirection d'invitation)."""
    return unquote(unquote(texte or ""))


def est_lien_visio(url, propres=()):
    """Vrai si `url` mène à une salle de visio : l'une des `propres` (celle de
    la fiche), ou une salle connue (HOTES_VISIO, Nextcloud Talk, Odoo)."""
    decode = _decode(url).strip()
    if not decode:
        return False
    if "://" not in decode:
        decode = "https://" + decode
    for propre in propres:
        propre = _decode(propre).strip().rstrip("/")
        if propre and propre in decode:
            return True
    for hote, chemin in ADRESSE.findall(decode):
        hote = hote.rsplit("@", 1)[-1].split(":")[0].lower()
        if (any(hote == h or hote.endswith("." + h) for h in HOTES_VISIO)
                or "jitsi" in hote or "jitsi" in chemin.lower()):
            return True
        if CHEMIN_SALLE.search(chemin):
            return True
    return False


def texte_sans_liens_visio(texte, propres=()):
    """`texte` sans ses adresses de visio (le reste intact)."""
    def remplacer(m):
        adresse = m.group(0)
        coeur = adresse.rstrip(PONCTUATION_FINALE)
        return adresse[len(coeur):] if est_lien_visio(coeur, propres) else adresse
    return URL_DANS_UN_TEXTE.sub(remplacer, texte or "")


def contient_lien_visio(texte, propres=()):
    return any(
        est_lien_visio(m.group(0).rstrip(PONCTUATION_FINALE), propres)
        for m in URL_DANS_UN_TEXTE.finditer(texte or "")
    )


def html_sans_liens_visio(valeur, propres=()):
    """HTML `valeur` sans ses liens de visio : un `<a>` qui mène à une salle est
    retiré avec son texte (« Rejoindre la réunion »), une adresse en clair est
    retirée du texte et des attributs ; le reste du HTML est gardé tel quel."""
    texte = str(valeur or "")
    if not contient_lien_visio(texte, propres):
        return valeur
    try:
        racine = lxml_html.fragment_fromstring(texte, create_parent="div")
    except (etree.ParserError, ValueError):
        return texte_sans_liens_visio(texte, propres)
    for lien in list(racine.iter("a")):
        if est_lien_visio(lien.get("href") or "", propres):
            parent, queue = lien.getparent(), lien.tail or ""
            precedent = lien.getprevious()
            if precedent is not None:
                precedent.tail = (precedent.tail or "") + queue
            else:
                parent.text = (parent.text or "") + queue
            parent.remove(lien)
    for element in racine.iter():
        if not isinstance(element.tag, str):
            continue  # commentaire, instruction
        if element.text:
            element.text = texte_sans_liens_visio(element.text, propres)
        if element.tail and element is not racine:
            element.tail = texte_sans_liens_visio(element.tail, propres)
        for nom, val in element.attrib.items():
            if contient_lien_visio(val, propres):
                element.set(nom, texte_sans_liens_visio(val, propres))
    resultat = str(escape(racine.text or "")) + "".join(
        lxml_html.tostring(enfant, encoding="unicode") for enfant in racine)
    # Filet : une adresse qu'aucune des passes n'aurait vue (forme inattendue).
    if contient_lien_visio(resultat, propres):
        resultat = texte_sans_liens_visio(resultat, propres)
    return resultat


class WebCalendarErgonomics(models.AbstractModel):
    _name = "web.calendar.ergonomics"
    _description = "Calendar view helpers"

    # ------------------------------------------------------------------
    # Heures de travail
    # ------------------------------------------------------------------

    @api.model
    def _work_resource(self, user):
        """La ressource de travail de l'usager, ou une ressource vide.

        La fiche d'employé d'abord : un usager peut porter plusieurs
        ressources (prises de rendez-vous, plages techniques), et c'est celle
        de l'employé qui dit quand il travaille. Sans module RH, la première
        ressource « usager » de la société courante.
        """
        Resource = self.env["resource.resource"].sudo()
        company = self.env.company
        if "hr.employee" in self.env:
            employee = self.env["hr.employee"].sudo().search([
                ("user_id", "=", user.id),
                ("company_id", "=", company.id),
            ], limit=1)
            if employee.resource_id:
                return employee.resource_id
        return Resource.search([
            ("user_id", "=", user.id),
            ("resource_type", "=", "user"),
            ("company_id", "in", [company.id, False]),
        ], order="id", limit=1)

    @api.model
    def work_hours(self, start, end, tz_name=None):
        """Heures de travail sur [start, end], au format `businessHours` de FullCalendar.

        `start`/`end` : chaînes UTC de la vue. `tz_name` : le fuseau de la
        grille (celui du navigateur, que la vue affiche).

        Rend False quand il n'y a rien à ombrer (pas d'horaire, plage trop
        longue, compte portail), une liste VIDE quand l'horaire existe mais ne
        travaille pas sur la plage (jour férié, fin de semaine) : toute la
        plage est alors hors des heures de travail.

        Les intervalles viennent du moteur d'Odoo (`_work_intervals_batch`),
        donc au fuseau de la ressource (à défaut, celui de l'horaire), sans la
        pause du midi, congés et jours fériés retirés ; ils sont ensuite
        convertis dans `tz_name`.
        """
        user = self.env.user
        if not user._is_internal():
            return False
        try:
            debut = fields.Datetime.to_datetime(start)
            fin = fields.Datetime.to_datetime(end)
        except (TypeError, ValueError):
            return False
        if not debut or not fin or fin <= debut or fin - debut > PLAGE_MAX:
            return False
        try:
            zone = pytz.timezone(tz_name or user.tz or "UTC")
        except pytz.UnknownTimeZoneError:
            zone = pytz.utc

        resource = self._work_resource(user)
        calendar = resource.calendar_id or self.env.company.resource_calendar_id
        if not calendar:
            return False
        calendar = calendar.sudo()
        intervals = calendar._work_intervals_batch(
            pytz.utc.localize(debut), pytz.utc.localize(fin),
            resources=resource or None,
        )[resource.id]

        regles = []
        for de, a, _meta in intervals:
            de, a = de.astimezone(zone), a.astimezone(zone)
            # Coupé à minuit local : une règle FullCalendar tient dans un jour.
            while de < a:
                minuit = zone.localize(
                    datetime.combine(de.date() + timedelta(days=1), time.min))
                bout = min(a, minuit)
                fin_texte = "24:00" if bout == minuit else bout.strftime("%H:%M")
                if fin_texte != de.strftime("%H:%M"):
                    regles.append({
                        # FullCalendar : 0 = dimanche ; Python : 0 = lundi.
                        "daysOfWeek": [(de.weekday() + 1) % 7],
                        "startTime": de.strftime("%H:%M"),
                        "endTime": fin_texte,
                    })
                de = bout
        return regles

    # ------------------------------------------------------------------
    # Dupliquer
    # ------------------------------------------------------------------

    @api.model
    def _duplicate_exclu(self, Model, champ):
        """Vrai si `champ` de `Model` ne passe jamais dans « Dupliquer »."""
        nom = champ.name
        if (
            nom in CHAMPS_JAMAIS
            or nom in CHAMPS_JAMAIS_PAR_NOM
            or nom in CHAMPS_JAMAIS_PAR_MODELE.get(Model._name, ())
            or champ.type in ("one2many", "binary")
            # Un champ réservé à un groupe que l'appelant n'a pas.
            # `copy_data()` le lit sans regarder `groups=`, `read()` le refuse.
            or not champ.is_accessible(self.env)
            or (champ.type == "many2one" and champ.comodel_name in MODELES_UNE_FOIS_PAR_RENCONTRE)
        ):
            return True
        if Model._name == "calendar.event":
            return nom not in CHAMPS_EVENEMENT_RECOPIES
        return False

    @api.model
    def duplicate_defaults(self, res_model, res_id):
        """Valeurs par défaut d'un formulaire neuf qui recopie la fiche `res_id`.

        Mêmes champs que `copy()` (`copy_data`), moins le lien vers la fiche
        d'origine, les one2many (fil, activités, participants : ils naissent
        des many2many à l'enregistrement), les champs que l'appelant ne peut
        pas lire, les liens vers une rencontre, et, sur l'Agenda, tout ce qui
        n'est pas dans la liste revue (CHAMPS_EVENEMENT_RECOPIES : ni la visio,
        ni l'organisateur, ni la récurrence...). Sur tout modèle, jamais la
        salle de visio : ni ses champs (CHAMPS_JAMAIS_PAR_NOM), ni un lieu qui
        est un lien de visio, ni ces liens dans la description. Sur l'Agenda,
        celui qui duplique est invité à sa copie. Lu avec les droits de
        l'usager : il faut pouvoir lire la fiche ET créer dans le modèle.
        """
        if not self.env.user._is_internal():
            raise AccessError(self.env._("Only internal users can duplicate from the calendar."))
        if res_model in MODELES_SANS_DUPLIQUER:
            raise UserError(self.env._("These records cannot be duplicated from the calendar."))
        if res_model not in self.env:
            return {}
        Model = self.env[res_model]
        Model.check_access("create")
        record = Model.browse(int(res_id)).exists()
        if not record:
            return {}
        record.check_access("read")

        exclus = {
            nom for nom, champ in Model._fields.items()
            if self._duplicate_exclu(Model, champ)
        }
        # Les champs copiables exclus passent en `default` : `copy_data` ne lit
        # alors ni les lignes des one2many ni les champs réservés.
        # Jamais le champ d'un `_inherits` : en `default`, il ferait sauter
        # tous les champs du parent.
        a_ne_pas_lire = {
            nom: False for nom in exclus
            if Model._fields[nom].copy and not Model._fields[nom].inherited
            and nom not in Model._inherits.values()
        }
        valeurs = record.copy_data(default=a_ne_pas_lire)[0]
        defauts = {}
        for nom, valeur in valeurs.items():
            champ = Model._fields.get(nom)
            if not champ or nom in exclus:
                continue
            if champ.type == "many2many":
                # Une seule forme, quelle que soit celle que rend copy_data.
                valeur = [(6, 0, record[nom].ids)]
            elif champ.type == "many2one":
                valeur = record[nom].id or False
            elif champ.type in ("date", "datetime"):
                valeur = champ.to_string(valeur) if valeur else False
            defauts[nom] = valeur

        # La salle de la fiche, pour la reconnaître dans le lieu et la
        # description (jamais rendue : le champ est exclu plus haut).
        propres = []
        champ_visio = Model._fields.get("videocall_location")
        if champ_visio and champ_visio.is_accessible(self.env):
            propres.append(record.videocall_location or "")
        champ = Model._fields.get("location")
        if defauts.get("location") and champ.type in ("char", "text"):
            lieu = str(defauts["location"])
            if lieu.strip() in [p.strip() for p in propres if p] or contient_lien_visio(lieu, propres):
                # Le lien seul : pas de lieu. Une salle ET un lien : la salle.
                lieu = texte_sans_liens_visio(lieu, propres).strip(" \t\r\n-–—|,;:/")
                if lieu.strip() in [p.strip() for p in propres if p]:
                    lieu = ""
                if lieu:
                    defauts["location"] = lieu
                else:
                    del defauts["location"]
        champ = Model._fields.get("description")
        if defauts.get("description") and champ.type in ("html", "char", "text"):
            if champ.type == "html":
                defauts["description"] = html_sans_liens_visio(defauts["description"], propres)
            else:
                defauts["description"] = texte_sans_liens_visio(defauts["description"], propres)

        # Celui qui duplique organise la copie : il en est l'un des invités
        # (`partner_ids` recopié remplace le défaut qui l'invitait).
        if Model._name == "calendar.event" and "partner_ids" in defauts:
            invites = list(defauts["partner_ids"][0][2])
            moi = self.env.user.partner_id.id
            if moi not in invites:
                invites.append(moi)
            defauts["partner_ids"] = [(6, 0, invites)]
        return defauts
