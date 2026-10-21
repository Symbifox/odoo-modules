# -*- coding: utf-8 -*-
"""La page d'état d'un client hébergé : une adresse à lui donner, sans compte.

🔴 La page a SON jeton, pas l'`access_token` d'un autre modèle ni le
`signup_token` du partenaire : celui-là ouvre une inscription, celui-ci ne
doit ouvrir que la page.

Ce que la page montre, et rien d'autre (liste blanche bâtie ici, jamais un
enregistrement passé tel quel au gabarit) : pour chaque service ACTIF du client
commercial, son nom, son logiciel et sa version, son environnement, son adresse,
un état lissé avec l'heure du relevé et la disponibilité sur 30 jours ; puis les
mises à jour réussies des 30 derniers jours. Jamais le code HST, le serveur, le
conteneur, les notes, le message d'erreur de la sonde ni le code HTTP.

Toutes les méthodes qui préparent la page commencent par `_` : un compte portail
parle à `call_kw`, et une méthode publique est appelable par lui.
"""
import secrets

import pytz
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import consteq
from odoo.tools.misc import format_date, format_datetime, format_time, formatLang

GROUPE_PUBLICATION = "hosting_management.group_hosting_manager"
LONGUEUR_MAX_JETON = 128
LONGUEUR_MIN_JETON = 32
# Au-delà, la sonde s'est tue : afficher son dernier « en service » serait une
# fausse bonne nouvelle.
FRAICHEUR_MAX = timedelta(minutes=15)
FENETRE_DISPONIBILITE = timedelta(days=30)
ORDRE_ENVIRONNEMENT = {"production": 0, "staging": 1, "testing": 2, "development": 3}


def jetons_egaux(attendu, jeton):
    """Compare deux jetons en temps constant, sans jamais lever.

    ⚠️ `consteq` lève `TypeError` sur une chaîne non ASCII : tout jeton hors
    forme est refusé AVANT la comparaison (repris de bf_stepbystep_clients).
    """
    if not isinstance(attendu, str) or not isinstance(jeton, str):
        return False
    if not attendu or not jeton or len(jeton) > LONGUEUR_MAX_JETON:
        return False
    if not attendu.isascii() or not jeton.isascii():
        return False
    return consteq(attendu, jeton)


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Les deux champs portent le groupe : un `groups=` de vue ne ferme rien, le
    # champ s'écrirait par RPC. Et la vue porte aussi le groupe sur chaque nœud,
    # sinon un interne sans lui n'ouvre plus aucune fiche contact.
    hosting_status_published = fields.Boolean(
        string="Status page published",
        copy=False,
        groups=GROUPE_PUBLICATION,
        help="Opens the status page to whoever holds its address. Unchecked, "
             "the address answers \"not found\", even with the right token.",
    )
    hosting_status_token = fields.Char(
        string="Status page token",
        copy=False,
        readonly=True,
        groups=GROUPE_PUBLICATION,
    )

    @api.constrains("hosting_status_token")
    def _hosting_status_controler_le_jeton(self):
        """Vide, la page ne s'ouvre à personne ; sinon 32 à 128 caractères ASCII
        visibles, sans espace (le jeton est un segment d'adresse)."""
        for partenaire in self.sudo():
            jeton = partenaire.hosting_status_token
            if not jeton:
                continue
            if (not LONGUEUR_MIN_JETON <= len(jeton) <= LONGUEUR_MAX_JETON
                    or not all("!" <= c <= "~" for c in jeton)):
                raise ValidationError(_(
                    "The status page token must be %(min)s to %(max)s printable "
                    "ASCII characters, without spaces.",
                    min=LONGUEUR_MIN_JETON, max=LONGUEUR_MAX_JETON))

    # ------------------------------------------------------------------
    # Publication, réservée aux gestionnaires d'hébergement
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, liste_valeurs):
        """🔴 Odoo 18 ne contrôle PAS le `groups=` d'un champ à la création : un
        gestionnaire de contacts créait une société « publiée » avec le jeton de
        son choix (par create, load ou une clé `default_` du contexte), puis y
        rattachait le contact d'un vrai client. Contrôlé APRÈS super(), qui a
        appliqué les défauts, donc tous les chemins passent ici."""
        partenaires = super().create(liste_valeurs)
        if not self.env.su and not self.env.user.has_group(GROUPE_PUBLICATION):
            if any(p.hosting_status_published or p.hosting_status_token for p in partenaires.sudo()):
                raise AccessError(_(
                    "Only a hosting manager can publish a client's status page."))
        return partenaires

    def _hosting_status_est_commercial(self):
        """La règle d'Odoo, sans lire `commercial_partner_id` stocké : il est vide
        en base sur des fiches anciennes."""
        self.ensure_one()
        return bool(self.is_company or not self.parent_id)

    def _hosting_status_exiger_le_droit(self):
        if not self.env.su and not self.env.user.has_group(GROUPE_PUBLICATION):
            raise AccessError(_(
                "The status page makes this client's services readable without an "
                "account. This action requires the \"Hosting Manager\" group."))

    def _hosting_status_exiger_un_client_commercial(self):
        for partenaire in self:
            if not partenaire._hosting_status_est_commercial():
                raise ValidationError(_(
                    "A status page belongs to the client company (%(company)s), "
                    "not to one of its contacts.",
                    company=partenaire.parent_id.display_name))

    def action_hosting_status_publish(self):
        self._hosting_status_exiger_le_droit()
        self._hosting_status_exiger_un_client_commercial()
        for partenaire in self.sudo():
            if not partenaire.hosting_status_token:
                partenaire.hosting_status_token = secrets.token_urlsafe(32)
            partenaire.hosting_status_published = True
        return True

    def action_hosting_status_unpublish(self):
        self._hosting_status_exiger_le_droit()
        self.sudo().write({"hosting_status_published": False})
        return True

    def action_hosting_status_regenerate_token(self):
        """L'ancienne adresse cesse de répondre ; la nouvelle est à redonner."""
        self._hosting_status_exiger_le_droit()
        for partenaire in self.sudo():
            partenaire.hosting_status_token = secrets.token_urlsafe(32)
        return True

    def _hosting_status_url(self):
        self.ensure_one()
        return "%s/etat/%s/%s" % (
            self.get_base_url().rstrip("/"), self.id, self.sudo().hosting_status_token)

    def action_hosting_status_copy_link(self):
        """L'adresse complète, prête à coller dans un courriel.

        ⚠️ Le même droit que publier : la méthode est publique, donc appelable par
        RPC par quiconque lit le partenaire, portail compris.
        """
        self.ensure_one()
        self._hosting_status_exiger_le_droit()
        if not self.sudo().hosting_status_published:
            raise ValidationError(_("Publish the status page first."))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "sticky": True,
                "title": _("Status page address"),
                "message": self._hosting_status_url(),
            },
        }

    # ------------------------------------------------------------------
    # Ce que voit le client (appelé en sudo par le contrôleur)
    # ------------------------------------------------------------------

    def _hosting_status_services(self):
        self.ensure_one()
        services = self.env["hosting.service"].sudo().search([
            # Les services rattachés à la SOCIÉTÉ seulement. Ni `child_of` (il prend ses
            # sociétés enfants), ni ceux de ses contacts : déplacer un contact sous une
            # autre société est un droit de gestion des contacts, et ferait paraître ses
            # services sur la page d'un autre client. Sur une base réelle, aucun service actif n'est
            # rattaché à un contact : le périmètre ne retire rien.
            ("partner_id", "=", self.id),
            ("state", "=", "active"),
        ])
        return services.sorted(lambda s: (ORDRE_ENVIRONNEMENT.get(s.environment, 9), s.name or ""))

    @api.model
    def _hosting_status_seuil(self):
        """Le même seuil que l'alerte : un seul relevé en défaut (un redémarrage)
        ne met pas la page au rouge."""
        try:
            seuil = int(self.env["ir.config_parameter"].sudo().get_param(
                "hosting.health_alert_min_consecutive", "2"))
        except ValueError:
            seuil = 2
        return max(seuil, 1)

    @staticmethod
    def _hosting_status_surveille(service):
        """Le même périmètre que la sonde (`_cron_health_check`)."""
        return bool(service.server_url or service.health_kind != "http")

    def _hosting_status_etat(self, service, maintenant, seuil, en_fenetre=()):
        """up, error, unreachable, maintenance, unknown ou not_monitored.

        `en_fenetre` : les services qu'une fenêtre ANNONCÉE couvre en ce moment.
        Pendant la fenêtre, le client lit « Maintenance en cours », pas « Erreur ».
        """
        if service.id in en_fenetre:
            return "maintenance"
        if not self._hosting_status_surveille(service):
            return "not_monitored"
        if service.health_silence_until and service.health_silence_until > maintenant:
            return "maintenance"
        if not service.last_health_check or maintenant - service.last_health_check > FRAICHEUR_MAX:
            return "unknown"
        releves = self.env["hosting.health.check"].sudo().search(
            [("service_id", "=", service.id)], order="check_date desc, id desc", limit=seuil)
        if len(releves) < seuil:
            releves = releves[:1]
        if any(r.status == "up" for r in releves):
            return "up"
        return "error" if releves[0].status == "degraded" else "unreachable"

    @staticmethod
    def _hosting_status_adresse(service):
        adresse = (service.domain_name or service.server_url or "").strip()
        for prefixe in ("https://", "http://"):
            if adresse.startswith(prefixe):
                adresse = adresse[len(prefixe):]
        return adresse.split("/")[0].rpartition("@")[2]

    @staticmethod
    def _hosting_status_meme_jour(debut, fin, tz):
        zone = pytz.timezone(tz)
        return pytz.utc.localize(debut).astimezone(zone).date() == pytz.utc.localize(fin).astimezone(zone).date()

    def _hosting_status_fenetres(self, services, maintenant):
        """Les fenêtres annoncées qui touchent ce client (à venir, en cours, et finies depuis
        moins de 30 jours), en dictionnaires. 🔴 Ne cite que SES services : une fenêtre de
        serveur touche plusieurs clients, et l'objet comme l'impact sont écrits pour tous."""
        if "hosting.maintenance.window" not in self.env:
            return []
        tz = self.tz or self.env.user.tz or "America/Toronto"
        resultat = []
        for f in self.env["hosting.maintenance.window"]._hosting_windows_for_services(services):
            les_miens = f.service_ids & services
            if not les_miens:
                continue
            if maintenant < f.date_start:
                phase = "upcoming"
            elif maintenant <= f.date_end:
                phase = "in_progress"
            else:
                phase = "done"
            resultat.append({
                "name": f.name or "",
                "impact": f.impact or "",
                "start_text": format_datetime(self.env, f.date_start, tz=tz, dt_format="short",
                                              lang_code=self.env.lang),
                # Même jour : l'heure de fin seulement (« 21 h 30 → 23 h 59 »).
                "end_text": (format_time(self.env, f.date_end, tz=tz, time_format="short",
                                         lang_code=self.env.lang)
                             if self._hosting_status_meme_jour(f.date_start, f.date_end, tz)
                             else format_datetime(self.env, f.date_end, tz=tz, dt_format="short",
                                                  lang_code=self.env.lang)),
                "services": ", ".join(les_miens.sorted("name").mapped("name")),
                "service_ids": les_miens.ids,
                "phase": phase,
            })
        return resultat

    def _hosting_status_valeurs(self):
        """Le dictionnaire du gabarit : des chaînes et des nombres, jamais un
        enregistrement (sauf la société émettrice, pour son logo et son nom)."""
        self.ensure_one()
        maintenant = fields.Datetime.now()
        seuil = self._hosting_status_seuil()
        services = self._hosting_status_services()
        # Les libellés de hosting_management sont écrits en français sans traduction
        # anglaise : une page en anglais affichait « Préproduction ». Table propre.
        environnements = {
            "production": _("Production"),
            "staging": _("Staging"),
            "testing": _("Test"),
            "development": _("Development"),
        }
        avec_releves = set(self.env["hosting.health.check"].sudo().search([
            ("service_id", "in", services.ids),
            ("check_date", ">=", maintenant - FENETRE_DISPONIBILITE),
        ]).service_id.ids)
        fenetres = self._hosting_status_fenetres(services, maintenant)
        en_fenetre = {sid for f in fenetres if f["phase"] == "in_progress" for sid in f["service_ids"]}
        lignes = []
        for s in services:
            etat = self._hosting_status_etat(s, maintenant, seuil, en_fenetre)
            lignes.append({
                "name": s.name or "",
                "software": s.software_id.name or "",
                "version": s.installed_version_id.version or "",
                "environment": s.environment or "",
                "environment_label": environnements.get(s.environment, ""),
                "address": self._hosting_status_adresse(s),
                "state": etat,
                "uptime": (round(s.uptime_30d, 2)
                           if etat != "not_monitored" and s.id in avec_releves else None),
            })
            lignes[-1]["uptime_text"] = (
                "%s %%" % formatLang(self.env, lignes[-1]["uptime"], digits=2)
                if lignes[-1]["uptime"] is not None else "")
        verifies = [s.last_health_check for s in services
                    if self._hosting_status_surveille(s) and s.last_health_check]
        journaux = self.env["hosting.update.log"].sudo().search([
            ("service_id", "in", services.ids),
            ("success", "=", True),
            ("update_date", ">=", maintenant - FENETRE_DISPONIBILITE),
        ], order="update_date desc, id desc", limit=20)
        tz = self.tz or self.env.user.tz or "America/Toronto"
        mises_a_jour = [{
            "date": j.update_date,
            "date_text": format_date(
                self.env, fields.Datetime.context_timestamp(self.with_context(tz=tz), j.update_date),
                lang_code=self.env.lang),
            "service": j.service_id.name or "",
            "from": j.from_version_id.version or "",
            "to": j.to_version_id.version or "",
        } for j in journaux]
        verifie = max(verifies) if verifies else None
        for f in fenetres:
            del f["service_ids"]
        return {
            "html_lang": (self.env.lang or "en_US").replace("_", "-"),
            # <title> n'est pas traduit par Odoo dans un gabarit : passé ici.
            "page_title": _("Status of your services"),
            "footer_text": _(
                "\"Error\" means the service answered with an error; \"Unreachable\" means it "
                "did not answer. A status changes only after %(count)s consecutive checks.",
                count=seuil),
            "client_name": self.name or "",
            "services": lignes,
            "checked_at": verifie,
            "checked_at_text": (format_datetime(self.env, verifie, tz=tz, dt_format="short",
                                                lang_code=self.env.lang) if verifie else ""),
            "updates": mises_a_jour,
            "windows_ahead": [f for f in fenetres if f["phase"] != "done"],
            # La plus récente d'abord dans l'historique.
            "windows_past": [f for f in reversed(fenetres) if f["phase"] == "done"],
            # Les titres des colonnes, répétés devant chaque valeur sur un téléphone.
            "label_address": _("Address"),
            "label_version": _("Version"),
            "label_uptime": _("30 days"),
        }
