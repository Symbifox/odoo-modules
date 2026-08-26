# -*- coding: utf-8 -*-
"""Insérer un lien de réservation sans quitter le courriel qu'on écrit.

L'assistant du menu fabrique très bien un lien, mais il oblige à sortir du
courriel, à retrouver le destinataire dans une liste, puis à revenir coller
l'adresse. Quand on est déjà en train d'écrire à quelqu'un, tout ça est connu :
le bouton s'en sert.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    # ------------------------------------------------------------------
    # Le lien, montré DANS le courriel plutôt que dans une autre fenêtre
    # ------------------------------------------------------------------

    bf_booking_id = fields.Many2one(
        "resource.booking", string="Lien de rendez-vous créé",
        readonly=True, copy=False,
        help="La réservation fabriquée par le bouton « Copier », gardée le "
             "temps de la rédaction pour ne pas en créer une par clic.")
    # ⚠️ CALCULÉ, non stocké, et ce n'est pas une économie de colonne : le lien
    # vaut jeton d'accès, et un modèle transitoire écrit dans une VRAIE table
    # qui survit jusqu'au ramasse-miettes et part dans toute sauvegarde prise
    # entre-temps. Le jeton vit déjà sur la réservation. Même raisonnement que
    # `bf.appointment.onetime.wizard`, et un seul écrivain.
    bf_booking_link_url = fields.Char(
        string="Lien à transmettre", compute="_compute_bf_booking_link")
    # Datetime plutôt qu'un texte fabriqué à la main : Odoo l'affiche dans le
    # fuseau de qui lit. Un `to_string` aurait sorti de l'UTC à l'écran.
    bf_booking_link_expires = fields.Datetime(
        related="bf_booking_id.link_expires_at", string="Expiration",
        readonly=True)

    @api.depends("bf_booking_id")
    def _compute_bf_booking_link(self):
        for composer in self:
            composer.bf_booking_link_url = (
                composer.bf_booking_id.one_time_url or "")

    def _bf_quick_link_partner(self):
        """Le destinataire du lien : d'abord celui du courriel, sinon celui du
        document auquel on répond."""
        self.ensure_one()
        if self.partner_ids:
            return self.partner_ids[0]
        if self.model and self.res_ids:
            try:
                ids = self._evaluate_res_ids()
            except Exception:  # pragma: no cover - contextes exotiques
                ids = []
            if ids:
                record = self.env[self.model].browse(ids[0]).exists()
                for champ in ("partner_id", "partner_ids", "commercial_partner_id"):
                    if champ in record._fields and record[champ]:
                        valeur = record[champ]
                        return valeur[0] if len(valeur) > 1 else valeur
        return self.env["res.partner"]

    def _bf_quick_link_type(self):
        """Le type à employer : le réglage de la société, sinon le premier type
        public listé."""
        societe = self.env.company
        if societe.appointment_quick_link_type_id:
            return societe.appointment_quick_link_type_id
        return self.env["resource.booking.type"].search(
            [("is_public", "=", True), ("listed_on_landing", "=", True)],
            order="sequence, id", limit=1,
        )

    def _bf_prepare_booking_link(self):
        """Résout le destinataire et le type, puis crée le lien. Rend la
        réservation.

        Partagé par les deux boutons du compositeur : insérer dans le corps, et
        copier. Deux copies de cette séquence divergeraient au premier
        ajustement.
        """
        self.ensure_one()
        partner = self._bf_quick_link_partner()
        if not partner:
            raise UserError(_(
                "Aucun destinataire n'est encore choisi. Ajoutez la personne "
                "dans « À », puis reprenez : le lien est personnel, il lui sera "
                "rattaché."
            ))
        if not partner.email:
            raise UserError(_(
                "%s n'a pas d'adresse courriel. Le lien lui serait rattaché "
                "sans qu'on puisse lui écrire.", partner.display_name))
        booking_type = self._bf_quick_link_type()
        if not booking_type:
            raise UserError(_(
                "Aucun type de rendez-vous public n'est configuré. Choisissez-en "
                "un dans Configuration, sous « Type pour les liens rapides »."
            ))
        return booking_type._bf_create_onetime_link(partner)

    def action_bf_insert_booking_link(self):
        """Crée un lien personnel et le glisse dans le corps, avant la signature."""
        self.ensure_one()
        booking = self._bf_prepare_booking_link()
        libelle = _("Choisir un moment qui vous convient")
        extrait = (
            '<p><a href="%s" target="_blank" rel="noopener">%s</a></p>'
            % (booking.one_time_url, libelle)
        )
        # ⚠️ `body` est un Markup : y concaténer une str ÉCHAPPE les balises, et
        # le rédacteur voit « &lt;p&gt;&lt;a href… » en clair dans son courriel.
        # On repasse donc par une str AVANT toute opération. Signalé en
        # production le 2026-08-20, sur la première version de ce bouton.
        corps = str(self.body or "")
        point = self._bf_insertion_point(corps)
        self.body = corps[:point] + extrait + corps[point:]
        _logger.info(
            "Lien de rendez-vous %s inséré au courriel pour %s",
            booking.id, booking.partner_ids[:1].display_name)
        return self._bf_reopen_composer()

    def action_bf_copy_booking_link(self):
        """Crée le lien et l'affiche dans le courriel, prêt à être copié.

        ⚠️ La copie n'est PAS faite par le serveur, et ce n'est pas un défaut :
        écrire dans le presse-papiers exige une activation récente par
        l'usager. Une copie déclenchée après un aller-retour serveur se fait
        bloquer en silence par Safari et certaines versions de Chrome. Le
        widget natif d'Odoo garde le geste et la copie collés — ça marche
        partout, sans une ligne de JavaScript maison.

        🔴 Ce bouton ouvrait une FENÊTRE portant ce widget, et le courriel en
        cours de rédaction disparaissait de l'écran (signalé le 2026-08-25).
        Ce n'est pas un accident d'implémentation, c'est le fonctionnement du
        client : une action `target: "new"` rendue depuis un dialogue ne s'y
        empile pas, elle le REMPLACE — voir `_bf_reopen_composer`. Le lien
        s'affiche donc maintenant dans le compositeur lui-même.

        Un deuxième clic ne refabrique rien tant que le lien tient toujours et
        que le destinataire n'a pas changé : sans cette garde, chaque clic
        laissait une réservation en attente derrière lui.
        """
        self.ensure_one()
        booking = self.bf_booking_id
        destinataire = self._bf_quick_link_partner()
        if not (booking and booking.exists() and destinataire
                and destinataire in booking.partner_ids
                and booking._link_is_usable()):
            booking = self._bf_prepare_booking_link()
            self.bf_booking_id = booking
            _logger.info(
                "Lien de rendez-vous %s offert à la copie pour %s",
                booking.id, booking.partner_ids[:1].display_name)
        return self._bf_reopen_composer()

    def _bf_reopen_composer(self):
        """Rouvre le compositeur sur le brouillon en cours.

        🔴 Sans ça, le courriel se ferme. Une action `target: "new"` rendue
        depuis un bouton du compositeur ne s'AJOUTE pas par-dessus lui : le
        client retire le dialogue courant avant d'ouvrir le suivant
        (`web/.../action_service.js`, `_updateUI` : `if (nextDialog) {
        nextDialog.remove(); }`). Le brouillon, lui, survit — le clic sur un
        bouton `type="object"` enregistre l'assistant avant d'appeler la
        méthode — mais plus rien à l'écran n'y ramène, et la personne le
        tient pour perdu.

        Rendre le compositeur sur son propre `res_id` le rouvre avec tout ce
        qui était rédigé : corps, destinataires, pièces jointes.
        """
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "mail.compose.message",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": self.env.context,
        }

    # ------------------------------------------------------------------
    # Où glisser le lien
    # ------------------------------------------------------------------

    def _bf_insertion_point(self, corps):
        """Index où insérer : avant la signature, avant l'historique cité,
        sinon à la fin.

        Le compositeur d'Odoo compose le corps comme `{texte}<br>{signature}`,
        sans aucun marqueur autour de la signature (`composer.js`,
        `formatDefaultBodyForFullComposer`). Il n'y a donc rien à chercher :
        c'est la signature de l'usager elle-même qui sert de repère.

        Ajouter à la toute fin mettait le lien SOUS la signature, ce qui se lit
        comme une note de bas de page plutôt que comme une invitation.
        """
        self.ensure_one()
        if not corps:
            return 0
        reperes = []

        i = self._bf_signature_index(corps, str(self.env.user.signature or ""))
        if i > 0:
            reperes.append(i)

        # Historique d'une réponse : le lien doit précéder ce qui est cité.
        for marqueur in ("<blockquote", "data-o-mail-quote", "gmail_quote"):
            j = corps.find(marqueur)
            if j > 0:
                ouverture = corps.rfind("<", 0, j + 1)
                reperes.append(ouverture if ouverture > 0 else j)

        return min(reperes) if reperes else len(corps)

    @staticmethod
    def _bf_signature_index(corps, signature):
        """Où commence la signature dans le corps, ou -1.

        ⚠️ Comparer la signature LITTÉRALEMENT ne marche pas : Odoo assainit le
        HTML à l'écriture. Mesuré sur la signature de production le 2026-08-20 :
        11 337 caractères deviennent 10 700, avec en prime des espaces insérés
        dans les styles (`border-style:none` → `border-style: none`). Un
        `rfind` de la signature brute échoue donc toujours, en silence, et le
        lien retombe à la fin.

        On compare sans aucun espace, et sur un PRÉFIXE : la tête de la
        signature survit à l'assainissement, sa queue pas toujours.
        """
        import re as _re

        if not signature:
            return -1
        compact_sig = _re.sub(r"\s+", "", signature)
        # Seuil bas volontairement : quelqu'un peut signer « Jane Doe » sans
        # mise en forme, ce qui fait moins de 30 caractères compactés. Le
        # risque d'une correspondance fortuite est écarté par le `rfind` :
        # c'est la DERNIÈRE occurrence qui compte, et la signature est en
        # queue de corps. Un seuil à 40 rejetait ces signatures-là d'emblée,
        # et le lien retombait à la fin sans rien dire.
        if len(compact_sig) < 12:
            return -1
        # Carte position-sans-espace -> position d'origine.
        positions = [k for k, ch in enumerate(corps) if not ch.isspace()]
        compact_corps = "".join(corps[k] for k in positions)
        # ⚠️ La dernière taille est la signature ENTIÈRE. Sans elle, une
        # signature compactée à moins de 60 caractères ne déclenchait AUCUN
        # essai — toutes les tailles étant sautées faute de longueur — et le
        # lien retombait à la fin sans que rien ne le signale. Les signatures
        # réelles font des milliers de caractères, donc le défaut ne se voyait
        # pas en production ; un test l'a démasqué le 2026-08-20.
        for taille in (400, 200, 100, 60, len(compact_sig)):
            if len(compact_sig) < taille:
                continue
            j = compact_corps.rfind(compact_sig[:taille])
            if j > 0:
                return positions[j]
        return -1
