# -*- coding: utf-8 -*-
"""La page que le client ouvre sans compte : `/progression/<projet>/<jeton>`.

Trois gardes, et toutes répondent la même chose (404) : projet absent, jeton
faux, portail fermé. Distinguer les cas renseignerait un visiteur sur
l'existence d'un projet.

⚠️ Le jeton voyage dans le chemin : la page ne charge rien d'externe, et
l'en-tête `Referrer-Policy: no-referrer` empêche qu'un lien sortant le
transmette. `X-Robots-Tag` tient la page hors des moteurs, au cas où l'adresse
se retrouverait collée quelque part de public.
"""
import logging
from email.utils import parseaddr

from psycopg2 import IntegrityError, OperationalError
from werkzeug.utils import redirect

from odoo import _, http
from odoo.http import request

from ..models.progression_document import DepotRefuse
from ..models.project_project import jetons_egaux

_logger = logging.getLogger(__name__)

# L'enveloppe multipart d'un dépôt (séparateurs, en-têtes, jeton CSRF) tient
# largement là-dedans.
MARGE_FORMULAIRE = 64 * 1024

# Le témoin de l'appareil reconnu. Un par mandat, et son chemin le borne au
# mandat : le témoin d'un dossier ne voyage pas vers un autre.
TEMOIN_APPAREIL = "bf_progression_device_%s"

EN_TETES = [
    ("Referrer-Policy", "no-referrer"),
    # Ni dans un cadre : la page porte un formulaire d'accès.
    ("X-Frame-Options", "DENY"),
    ("X-Robots-Tag", "noindex, nofollow"),
    ("Cache-Control", "no-store"),
]


class ProgressionPortail(http.Controller):

    @classmethod
    def _progression_projet(cls, project_id, token, exiger_otp=True):
        """Le projet en sudo si les gardes passent, sinon None.

        Un projet archivé ne répond plus : le mandat est clos, son adresse aussi.

        🔴 `exiger_otp` par DÉFAUT : tout chemin qui ne le désactive pas est
        protégé par le code, y compris ceux des modules ponts, qui n'ont rien à
        savoir de la double authentification. Seule la page elle-même le
        désactive, parce qu'elle doit rendre le formulaire du code plutôt qu'un
        404 — c'est elle qui demande le code, pas elle qui le refuse.
        """
        projet = request.env["project.project"].sudo().browse(project_id).exists()
        if (not projet or not projet.active or not token
                or not projet.progression_portal_published):
            return None
        if not jetons_egaux(projet.progression_portal_token or "", token):
            return None
        if exiger_otp and not cls._progression_appareil_reconnu(projet):
            return None
        return projet

    @classmethod
    def _progression_appareil_reconnu(cls, projet):
        """Vrai si le mandat ne demande pas de code, ou si cet appareil l'a
        déjà prouvé."""
        if not projet._progression_otp_exige():
            return True
        jeton = request.httprequest.cookies.get(TEMOIN_APPAREIL % projet.id)
        return bool(request.env["bf.progression.device"].sudo()._reconnaitre(projet, jeton))

    @staticmethod
    def _progression_courriel_masque(adresse):
        """`a***@domaine.com` : assez pour que le client reconnaisse SA boîte,
        pas assez pour apprendre une adresse à qui a trouvé le lien.

        ⚠️ L'adresse peut arriver formatée (« Jean <jean@x.ca> ») : elle se
        découpe avant, sinon le masque rendait `J***@x.ca>`. Et une partie
        locale d'un seul caractère ne se masque pas elle-même — on en garde
        alors zéro.
        """
        _nom, adresse = parseaddr(adresse or "")
        if "@" not in adresse:
            return ""
        local, domaine = adresse.rsplit("@", 1)
        debut = local[:1] if len(local) > 1 else ""
        return "%s***@%s" % (debut, domaine)

    @staticmethod
    def _progression_document_permis(projet, document_id):
        """La pièce, si elle est active et attendue à une étape faite ou en cours
        de ce projet ; sinon None. Le dépôt direct et le pont S3 passent par ici."""
        try:
            document_id = int(document_id)
        except (TypeError, ValueError):
            return None
        document = request.env["bf.progression.document"].sudo().browse(document_id).exists()
        # Un lien n'est pas une pièce : rien ne s'y dépose, ni par le dépôt
        # direct ni par le pont S3, qui passent tous deux par ici.
        if not document or not document.active or document.kind != "deposit":
            return None
        permises = [e for e in projet._progression_portal_steps()
                    if e["state"] in ("done", "current")]
        if not any(document.stage_id.id in e["stage_ids"] for e in permises):
            return None
        return document

    # Les messages qu'affiche la page après un dépôt. La clé voyage dans
    # l'adresse : jamais de texte libre venu du visiteur.
    MESSAGES = ("received", "missing", "type", "empty", "too_large", "rate")

    @classmethod
    def _progression_valeurs(cls, projet):
        """Ce que le gabarit reçoit. Un module pont étend ce dict, pas la garde."""
        etapes = projet._progression_portal_steps()
        for etape in etapes:
            etape["documents"] = projet._progression_portal_documents(etape)
            etape["links"] = projet._progression_portal_liens(etape)
        return {
            "project": projet,
            "partner": projet.partner_id,
            # Celui que la page NOMME. Par défaut le client du projet, et un
            # module pont le remplace par le bénéficiaire du mandat. `partner`
            # reste dans le dict (un pont peut en avoir besoin), mais le gabarit
            # ne le lit plus pour se présenter.
            "portal_client": projet.partner_id.commercial_partner_id,
            # Ce que le mandat écrit sur la page : le nom public du
            # programme, où le poser, et la note de confidentialité. Dans le
            # dict plutôt que lus du projet par le gabarit, pour la même raison
            # que `portal_client` : un module pont les remplace (un programme
            # porté par le bénéficiaire, une note de société) sans toucher au
            # gabarit.
            "program_label": projet.progression_program_label or "",
            "program_label_position": (
                projet.progression_program_label_position or "overline"),
            "confidentiality_note": projet.progression_confidentiality_note or "",
            "company": projet.company_id or request.env.company,
            "steps": etapes,
            "token": projet.progression_portal_token,
            "deposit_mode": cls._progression_mode_de_depot(projet),
            "max_upload_mb": request.env["bf.progression.deposit"].sudo()._plafond_octets() // (1024 * 1024),
        }

    @classmethod
    def _progression_mode_de_depot(cls, projet):
        """« direct » : le fichier passe par Odoo. Un module pont (S3) le change."""
        return "direct"

    @classmethod
    def _progression_langue_du_lecteur(cls, projet):
        """La langue de celui qui LIT la page. Un module pont la remplace.

        Seule cette ligne-là change selon le lecteur ; la bascule, elle, reste
        celle de `_progression_langue` ci-dessous, et un pont n'a pas à la
        recopier pour en changer la source.
        """
        return projet.partner_id.lang

    @classmethod
    def _progression_langue(cls, projet):
        """La langue du client du mandat, posée sur la REQUÊTE.

        🔴 `classmethod` et non `staticmethod` : c'est `cls` qui fait trouver la
        version d'un module pont. En statique, la méthode appelée serait figée
        sur celle écrite ici.
        """
        langue = cls._progression_langue_du_lecteur(projet)
        # ⚠️ La bascule ne va que vers une langue INSTALLÉE : poser une langue
        # absente de la base ferait lever le premier champ traduit touché.
        actives = [code for code, _nom in request.env["res.lang"].get_installed()]
        if langue in actives and request.env.context.get("lang") != langue:
            request.update_context(lang=langue)
        return projet.with_context(lang=request.env.context.get("lang"))

    @http.route(
        ["/progression/<int:project_id>/<string:token>"],
        # website=True : là où le module `website` est installé (la démo), il étend
        # la mise en page publique et attend `website` dans le rendu ; sans lui, 500.
        # Sans `website`, le drapeau est ignoré.
        type="http", auth="public", methods=["GET"], website=True, sitemap=False)
    def progression_page(self, project_id, token, **kw):
        # `exiger_otp=False` : si le mandat demande un code, c'est cette page
        # qui le demande, elle ne peut pas se refuser elle-même.
        projet = self._progression_projet(project_id, token, exiger_otp=False)
        if projet is None:
            return request.not_found()
        try:
            # La langue du client du mandat d'abord : c'est lui qui lit la page.
            # Elle passe dans le contexte de la REQUÊTE, sinon les libellés du
            # gabarit suivraient le navigateur et les données la fiche.
            # ⚠️ DANS le `try` : cet appel lit des enregistrements, et un module
            # pont peut y en lire d'autres. Hors du `try`, une donnée aberrante
            # y rendait un 500 au visiteur, contre la promesse du module.
            projet = self._progression_langue(projet)
            # 🔴 APRÈS la bascule de langue : la page du code se rendait en
            # anglais à un client francophone, parce qu'elle sortait avant.
            valeurs = self._progression_valeurs(projet)
            if not self._progression_appareil_reconnu(projet):
                # Les valeurs sont déjà calculées : le formulaire du code n'a
                # pas à refaire les étapes, les pièces et l'état du questionnaire.
                return self._progression_demander_le_code(
                    projet, token, nomme=valeurs.get("portal_client"))
            message = kw.get("message")
            valeurs["message"] = message if message in self.MESSAGES else None
        except (IntegrityError, OperationalError):
            # 🔴 Cette page ÉCRIT depuis qu'elle envoie des codes (le plafond
            # horaire prend une version de ligne du projet) : l'erreur de
            # sérialisation doit remonter jusqu'au rejeu d'Odoo, comme sur les
            # autres chemins qui écrivent.
            raise
        except Exception:
            # ⚠️ Route PUBLIQUE : une donnée aberrante ne rend pas un 500 au
            # visiteur. On refuse comme le reste et on le journalise.
            _logger.exception("Portail Progression illisible pour le projet %s", project_id)
            return request.not_found()
        reponse = request.render(
            "bf_stepbystep_clients.progression_portal_page", valeurs,
            headers=EN_TETES)
        return reponse

    @classmethod
    def _progression_demander_le_code(cls, projet, token, message=None, nomme=None):
        """Rend le formulaire du code, et envoie le code s'il n'y en a pas déjà
        un en vol.

        ⚠️ Un chargement de page envoie donc un courriel la première fois. Un
        rechargement, un préchargeur de liens ou un antivirus de courriel ne
        consomme rien de plus : le code en vol est réutilisé tel quel, et le
        plafond horaire borne le reste.
        """
        Otp = request.env["bf.progression.otp"].sudo()
        destinataire = cls._progression_destinataire_du_code(projet, nomme=nomme)
        if not message:
            otp, raison = Otp._envoyer(
                projet, destinataire=destinataire,
                ip=request.httprequest.remote_addr)
            # ⚠️ L'adresse ANNONCÉE est celle où le code est parti, pas celle
            # qu'on écrirait maintenant : corriger la fiche pendant qu'un code
            # est en vol faisait annoncer la nouvelle adresse pour un courriel
            # déjà envoyé à l'ancienne.
            if otp and otp.email:
                destinataire = otp.email
            # ⚠️ Un code déjà en vol se DIT aussi : le premier chargement du
            # lien est souvent celui d'un détonateur de liens (Safe Links, un
            # antivirus de courriel), donc le code et son horloge partent avant
            # le clic du client. Sans message, il trouvait un champ vide, sans
            # adresse ni échéance.
            message = raison
        niveau, texte = cls._progression_texte_du_code(
            message, cls._progression_courriel_masque(destinataire), Otp._minutes())
        valeurs = {
            "project": projet,
            "company": projet.company_id or request.env.company,
            "token": token,
            # Le message est une PHRASE entière, bâtie en Python : un texte de
            # gabarit coupé par un `<t t-out/>` entre au catalogue en morceaux
            # (le module en porte déjà un : « The file is too large (maximum »).
            "message_niveau": niveau,
            "message_texte": texte,
        }
        return request.render(
            "bf_stepbystep_clients.progression_portal_otp", valeurs, headers=EN_TETES)

    @classmethod
    def _progression_texte_du_code(cls, message, adresse, minutes):
        """Le niveau et la phrase du message, ou `(None, "")`."""
        if message == "sent":
            return "info", _(
                "A six-digit code has been sent to %(adresse)s. It expires in "
                "%(minutes)s minutes.", adresse=adresse, minutes=minutes)
        if message == "wrong":
            return "danger", _("That code does not match. Check the email and try again.")
        if message == "expired":
            return "warning", _("That code has expired. Reload this page to get a new one.")
        if message == "too_many":
            return "danger", _("Too many codes have been asked for in the last hour. Try again later.")
        if message == "too_many_today":
            # ⚠️ Sa propre phrase : « dans la dernière heure, réessayez plus
            # tard » envoyait le vrai client réessayer pendant 24 heures sans
            # jamais comprendre, et sans savoir qu'un geste interne le débloque.
            return "danger", _("Too many attempts today. Try again tomorrow, or contact us to reopen the page.")
        if message == "no_email":
            return "danger", _("This mandate has no email address on file. Contact us so we can open the page for you.")
        if message == "not_sent":
            return "danger", _("The code could not be sent. Contact us so we can open the page for you.")
        return None, ""

    @classmethod
    def _progression_destinataire_du_code(cls, projet, nomme=None):
        """L'adresse où part le code : celle de la partie que la PAGE nomme.

        🔴 Et JAMAIS un repli vers le payeur. Chez un donneur d'ordre, la page
        nomme le bénéficiaire (le donneur d'ordre paie, le bénéficiaire lit) : une société
        bénéficiaire sans adresse propre faisait partir le code chez le payeur,
        qui ouvrait alors la page d'un dossier qui n'est pas le sien — la page
        même dont 2.5.0 avait retiré son nom —, tandis que le lecteur, qui tient
        le lien, restait dehors pour toujours. Sans adresse sur la partie
        nommée : pas de code, et la page le dit.
        """
        if nomme is None:
            nomme = cls._progression_valeurs(projet).get("portal_client")
        paye_et_lit = projet.sudo().partner_id.commercial_partner_id
        if nomme and nomme != paye_et_lit:
            return nomme.email or ""
        return projet._progression_otp_courriel()

    @http.route(
        ["/progression/<int:project_id>/<string:token>/code"],
        type="http", auth="public", methods=["POST"], website=True, sitemap=False)
    def progression_code(self, project_id, token, **kw):
        """Vérifie le code et retient l'appareil.

        Le témoin est posé ici, et nulle part ailleurs : c'est la seule fois où
        le jeton de l'appareil existe en clair.
        """
        projet = self._progression_projet(project_id, token, exiger_otp=False)
        if projet is None:
            return request.not_found()
        try:
            projet = self._progression_langue(projet)
            if self._progression_appareil_reconnu(projet):
                # Déjà reconnu : rien à vérifier, on rend la page.
                return redirect("/progression/%s/%s" % (projet.id, token), code=303)
            # Une seule fois, et passé ensuite : chaque refus rappelait tout le
            # rendu (étapes, pièces, état du questionnaire) pour en tirer une
            # adresse, sur un chemin public sans limite de débit.
            nomme = self._progression_valeurs(projet).get("portal_client")
            Otp = request.env["bf.progression.otp"].sudo()
            if not Otp._code_en_vol(projet):
                # 🔴 Épuisé n'est pas périmé : « rechargez pour en recevoir un
                # nouveau » disait au visiteur comment remettre le compteur
                # d'essais à zéro. Un code épuisé répond « trop de demandes ».
                raison = "too_many" if Otp._code_epuise(projet) else "expired"
                return self._progression_demander_le_code(
                    projet, token, message=raison, nomme=nomme)
            verdict = Otp._verifier(projet, kw.get("code"))
            if verdict != "ok":
                return self._progression_demander_le_code(
                    projet, token, message=verdict, nomme=nomme)
            jeton_appareil = request.env["bf.progression.device"].sudo()._retenir(
                projet, email=self._progression_destinataire_du_code(projet, nomme=nomme),
                ip=request.httprequest.remote_addr)
        except (IntegrityError, OperationalError):
            # 🔴 AVANT le filet : `retrying()` d'Odoo ne rejoue que ce qui lève,
            # et le plafond horaire des codes tient à ce rejeu.
            raise
        except Exception:
            _logger.exception(
                "Code de progression illisible pour le projet %s", project_id)
            return request.not_found()
        reponse = redirect("/progression/%s/%s" % (projet.id, token), code=303)
        jours = request.env["bf.progression.device"].sudo()._jours()
        reponse.set_cookie(
            TEMOIN_APPAREIL % projet.id, jeton_appareil,
            max_age=jours * 24 * 3600, httponly=True,
            secure=request.httprequest.is_secure, samesite="Lax",
            path="/progression/%s/" % projet.id)
        return reponse

    @http.route(
        ["/progression/<int:project_id>/<string:token>/deposit/<int:document_id>"],
        type="http", auth="public", methods=["POST"], sitemap=False)
    def progression_deposit(self, project_id, token, document_id, **kw):
        """Le client dépose une pièce. Même garde que la page, puis la pièce doit
        être attendue à une étape FAITE ou EN COURS de CE projet."""
        projet = self._progression_projet(project_id, token)
        if projet is None:
            return request.not_found()
        # La note au chatter suit la langue du client, comme la page.
        projet = self._progression_langue(projet)
        retour = "/progression/%s/%s" % (projet.id, token)
        document = self._progression_document_permis(projet, document_id)
        if document is None:
            return request.not_found()
        Depot = request.env["bf.progression.deposit"].sudo()
        plafond = Depot._plafond_octets()
        # Un corps plus gros que le plafond (plus l'enveloppe du formulaire) est
        # refusé avant d'en lire le fichier.
        longueur = request.httprequest.content_length
        if longueur and longueur > plafond + MARGE_FORMULAIRE:
            return redirect(retour + "?message=too_large#doc-%s" % document.id, code=303)
        fichier = request.httprequest.files.get("file")
        if not fichier or not fichier.filename:
            return redirect(retour + "?message=missing#doc-%s" % document.id, code=303)
        # On lit un octet de plus que le plafond : assez pour refuser sans tout
        # charger en mémoire.
        contenu = fichier.stream.read(plafond + 1)
        try:
            Depot._verser(projet, document, fichier.filename, contenu,
                          ip=request.httprequest.remote_addr)
        except DepotRefuse as refus:
            return redirect(retour + "?message=%s#doc-%s" % (refus.code, document.id), code=303)
        return redirect(retour + "?message=received#doc-%s" % document.id, code=303)
