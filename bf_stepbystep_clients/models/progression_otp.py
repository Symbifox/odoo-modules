# -*- coding: utf-8 -*-
"""La double authentification de la page de progression : un code par courriel.

Deux modèles, et chacun ne garde qu'une empreinte :

- `bf.progression.otp` : un code EN VOL. Six chiffres, une demi-heure, cinq
  essais. Le code n'est jamais gardé en clair — seule son empreinte HMAC l'est,
  salée par mandat et par le secret de la base, comparée en temps constant.
- `bf.progression.device` : un appareil RECONNU. Le client qui a prouvé une fois
  qu'il lit le courriel du mandat n'a plus à le refaire pendant la durée du
  mandat. Le jeton de l'appareil vit dans un témoin, et la base n'en garde que
  l'empreinte : une lecture de la base ne rend pas un témoin utilisable.

🔴 Pourquoi un code et pas un mot de passe : le client d'un mandat de six heures
n'ouvrira pas un compte, et le lien tokenisé seul ouvre la page à quiconque a
reçu le courriel suivi. Le code prouve l'accès à la boîte, une fois par appareil.

🔴 Le courriel part par la FILE, déclenchée tout de suite. Un envoi synchrone
dans une route publique tient un fil HTTP pendant tout le SMTP : sur un
locataire à deux fils, deux chargements pendant une lenteur du relais SMTP (plusieurs
dizaines de secondes au DATA) suffisent à coucher tout le monde, usagers internes compris. La
file rend la main immédiatement ; l'échec se constate au chargement SUIVANT, sur
l'état du message, et la page le dit alors au client.
"""
import hashlib
import hmac
import logging
import secrets

from psycopg2 import IntegrityError, OperationalError

from odoo import _, api, fields, models
from odoo.tools import consteq
from odoo.tools.mail import email_normalize

_logger = logging.getLogger(__name__)

# Les plafonds, réglables par paramètre système comme ceux du dépôt.
# ⚠️ La demi-heure et non le quart : la file n'est pas instantanée, et un code
# qui meurt avant d'arriver ne vaut rien. C'est la durée que la maison emploie
# déjà pour le code d'accusé de `privacy_breach_notice`.
MINUTES_DEFAUT = 30
ESSAIS_DEFAUT = 5
ESSAIS_PAR_JOUR_DEFAUT = 20
CODES_PAR_HEURE_DEFAUT = 5
JOURS_APPAREIL_DEFAUT = 90
# Au-delà de cette grâce, un message encore « outgoing » dit que la file ne
# tourne pas : la page doit le dire au lieu d'annoncer un envoi. La file part en
# ~2 s avec le réveil du cron, et la grâce reste loin sous la vie du code.
GRACE_ENVOI_MINUTES = 5
# Un passage n'est noté qu'une fois par heure : chaque lecture devenait une
# écriture, donc un verrou de ligne, donc des rejeux dès que deux requêtes du
# même appareil se croisaient (le pont S3 signe ses parts en parallèle).
MINUTES_ENTRE_DEUX_PASSAGES = 60


def _empreinte(env, sel, valeur):
    """HMAC-SHA256 de `valeur`, salée par `sel` et par le secret de la base.

    Le secret ne sort jamais de la base, et l'empreinte change d'un locataire à
    l'autre : un code recopié d'une base à l'autre ne vaut rien.
    """
    secret = env["ir.config_parameter"].sudo().get_param("database.secret") or ""
    message = ("%s:%s" % (sel, valeur)).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class ProgressionOtp(models.Model):
    _name = "bf.progression.otp"
    _description = "Access code sent for a progression page"
    _order = "id desc"
    _rec_name = "project_id"

    project_id = fields.Many2one(
        "project.project", string="Mandate", required=True, ondelete="cascade",
        index=True)
    company_id = fields.Many2one(related="project_id.company_id", store=True)
    email = fields.Char(string="Sent to", readonly=True)
    # Les deux empreintes sont réservées au groupe système : un administrateur
    # de projets n'a aucune raison de les lire, et le module en ferme déjà
    # l'équivalent pour le jeton du portail.
    code_hash = fields.Char(
        string="Code fingerprint", readonly=True, groups="base.group_system")
    expires_at = fields.Datetime(string="Expires at", readonly=True, index=True)
    attempts = fields.Integer(string="Attempts", readonly=True, default=0)
    sent_at = fields.Datetime(string="Sent at", readonly=True)
    mail_id = fields.Many2one(
        "mail.mail", string="Message", readonly=True, ondelete="set null",
        groups="base.group_system")
    ip_address = fields.Char(string="IP address", readonly=True)

    # ------------------------------------------------------------------
    # Les plafonds
    # ------------------------------------------------------------------

    @api.model
    def _parametre(self, cle, defaut):
        valeur = self.env["ir.config_parameter"].sudo().get_param(
            "bf_stepbystep_clients.%s" % cle, defaut)
        try:
            return max(int(valeur), 1)
        except (TypeError, ValueError):
            return defaut

    @api.model
    def _minutes(self):
        return self._parametre("portal_otp_minutes", MINUTES_DEFAUT)

    @api.model
    def _essais(self):
        return self._parametre("portal_otp_attempts", ESSAIS_DEFAUT)

    @api.model
    def _essais_par_jour(self):
        return self._parametre("portal_otp_attempts_per_day", ESSAIS_PAR_JOUR_DEFAUT)

    @api.model
    def _codes_par_heure(self):
        return self._parametre("portal_otp_per_hour", CODES_PAR_HEURE_DEFAUT)

    # ------------------------------------------------------------------
    # L'état des codes d'un mandat
    # ------------------------------------------------------------------

    @api.model
    def _code_en_vol(self, projet):
        """Le code encore valide ET pas épuisé de ce mandat, s'il y en a un.

        ⚠️ Réutilisé tel quel plutôt que remplacé : un rechargement de page, un
        détonateur de liens ou un antivirus de courriel ne doit pas consommer un
        envoi ni invalider le code que le client est en train de lire.
        """
        return self.sudo().search([
            ("project_id", "=", projet.id),
            ("expires_at", ">", fields.Datetime.now()),
            ("attempts", "<", self._essais()),
        ], limit=1)

    @api.model
    def _code_epuise(self, projet):
        """Un code encore dans sa fenêtre mais dont les essais sont brûlés.

        🔴 Il BLOQUE l'envoi d'un code neuf. Sans ça, qui tient le lien brûlait
        cinq essais, rechargeait la page, et repartait avec cinq essais neufs :
        le plafond réel était 5 essais × 5 codes à l'heure, et le message disait
        au visiteur comment remettre le compteur à zéro.
        """
        return self.sudo().search([
            ("project_id", "=", projet.id),
            ("expires_at", ">", fields.Datetime.now()),
            ("attempts", ">=", self._essais()),
        ], limit=1)

    @api.model
    def _essais_du_jour(self, projet):
        depuis = fields.Datetime.subtract(fields.Datetime.now(), days=1)
        codes = self.sudo().search([
            ("project_id", "=", projet.id), ("sent_at", ">=", depuis)])
        return sum(codes.mapped("attempts"))

    def _tuer(self):
        """Le code ne vaut plus rien, mais sa ligne reste : elle compte au
        plafond horaire. L'effacer ferait du chemin d'échec un trou dans le
        plafond — chaque panne d'envoi rouvrirait la rafale."""
        self.sudo().write({"expires_at": fields.Datetime.now()})

    # ------------------------------------------------------------------
    # Envoyer
    # ------------------------------------------------------------------

    @api.model
    def _envoyer(self, projet, destinataire=None, ip=None):
        """Met un code en route pour le client du mandat.

        Rend `(otp, raison)`, où la raison dit à la page quoi afficher sans
        mentir : « sent » (un code est en route, neuf ou déjà en vol),
        « no_email », « too_many » (plafond horaire ou essais épuisés),
        « not_sent » (l'envoi du code précédent a échoué).
        """
        projet = projet.sudo()
        vide = self.sudo().browse()
        # 🔴 `is None`, pas `or` : une adresse donnée VIDE doit rester vide. Avec
        # un `or`, le repli du modèle rattrapait le payeur juste après que le
        # contrôleur ait refusé de traverser vers lui — le défaut déménageait au
        # lieu de disparaître, et la page annonçait un envoi « à  » (rien) alors
        # que le code partait chez le donneur d'ordre.
        if destinataire is None:
            destinataire = projet._progression_otp_courriel()
        # 🔴 Normalisée, pas seulement non vide. Une adresse mal formée (« n/a »,
        # « jean@ », « jean dupont@x.ca ») passait pour bonne : le code naissait,
        # la page annonçait un envoi « à  » (le masque ne trouve pas de @), et le
        # cœur d'Odoo EFFAÇAIT le message pour `mail_email_invalid` — donc son
        # absence se lisait ensuite comme un succès. Trois défauts d'un seul
        # champ sale, sur une base réelle de fiches de contact.
        destinataire = email_normalize(destinataire or "") or ""
        # ⚠️ Le normalisateur d'Odoo accepte une partie locale VIDE : « @x.ca »
        # passe, et « jean@@x.ca » est réduit à « @x.ca ». Rien de livrable, et
        # le masque aurait rendu « ***@x.ca ». Mesuré, pas supposé.
        if destinataire.startswith("@"):
            destinataire = ""
        if not destinataire:
            return vide, "no_email"
        # Si la file ne tourne pas, rien ne partira : le dire tout de suite
        # plutôt que de créer un code que personne ne recevra.
        if not self._file_courriel_tourne():
            return vide, "not_sent"

        en_vol = self._code_en_vol(projet)
        if en_vol:
            # ⚠️ L'échec d'envoi se constate ICI, au chargement suivant : la file
            # a rendu la main avant de savoir. Un message resté en « exception »
            # dit que le code n'arrivera jamais ; on le tue pour qu'un autre
            # puisse partir, et la page le dit au client.
            if en_vol._envoi_en_echec():
                # Le code précédent n'arrivera jamais. On le tue et on le DIT :
                # refaire aussitôt un envoi vers un relais qui vient de refuser
                # ne ferait qu'ajouter du silence, et le message « le code n'a
                # pas pu être envoyé » ne sortirait jamais. Un rechargement de
                # plus en fera partir un neuf, au prix d'un envoi du plafond.
                en_vol._tuer()
                return vide, "not_sent"
            return en_vol, "sent"

        if self._code_epuise(projet):
            return vide, "too_many"
        if self._essais_du_jour(projet) >= self._essais_par_jour():
            return vide, "too_many_today"

        # ⚠️ Même technique que le dépôt : une ÉCRITURE réelle de la ligne du
        # projet avant de compter, pour que deux demandes simultanées ne passent
        # pas toutes deux sous le plafond. L'erreur de sérialisation doit
        # REMONTER (les routes la laissent passer) : c'est Odoo qui rejoue.
        self.env.cr.execute(
            "UPDATE project_project SET write_date = write_date WHERE id = %s",
            (projet.id,))
        depuis = fields.Datetime.subtract(fields.Datetime.now(), hours=1)
        recents = self.sudo().search_count([
            ("project_id", "=", projet.id), ("sent_at", ">=", depuis)])
        if recents >= self._codes_par_heure():
            return vide, "too_many"

        code = "%06d" % secrets.randbelow(1000000)
        otp = self.sudo().create({
            "project_id": projet.id,
            "email": destinataire,
            "code_hash": _empreinte(self.env, "otp-%s" % projet.id, code),
            "expires_at": fields.Datetime.add(
                fields.Datetime.now(), minutes=self._minutes()),
            "sent_at": fields.Datetime.now(),
            "ip_address": ip,
        })
        courriel = otp._poster(code, destinataire)
        if not courriel:
            otp._tuer()
            return vide, "not_sent"
        otp.sudo().mail_id = courriel
        return otp, "sent"

    @api.model
    def _file_courriel_tourne(self):
        """Vrai si la file de courriel peut partir.

        🔴 `_trigger()` sur un cron DÉSACTIVÉ est un non-geste, et Odoo désactive
        lui-même un cron qui a planté — quatre crons de courriel ont été trouvés
        éteints sur une instance réelle, après un rejet en cascade du relais.
        Sans ce contrôle, la page annonçait « envoyé » pour toujours, sur tous
        les mandats à la fois, et rien ne le disait.
        """
        cron = self.env.ref(
            "mail.ir_cron_mail_scheduler_action", raise_if_not_found=False)
        return bool(cron and cron.sudo().active)

    def _envoi_en_echec(self):
        """Vrai si le message de ce code ne partira jamais.

        Trois états lus, et pas un seul :

        - « exception » : le relais a refusé, c'est l'échec le plus visible ;
        - « outgoing » passé la grâce : la file ne tourne pas ;
        - absent : le message est PARTI (`auto_delete`), donc on ne crie pas.

        ⚠️ Limite assumée : un relais qui fait supprimer le message sans laisser
        d'état (vu avec un relais réel) est indistinguable d'un succès. La
        cause la plus fréquente de cette suppression, l'adresse mal formée, est
        désormais écartée à l'entrée.
        """
        self.ensure_one()
        courriel = self.sudo().mail_id
        if not courriel or not courriel.exists():
            return False
        if courriel.state == "exception":
            return True
        if courriel.state == "outgoing" and self.sent_at:
            grace = fields.Datetime.subtract(
                fields.Datetime.now(), minutes=GRACE_ENVOI_MINUTES)
            return self.sent_at < grace
        return False

    def _poster_par_gabarit(self, gabarit, projet, code, destinataire):
        """Rend le `mail.mail` mis en file par le gabarit, ou un ensemble vide.

        Le code et la durée voyagent par le CONTEXTE : ils ne sont écrits nulle
        part sur le projet, et le gabarit les lit par `ctx`.
        """
        langue = self.env.context.get("lang") or projet.partner_id.lang
        try:
            courriel_id = gabarit.sudo().with_context(
                bf_progression_code=code,
                bf_progression_minutes=self._minutes(),
                bf_progression_lang=langue,
            ).send_mail(
                projet.id, force_send=False,
                email_values={"email_to": destinataire, "auto_delete": True})
        except (IntegrityError, OperationalError):
            raise
        except Exception:
            _logger.exception(
                "Code de progression non mis en file pour le projet %s", projet.id)
            return self.env["mail.mail"].sudo().browse()
        courriel = self.env["mail.mail"].sudo().browse(courriel_id)
        # ⚠️ Détaché du dossier : le gabarit a posé `model`/`res_id`, et le
        # chatter du projet aurait porté le code en clair.
        courriel.mail_message_id.sudo().write({"model": False, "res_id": False})
        try:
            self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
        except Exception:
            _logger.warning(
                "Réveil de la file refusé pour le projet %s ; le code partira au"
                " prochain passage", projet.id)
        return courriel

    def _poster(self, code, destinataire):
        """Met le courriel du code dans la file et la déclenche tout de suite.

        Rend le `mail.mail`, ou rien si même la mise en file a échoué.

        🔴 Par un `mail.template`, pour sa mise en page : `email_layout_xmlid`
        met le courriel dans l'enveloppe de l'instance — logo, pied, couleur de
        marque —, comme tous les autres courriels maison. Un `mail.mail` nu
        arrivait sans rien de tout ça.

        🔴 `auto_delete` : le corps porte le code EN CLAIR. Odoo supprime le
        message dès qu'il est parti, et avec lui son `mail.message` — sinon un
        vidage de base, une réplique ou une sauvegarde rendrait des codes
        vivants, et l'empreinte du modèle serait décorative.

        ⚠️ Le message est DÉTACHÉ du projet après la mise en file : un gabarit
        pose `model`/`res_id`, donc le code en clair aurait figuré au chatter du
        dossier — et y serait resté si l'envoi échouait.
        """
        self.ensure_one()
        projet = self.project_id.sudo()
        societe = projet.company_id or self.env.company
        gabarit = self.env.ref(
            "bf_stepbystep_clients.mail_template_progression_otp",
            raise_if_not_found=False)
        if gabarit:
            return self._poster_par_gabarit(gabarit, projet, code, destinataire)
        # Repli sans gabarit (données absentes) : le corps nu plutôt que rien.
        corps = _(
            "<p>Hello,</p>"
            "<p>Your access code for the progress page of your mandate is "
            "<strong style=\"font-size:1.4em;letter-spacing:.1em\">%(code)s</strong></p>"
            "<p>It expires in %(minutes)s minutes. If you did not ask for this "
            "code, you can ignore this message.</p>",
            code=code, minutes=self._minutes())
        try:
            courriel = self.env["mail.mail"].sudo().create({
                "subject": _("Your access code"),
                "body_html": corps,
                "email_to": destinataire,
                "email_from": societe.email_formatted or societe.email,
                "auto_delete": True,
                "state": "outgoing",
            })
        except (IntegrityError, OperationalError):
            # Concurrence : laisser remonter, c'est Odoo qui rejoue.
            raise
        except Exception:
            # ⚠️ Large à dessein : la pile de surcharges de `mail.mail` d'un
            # locataire (marque, impersonation, débranding, cc/cci) peut lever
            # un `UserError`, qui remontait au filet de la route et rendait 404
            # sur un lien valide au lieu de dire que le code n'est pas parti.
            _logger.exception(
                "Code de progression non mis en file pour le projet %s", projet.id)
            return self.env["mail.mail"].sudo().browse()
        try:
            # « Maintenant », pas au prochain passage aux cinq minutes.
            self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
        except Exception:
            # ⚠️ À PART : le message est créé et partira au prochain passage de
            # la file. Tuer le code ici laisserait partir un code déjà mort.
            _logger.warning(
                "Réveil de la file refusé pour le projet %s ; le code partira au"
                " prochain passage", projet.id)
        return courriel

    # ------------------------------------------------------------------
    # Vérifier
    # ------------------------------------------------------------------

    @api.model
    def _verifier(self, projet, code):
        """Rend la RAISON du verdict : « ok », « wrong », « too_many_today » ou
        « expired ».

        ⚠️ Pas un booléen : au plafond du jour, la page disait au client qui
        tient le BON code que son code ne correspond pas.
        """
        code = (code or "").strip()
        otp = self._code_en_vol(projet)
        if not otp or not code:
            return "expired"
        if self._essais_du_jour(projet) >= self._essais_par_jour():
            return "too_many_today"
        otp = otp.sudo()
        otp.attempts += 1
        attendu = otp.code_hash or ""
        donne = _empreinte(self.env, "otp-%s" % projet.id, code)
        if not attendu or not consteq(attendu, donne):
            # La suite d'erreurs se compte au mandat, et avise son responsable
            # au seuil.
            projet.sudo()._progression_otp_noter_un_echec()
            return "wrong"
        # Un code juste est consommé : il ne sert pas deux fois.
        otp._tuer()
        projet.sudo()._progression_otp_remettre_la_suite()
        return "ok"


class ProgressionDevice(models.Model):
    _name = "bf.progression.device"
    _description = "Device recognised for a progression page"
    _order = "id desc"
    _rec_name = "project_id"

    project_id = fields.Many2one(
        "project.project", string="Mandate", required=True, ondelete="cascade",
        index=True)
    company_id = fields.Many2one(related="project_id.company_id", store=True)
    token_hash = fields.Char(
        string="Device fingerprint", readonly=True, index=True,
        groups="base.group_system")
    email = fields.Char(string="Proved by", readonly=True)
    expires_at = fields.Datetime(string="Expires at", readonly=True, index=True)
    last_seen = fields.Datetime(string="Last seen", readonly=True)
    ip_address = fields.Char(string="IP address", readonly=True)

    @api.model
    def _jours(self):
        return self.env["bf.progression.otp"]._parametre(
            "portal_device_days", JOURS_APPAREIL_DEFAUT)

    @api.model
    def _reconnaitre(self, projet, jeton):
        """Rend l'appareil si le témoin est connu et pas périmé, sinon vide.

        ⚠️ La base ne garde que l'empreinte du jeton : on cherche donc par
        empreinte, et une lecture de la base ne rend aucun témoin utilisable.
        """
        if not jeton or not isinstance(jeton, str) or len(jeton) > 128:
            return self.sudo().browse()
        if not jeton.isascii():
            return self.sudo().browse()
        appareil = self.sudo().search([
            ("project_id", "=", projet.id),
            ("token_hash", "=", _empreinte(self.env, "device-%s" % projet.id, jeton)),
            ("expires_at", ">", fields.Datetime.now()),
        ], limit=1)
        if appareil:
            appareil._noter_le_passage()
        return appareil

    def _noter_le_passage(self):
        """Une fois par heure au plus : chaque lecture devenait une écriture."""
        self.ensure_one()
        maintenant = fields.Datetime.now()
        if (not self.last_seen
                or self.last_seen < fields.Datetime.subtract(
                    maintenant, minutes=MINUTES_ENTRE_DEUX_PASSAGES)):
            self.sudo().last_seen = maintenant

    @api.model
    def _retenir(self, projet, email=None, ip=None):
        """Crée un appareil et rend son jeton EN CLAIR, pour le témoin.

        C'est la seule fois où le jeton existe en clair : la base n'en garde que
        l'empreinte, et la page le pose dans un témoin.
        """
        jeton = secrets.token_urlsafe(32)
        self.sudo().create({
            "project_id": projet.id,
            "token_hash": _empreinte(self.env, "device-%s" % projet.id, jeton),
            "email": email,
            "expires_at": fields.Datetime.add(
                fields.Datetime.now(), days=self._jours()),
            "last_seen": fields.Datetime.now(),
            "ip_address": ip,
        })
        return jeton
