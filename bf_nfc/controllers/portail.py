"""Les deux portes qui passent par un navigateur : la session, et la puce signée.

🔴 **Rien n'agit sur un GET.** Un aperçu de lien, un antipourriel, un scanner de
sécurité, une préconnexion de navigateur : tout ça ouvre les URL sans que
personne n'ait touché à quoi que ce soit. Sur des liens tracés, une grande part
des clics enregistrés viennent de machines. Un geste qui écrit passe donc par
une page à un bouton, et c'est le POST qui agit.

⚠️ Un code inconnu rend un **404 franc**, jamais une redirection vers l'accueil.
Une pastille déjà gravée qui atterrit ailleurs ne se corrige plus : mieux vaut
qu'elle dise clairement qu'elle ne mène nulle part.
"""
import logging

from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.http import request

from ..models.sdm import SdmInvalide, lire_picc, verifier_cmac

_logger = logging.getLogger(__name__)

# ⚠️ ``website=True`` sur les routes qui rendent une page, même sans le module
# « website » : c'est ce drapeau qui fait poser à Odoo le contexte de façade
# (``frontend_languages``, ``url_for``). Sans lui, ``portal.frontend_layout``
# rend un 500 sur ``len(None)`` dans le sélecteur de langue du pied de page,
# et l'erreur ne nomme jamais la route fautive.


def _cle_societe(company, suffixe):
    """La clé de pastille signée d'une société, ou celle par défaut.

    ⚠️ Rangée dans ``ir.config_parameter``, que seul l'administrateur système
    peut lire. Sur ``res.company`` elle serait lisible par n'importe quel
    interne en XML-RPC, ce qui reviendrait à publier la clé.
    """
    icp = request.env["ir.config_parameter"].sudo()
    return icp.get_param("bf_nfc.%s.%s" % (suffixe, company.id)) \
        or icp.get_param("bf_nfc.%s" % suffixe)


class PortailNfc(http.Controller):

    # ------------------------------------------------------------------
    # Porte « session » : la personne est connectée dans son navigateur
    # ------------------------------------------------------------------
    @http.route("/nfc/<string:code>", type="http", auth="user", methods=["GET"],
                website=True)
    def ouvrir(self, code, **kw):
        tag = request.env["bf.nfc.tag"]._resoudre(code)
        if not tag:
            raise NotFound()
        action = "/nfc/%s/agir" % tag.code
        # ⚠️ Un menu ne passe pas par la confirmation : sans choix, il ne fait que
        # lister ses boutons, et `taper` défait tout geste qui pose une question.
        if tag.gesture_id.kind != "menu" and tag.gesture_id.writes and tag.confirm_required:
            return self._page_confirmation(tag, action=action)
        return self._agir_et_rendre(tag, "session", kw, action=action)

    @http.route("/nfc/<string:code>/agir", type="http", auth="user", methods=["POST"],
                website=True)
    def agir(self, code, **kw):
        tag = request.env["bf.nfc.tag"]._resoudre(code)
        if not tag:
            raise NotFound()
        return self._agir_et_rendre(tag, "session", kw, action="/nfc/%s/agir" % tag.code)

    # ------------------------------------------------------------------
    # Porte « signée » : la puce prouve, la personne n'a pas de compte
    # ------------------------------------------------------------------
    @http.route("/nfc/s", type="http", auth="public", methods=["GET"], website=True)
    def signee(self, **kw):
        tag, compteur, erreur = self._verifier_signature(kw)
        if erreur:
            return self._page_resultat(None, {"statut": "refused", "titre": _("Refusé"),
                                              "message": erreur, "url": None})
        if tag.gesture_id.kind != "menu" and tag.gesture_id.writes and tag.confirm_required:
            return self._page_confirmation(tag, action="/nfc/s/agir", cache=kw)
        return self._agir_signe(tag, compteur, kw)

    @http.route("/nfc/s/agir", type="http", auth="public", methods=["POST"],
                website=True)
    def signee_agir(self, **kw):
        tag, compteur, erreur = self._verifier_signature(kw)
        if erreur:
            return self._page_resultat(None, {"statut": "refused", "titre": _("Refusé"),
                                              "message": erreur, "url": None})
        return self._agir_signe(tag, compteur, kw)

    def _verifier_signature(self, kw):
        """Rend (pastille, compteur, None) quand la signature tient.

        🔴 Le compteur est vérifié ici mais n'est **pas** enregistré : seule
        l'exécution le consomme. Sinon un aperçu de lien brûlerait le
        tapotement de la personne qui tient encore son téléphone.
        """
        picc = kw.get("picc_data") or kw.get("p")
        cmac = kw.get("cmac") or kw.get("c")
        if not (picc and cmac):
            return None, 0, _("Cette adresse ne porte pas de signature de puce.")
        Tag = request.env["bf.nfc.tag"].sudo()
        societes = request.env["res.company"].sudo().search([])
        for company in societes:
            cle_meta = _cle_societe(company, "sdm_meta_key")
            if not cle_meta:
                continue
            try:
                uid, compteur = lire_picc(cle_meta, picc)
            except SdmInvalide:
                continue
            tag = Tag._resoudre_signee(uid)
            if not tag:
                continue
            cle_fichier = _cle_societe(tag.company_id, "sdm_file_key")
            try:
                valide = verifier_cmac(cle_fichier, uid, compteur, cmac)
            except SdmInvalide as exc:
                return None, 0, str(exc)
            if not valide:
                _logger.warning("Pastille signée %s : CMAC invalide", uid)
                return None, 0, _("La signature de cette pastille n'est pas valide.")
            if not tag.user_id:
                # ⚠️ Refusé ici et pas seulement dans _refus_eventuel : plus bas,
                # on bascule l'environnement sur ce compte, et basculer sur rien
                # lève avant que la phrase de refus n'ait pu être écrite.
                return None, 0, _("Cette pastille signée ne désigne aucun compte "
                                  "au nom duquel agir.")
            if compteur <= tag.sdm_counter:
                _logger.warning("Pastille signée %s : compteur rejoué (%s <= %s)",
                                uid, compteur, tag.sdm_counter)
                return None, 0, _("Ce tapotement a déjà servi. Approchez de nouveau "
                                  "le téléphone de la pastille.")
            return tag, compteur, None
        return None, 0, _("Cette pastille n'est pas reconnue.")

    def _agir_signe(self, tag, compteur, kw):
        """Exécute au nom du compte désigné sur la pastille.

        ⚠️ Toujours une page, jamais une redirection : la personne qui tape une
        pastille signée n'a pas de compte. L'envoyer vers une fiche du client
        web la déposerait sur l'écran de connexion, ce qui se lit comme une
        panne. La page porte le lien, et celui qui a un compte le suit.
        """
        request.update_env(user=tag.user_id.id)
        # ⚠️ La langue reste celle du NAVIGATEUR de la personne qui tape (celle de
        # la page) : le compte désigné n'est pas la personne devant l'écran.
        tag_acteur = request.env["bf.nfc.tag"].sudo().browse(tag.id)
        resultat = tag_acteur.taper("signed", params=self._params_utiles(kw),
                                    compteur=compteur, **self._reponse(kw))
        if resultat["statut"] == "choice":
            # 🔴 Le compteur n'a pas été consommé : une question n'est pas une
            # exécution. La page renvoie la même signature avec le choix.
            return self._page_choix(tag_acteur, resultat, "/nfc/s/agir", cache=kw)
        return self._page_resultat(tag_acteur, resultat)

    # ------------------------------------------------------------------
    # Commun
    # ------------------------------------------------------------------
    def _params_utiles(self, kw):
        """Les paramètres de l'adresse, moins ceux qui appartiennent au transport."""
        reserves = {"picc_data", "cmac", "p", "c", "csrf_token", "choix", "texte"}
        return {k: v for k, v in (kw or {}).items() if k not in reserves}

    def _reponse(self, kw):
        """Le choix et le texte d'une réponse à la question d'un geste."""
        choix = (kw.get("choix") or "").strip()[:64] or None
        texte = (kw.get("texte") or "").strip()[:2000] or None
        return {"choix": choix, "texte": texte}

    def _agir_et_rendre(self, tag, porte, kw, action):
        resultat = tag.taper(porte, params=self._params_utiles(kw), **self._reponse(kw))
        if resultat["statut"] == "choice":
            return self._page_choix(tag, resultat, action)
        if resultat["statut"] in ("ok", "duplicate") and resultat.get("url"):
            return request.redirect(resultat["url"])
        return self._page_resultat(tag, resultat)

    def _page_choix(self, tag, resultat, action, cache=None):
        return request.render("bf_nfc.page_choix", {
            "tag": tag,
            "resultat": resultat,
            "action": action,
            "picc_data": (cache or {}).get("picc_data") or (cache or {}).get("p") or "",
            "cmac": (cache or {}).get("cmac") or (cache or {}).get("c") or "",
        })

    def _page_confirmation(self, tag, action, cache=None):
        return request.render("bf_nfc.page_confirmation", {
            "tag": tag,
            "action": action,
            "picc_data": (cache or {}).get("picc_data") or (cache or {}).get("p") or "",
            "cmac": (cache or {}).get("cmac") or (cache or {}).get("c") or "",
        })

    def _page_resultat(self, tag, resultat):
        return request.render("bf_nfc.page_resultat", {
            "tag": tag,
            "resultat": resultat,
        })
