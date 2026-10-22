# -*- coding: utf-8 -*-
"""Le lien de la page d'état dans les messages : masqué au chatter, rendu à l'envoi.

1. `mail.template.send_mail` recopie le corps rendu dans `body`, le message affiché au chatter
   du destinataire : tout interne qui lit la fiche lirait le jeton, donc la page du client. Et la
   réponse du client, qui CITE l'avis, reviendrait au chatter avec le jeton en clair (passerelle,
   courriel rangé). Tout message (`mail.message`) est donc masqué à la création et à l'écriture :
   `jeton-masque` à la place du jeton. Le courriel d'un gabarit (`mail.mail.body_html`) garde le
   lien ; il n'est lisible que par l'administration.
2. Un rappel se programme des jours d'avance, corps déjà rendu. Une nouvelle adresse, une
   dépublication ou un destinataire retiré entre-temps, et il partirait avec l'ancien lien. Chaque
   lien d'état, masqué ou non, est donc jugé au moment d'envoyer, pour chaque destinataire
   (`_personalize_outgoing_body`) : le jeton courant à un destinataire encore nommé d'une page
   publiée ; sinon le paragraphe du lien de l'avis est retiré, et tout autre lien reste masqué.
   Le masque est ainsi sans perte : un lien cité ou collé au chatter repart entier à qui y a droit.

Le masque cherche la VALEUR des jetons posés, pas la forme de l'adresse : le client voit la page
sans `www.` quand le site redirige, et un antivirus de messagerie réécrit les liens
(`url=https%3A%2F%2F…`). À l'envoi, seuls les liens à l'adresse de la base (`get_base_url()` du
client, celle de `_hosting_status_url`) sont jugés ; `https://autre.example/etat/2025/<long
segment>` n'est pas touché.
"""
import re

from markupsafe import Markup

from odoo import api, models, tools

# `<base>/etat/<client>/<jeton>`. Le jeton s'arrête au premier caractère qui ne peut pas finir une
# adresse ; 32 caractères au moins (la contrainte du champ), ou le masque lui-même.
JETON_MASQUE = "jeton-masque"  # ASCII : le nettoyeur du message encode un « … » dans un href.
MOTIF_LIEN = re.compile(
    r"(?P<base>https?://[^\s\"'<>/]+)/etat/(?P<client>\d{1,12})/"
    r"(?P<jeton>jeton-masque|[^\s\"'<>&/?#]{32,128})(?![^\s\"'<>&/?#])")
# Le paragraphe du lien dans l'avis de maintenance (hosting_management ≥ 18.0.2.65.0). Le gabarit
# n'y imbrique aucun autre paragraphe.
MOTIF_PARAGRAPHE = re.compile(
    r"<p\b[^>]*\bo_hosting_status_link\b[^>]*>.*?</p>\s*", re.DOTALL | re.IGNORECASE)


class _Lecteur:
    """Reconnaît les liens d'état d'un texte, avec un cache par client."""

    def __init__(self, env):
        self.Partner = env["res.partner"].sudo()
        self._bases = {}

    def est_un_lien(self, m):
        client_id = int(m.group("client"))
        if client_id not in self._bases:
            client = self.Partner.browse(client_id).exists()
            self._bases[client_id] = client.get_base_url().rstrip("/").lower() if client else None
        return self._bases[client_id] == m.group("base").rstrip("/").lower()


def masquer_jetons(env, texte):
    """Le texte sans aucun jeton de page d'état posé, quelle que soit l'adresse autour."""
    if not texte:
        return texte
    for jeton in env["res.partner"]._hosting_status_jetons_poses():
        if jeton in texte:
            texte = texte.replace(jeton, JETON_MASQUE)
    return texte


def _masquer_le_corps(env, valeurs):
    corps = valeurs.get("body")
    if corps:
        masque = masquer_jetons(env, str(corps))
        # Garder le type : un `Markup` repasserait autrement par le nettoyeur HTML.
        valeurs["body"] = Markup(masque) if isinstance(corps, Markup) else masque


class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.model_create_multi
    def create(self, liste_valeurs):
        """Tout message, entrant compris (réponse citée, courriel rangé) ; un `mail.mail` crée
        son message par `_inherits`, il passe donc ici aussi."""
        for valeurs in liste_valeurs:
            _masquer_le_corps(self.env, valeurs)
        return super().create(liste_valeurs)

    def write(self, vals):
        if "body" in vals:
            vals = dict(vals)
            _masquer_le_corps(self.env, vals)
        return super().write(vals)


class MailMail(models.Model):
    _inherit = "mail.mail"

    def _personalize_outgoing_body(self, body, partner=False, recipients_follower_status=None):
        body = super()._personalize_outgoing_body(
            body, partner=partner, recipients_follower_status=recipients_follower_status)
        if not body or "/etat/" not in body:
            return body
        return self._hosting_status_lien_a_l_envoi(body, partner)

    def _hosting_status_lien_a_l_envoi(self, body, partner):
        """Chaque lien d'état du corps, jugé pour CE destinataire au moment de l'envoi.

        `partner` vide : une adresse en `email_to` ou `email_cc` (une copie d'essai, un relais),
        jamais nommée.
        """
        lecteur = _Lecteur(self.env)
        Partner = lecteur.Partner
        client_du_compte = Partner._hosting_status_client_du_compte(partner) if partner else Partner
        jetons = {}

        def jeton_courant(client_id):
            """Le jeton à donner pour ce client, ou None si ce destinataire n'y a pas droit."""
            if client_id not in jetons:
                client = Partner.browse(client_id).exists()
                jetons[client_id] = (
                    client.hosting_status_token
                    if (client and client == client_du_compte
                        and client.hosting_status_published and client.hosting_status_token)
                    else None)
            return jetons[client_id]

        def paragraphe(m):
            liens = [l for l in MOTIF_LIEN.finditer(m.group(0)) if lecteur.est_un_lien(l)]
            if liens and all(jeton_courant(int(l.group("client"))) for l in liens):
                return m.group(0)
            return ""

        body = MOTIF_PARAGRAPHE.sub(paragraphe, body)

        def lien(m):
            if not lecteur.est_un_lien(m):
                return m.group(0)
            jeton = jeton_courant(int(m.group("client")))
            return "%s/etat/%s/%s" % (m.group("base"), m.group("client"), jeton or JETON_MASQUE)

        return MOTIF_LIEN.sub(lien, body)
