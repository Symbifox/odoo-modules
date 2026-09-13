"""Ce que Gen fait du courrier, et ce qu'elle n'en fait pas.

Gmail est entré dans l'ère Gemini le 8 janvier 2026 : résumé de fil gratuit,
questions en langage naturel réservées aux abonnements payants, rédaction
assistée, relecture. La moitié de ce que Gmail vend aujourd'hui est de l'IA.

Trois gestes ici, et un seul principe qui les gouverne :

⚠️ **Rien n'est écrit sur la ligne.** Le résumé et la réponse proposée sont
rendus à l'écran et disparaissent avec l'écran. Les stocker les ferait entrer
dans la recherche, dans les sauvegardes, dans le calendrier de conservation et
dans le registre de destruction, pour une valeur qui ne survit pas à la
lecture.

⚠️ **Éteint tant que personne ne l'allume.** `bf_email.gen_enabled` absent vaut
NON. Le courrier contient du renseignement personnel de clients ; l'envoyer à
un modèle est une communication à un tiers, et ça se décide, ça ne se code pas
d'abord. Même doctrine que l'avis d'arrivée et le mode « ne pas déranger ».

⚠️ **Et si Gen est absente, le module fonctionne quand même.** Le bouton n'est
pas offert, le reste ne bouge pas. Même contrainte que pour
`bf_contact_absence_mail`.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Ce qu'on accepte de lire d'un fil avant de le donner à résumer. Un fil de
# quarante messages, c'est le corpus mesuré comme le plus long sur BF.
MAX_MESSAGES = 25
MAX_CAR_PAR_MESSAGE = 2500
TIMEOUT = 90


class BfEmailGen(models.Model):
    _inherit = "bf.email"

    @api.model
    def _gen_enabled(self):
        """L'interrupteur d'instance. Absent vaut NON, comme les autres."""
        valeur = self.env["ir.config_parameter"].sudo().get_param(
            "bf_email.gen_enabled", "0")
        return str(valeur).strip().lower() in ("1", "true", "yes", "oui")

    @api.model
    def _gen_available(self):
        """Vrai si l'interrupteur est mis ET que le pont répond.

        ⚠️ `bf_ai_bridge` n'est PAS une dépendance du manifeste, et c'est
        délibéré : une instance sans Gen doit pouvoir installer et faire
        tourner la boîte. Le modèle peut donc être absent du registre, ce qui
        lève un `KeyError` et non un booléen — d'où le filet.
        """
        if not self._gen_enabled():
            return False
        if "bf.ai.bridge" not in self.env:
            return False
        try:
            return bool(self.env["bf.ai.bridge"].available())
        except Exception:  # noqa: BLE001 - un pont absent n'est pas une panne
            return False

    def _gen_call(self, invite, timeout=TIMEOUT):
        # ⚠️ La disponibilité se vérifie AVANT d'aller chercher le modèle du
        # pont : sur une instance sans `bf_ai_bridge`, le simple accès au
        # registre lève, et l'usager verrait une trace au lieu d'une phrase.
        if not self._gen_available():
            raise UserError(_(
                "L'assistance de Gen est éteinte sur cette instance, ou le "
                "service ne répond pas. Réglages → Gestion des courriels."))
        icp = self.env["ir.config_parameter"].sudo()
        pont = self.env["bf.ai.bridge"]
        charge = {
            "message": invite,
            "model": icp.get_param("bf_email.gen_model", "haiku"),
            "max_turns": 1,
            "tenant": icp.get_param("bf_ai_bridge.tenant", "bf"),
        }
        try:
            reponse = pont.call("/chat", charge, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            _logger.info("bf_email gen : pont muet (%s)", type(exc).__name__)
            raise UserError(_(
                "Gen n'a pas répondu (%s). Rien n'a été écrit.",
                type(exc).__name__)) from exc
        texte = reponse.get("response") if isinstance(reponse, dict) else ""
        if not texte:
            raise UserError(_("Gen a répondu sans rien dire."))
        return texte.strip()

    # ------------------------------------------------------------------
    # Résumer un fil
    # ------------------------------------------------------------------
    def _gen_thread_text(self):
        """Le fil, du plus ancien au plus récent, borné.

        Lit `body_text`, qui n'existe que depuis : avant, il aurait
        fallu résumer des aperçus de 300 caractères, c'est-à-dire résumer un
        résumé.
        """
        self.ensure_one()
        if not self.thread_root_id:
            lignes = self
        else:
            lignes = self.search([
                ("thread_root_id", "=", self.thread_root_id),
                ("user_id", "=", self.user_id.id or self.env.uid),
            ], order="date asc", limit=MAX_MESSAGES)
        morceaux = []
        for ligne in lignes:
            corps = (ligne.body_text or ligne.body_preview or "")
            morceaux.append("[%s] %s\nObjet : %s\n%s" % (
                "reçu" if ligne.direction == "in" else "envoyé",
                ligne.email_from or "",
                ligne.subject or "",
                corps[:MAX_CAR_PAR_MESSAGE],
            ))
        return "\n\n---\n\n".join(morceaux)

    def action_gen_summary(self):
        """Rend un résumé du fil, à l'écran, sans rien écrire."""
        self.ensure_one()
        fil = self._gen_thread_text()
        if not fil.strip():
            raise UserError(_("Ce fil n'a pas de texte à résumer."))
        invite = (
            "Voici un fil de courriels, du plus ancien au plus récent. "
            "Résume-le en français, en cinq phrases au maximum, sans puces et "
            "sans titre. Dis ce qui est demandé, ce qui a été décidé, et ce "
            "qui reste en suspens. N'invente rien : si le fil ne dit pas, "
            "écris que le fil ne le dit pas.\n\n%s"
        ) % fil
        return self._gen_call(invite)

    def action_gen_reply(self):
        """Rend une proposition de réponse. Un BROUILLON, jamais un envoi."""
        self.ensure_one()
        fil = self._gen_thread_text()
        signataire = (self.user_id or self.env.user).name
        invite = (
            "Voici un fil de courriels. Écris une réponse au dernier message "
            "reçu, en français, signée %s. Moins de 120 mots, une seule idée, "
            "la réponse dans la première phrase, aucune puce, aucun tiret "
            "cadratin, aucun titre en gras. N'invente aucun engagement, "
            "aucune date et aucun chiffre qui ne soit pas dans le fil. Rends "
            "UNIQUEMENT le corps du message, sans objet ni salutation "
            "finale.\n\n%s"
        ) % (signataire, fil)
        return self._gen_call(invite)

    # ------------------------------------------------------------------
    # Surface RPC de la boîte
    # ------------------------------------------------------------------
    @api.model
    def inbox_gen(self, kind, email_id):
        """« Résumer » et « Proposer une réponse », depuis la boîte.

        ⚠️ Le nom du geste vient du navigateur : le serveur décide donc de ce
        qui est nommable, comme `inbox_run_action`. Sans cette liste, l'appel
        RPC deviendrait « exécute la méthode que je nomme ».
        """
        gestes = {"summary": "action_gen_summary", "reply": "action_gen_reply"}
        if kind not in gestes:
            raise UserError(_("Geste inconnu : %s", kind))
        rec = self.browse(int(email_id)).exists()
        if not rec:
            raise UserError(_("Courriel introuvable."))
        rec.check_access("read")
        return {"kind": kind, "text": getattr(rec, gestes[kind])()}

    @api.model
    def inbox_gen_available(self):
        return self._gen_available()
