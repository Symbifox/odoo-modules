"""Copies conformes et cachées : une copie par destinataire, et le Cci caché.

🔴 Défauts trouvés le 2026-09-16 en ajoutant le Cci au téléphone, présents au
poste depuis que ``mail_composer_cc_bcc`` sert. Prouvés par des envois SMTP
réels au banc, avec le code exact de la production : huit courriels exposés
depuis juin.

Le module OCA (18.0.1.0.1) réécrit, dans ``mail.mail._prepare_outgoing_list``,
la liste des copies que le noyau prépare, puis choisit le destinataire SMTP de
chaque copie en dépilant une FILE posée dans le contexte
(``ir.mail_server._prepare_email_message``). Quatre défauts s'y cumulent :

1. **L'adresse cachée part chez tout le monde.** Le noyau construit UN
   dictionnaire d'en-têtes par ``mail.mail`` et le donne à chaque copie
   (``'headers': headers``). Le module y ajoute ``X-Odoo-Bcc`` par
   ``m["headers"].update`` en passant sur la copie du destinataire caché :
   c'est le dictionnaire commun qu'il modifie, et chaque copie — celles des
   « À » et des « Cc » comprises — sort avec ``X-Odoo-Bcc: "Nom" <adresse>``.

2. **La mauvaise copie est retirée.** Avec une copie conforme, le noyau prépare
   une copie « Cc » générique EN PLUS de la copie personnelle du destinataire
   en Cc. Le module retire une copie de trop… la dernière de la liste, souvent
   celle du destinataire caché.

3. **La file est dans le désordre** : rangée dans un ``set``, elle suit le germe
   de hachage du processus, et la copie préparée pour l'un part chez l'autre.

4. 🔴 **Une copie refusée décale toutes les suivantes** (relecture adverse du
   2026-09-16). Le noyau ne garde, pour chaque copie, que les adresses
   d'en-tête présentes dans ``send_validated_to`` — en MINUSCULES. Le module
   écrit les en-têtes avec l'adresse brute de la fiche : ``Alice@Dest.test``
   n'y est pas, la copie est refusée AVANT d'avoir dépilé la file, et la copie
   suivante part chez le destinataire de la précédente. Au banc : la copie du
   Cci livrée à Alice, adresse cachée comprise, et Denis ne reçoit rien.

Le correctif ne touche pas le module OCA (un arbre de locataire ne se modifie
pas sur place) et **ne se sert plus de la file**. Il demande au noyau sa liste
sans la réécriture du module (``is_from_composer`` retiré le temps de
l'appel), puis :

- donne à toutes les copies le même « À » et le même « Cc », écrits avec les
  adresses NORMALISÉES — celles que ``send_validated_to`` reconnaît ;
- garde une copie par destinataire (la personnelle gagne sur la générique) ;
- donne à chaque copie ses propres en-têtes, et ``X-Odoo-Bcc`` à la seule copie
  du destinataire caché ;
- laisse le noyau choisir le destinataire SMTP de chaque copie : les adresses
  d'en-tête de CETTE copie qui figurent dans son propre ``send_validated_to``.
  Pour le destinataire caché, l'OCA transforme ``X-Odoo-Bcc`` en ``Bcc`` avant
  ce calcul, et le noyau retire ``Bcc`` du message.

Une copie refusée n'a donc plus d'effet sur ses voisines.
"""
import logging

from odoo import models, tools

_logger = logging.getLogger(__name__)

EN_TETE_CCI = "X-Odoo-Bcc"


def _adresses_normalisees(partenaires):
    """[(partenaire, adresse normalisée)], une paire par adresse de la fiche."""
    paires = []
    for partenaire in partenaires:
        for adresse in tools.email_normalize_all(partenaire.email or ""):
            paires.append((partenaire, adresse))
    return paires


def _formater(partenaires):
    """L'en-tête, avec les adresses normalisées : voir le défaut 4."""
    return ", ".join(tools.formataddr((p.name or "", a))
                     for p, a in _adresses_normalisees(partenaires))


class MailMail(models.Model):
    _inherit = "mail.mail"

    def _prepare_outgoing_list(self, mail_server=False,
                               recipients_follower_status=None):
        par_le_composeur = (
            len(self.ids) <= 1
            and self.env.context.get("is_from_composer", False)
            and "recipient_bcc_ids" in self._fields
        )
        if not par_le_composeur:
            emails = super()._prepare_outgoing_list(
                mail_server=mail_server,
                recipients_follower_status=recipients_follower_status,
            )
            # Même hors du composeur : une copie qui porte ses propres
            # en-têtes ne peut plus en prêter à sa voisine.
            for email in emails:
                email["headers"] = dict(email.get("headers") or {})
            return emails
        emails = super(MailMail, self.with_context(
            is_from_composer=False))._prepare_outgoing_list(
                mail_server=mail_server,
                recipients_follower_status=recipients_follower_status,
            )
        return self._bf_copies_du_composeur(emails)

    def _bf_copies_du_composeur(self, emails):
        """Une copie par destinataire, en-têtes communs normalisés, Cci caché."""
        self.ensure_one()
        copies = self.recipient_cc_ids | self.recipient_bcc_ids
        visibles = self.recipient_ids - copies
        a_header = _formater(visibles)
        a_brut = ", ".join(a for _p, a in _adresses_normalisees(visibles))
        cc_header = _formater(self.recipient_cc_ids)
        caches = {a: p for p, a in _adresses_normalisees(self.recipient_bcc_ids)}

        couvertes = set()
        for email in emails:
            if email.get("partner_id"):
                couvertes |= set(email.get("email_to_normalized") or [])

        resultat, ecartees = [], []
        for email in emails:
            adresses = set(email.get("email_to_normalized") or [])
            personnelle = bool(email.get("partner_id"))
            # La copie générique (Cc ou À du `mail.mail`, sans fiche) double
            # des copies personnelles : elle part à la trappe. Une générique
            # qui vise quelqu'un sans copie personnelle, elle, reste telle que
            # le noyau l'a faite, en-têtes compris : ses adresses ne figurent
            # pas forcément dans l'« À » commun, et le noyau la refuserait.
            if not personnelle and adresses and adresses <= couvertes:
                ecartees.append(email)
                continue
            en_tetes = dict(email.get("headers") or {})
            en_tetes.pop(EN_TETE_CCI, None)
            if personnelle:
                cachee = next((a for a in (email.get("email_to_normalized") or [])
                               if a in caches), None)
                if cachee:
                    en_tetes[EN_TETE_CCI] = tools.formataddr(
                        (caches[cachee].name or "", cachee))
                email.update({
                    "email_to": a_header,
                    "email_to_raw": a_brut,
                    "email_cc": cc_header,
                })
            email["headers"] = en_tetes
            resultat.append(email)

        self._bf_oublier_suivis(ecartees)
        # Plus de file : si un envoi précédent du même environnement en a
        # laissé une, elle ne doit pas être dépilée pour celui-ci.
        if self.env.context.get("recipients"):
            self.env.context = {**self.env.context, "recipients": []}
        return resultat or emails

    def _bf_oublier_suivis(self, ecartees):
        """Retirer le suivi que ``mail_tracking`` a créé pour une copie écartée.

        Ce module passe avant ``mail_tracking`` dans l'ordre des surcharges :
        le suivi naît pour chaque copie du noyau, y compris la générique qu'on
        retire ensuite. Sans ce ménage, chaque envoi avec copie conforme
        laisserait au chatter un suivi « inconnu », jamais envoyé.
        """
        if not ecartees or "mail.tracking.email" not in self.env:
            return
        Serveur = self.env["ir.mail_server"]
        if not hasattr(Serveur, "_tracking_email_id_body_get"):
            return
        ids = []
        for email in ecartees:
            ident = Serveur._tracking_email_id_body_get(email.get("body") or "")
            if ident:
                ids.append(int(ident))
        if ids:
            self.env["mail.tracking.email"].sudo().browse(ids).exists().unlink()
