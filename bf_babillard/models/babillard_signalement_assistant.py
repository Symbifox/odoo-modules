# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class BabillardSignalementAssistant(models.TransientModel):
    """Le dialogue « Signaler ce contenu ».

    🔴 Pourquoi un assistant plutôt que le formulaire du signalement : Odoo relit
    l'enregistrement après l'avoir sauvé, donc signaler par le formulaire exigeait
    le droit de LIRE le signalement. Ce droit ouvrait aussi à la personne qui
    signale la suite donnée et les échanges de la modération. L'assistant crée le
    signalement en son nom, puis s'efface.

    🔴 Des identifiants, pas des many2one. Odoo lit le nom d'un many2one en
    superutilisateur (`Many2one.convert_to_read`) : un `onchange` appelé avec
    `default_post_id` rendait le titre d'un brouillon ou d'une publication hors
    audience, et `default_message_id` le nom de n'importe quelle fiche de la base.
    Un entier ne porte pas de nom, et le titre affiché est relu avec les droits de
    la personne.

    ⚠️ Un enregistrement transitoire n'est PAS réservé à qui l'a créé en Odoo 18 :
    sans la règle `rule_assistant_le_mien`, tout le personnel lisait les motifs en
    attente de purge.
    """

    _name = "bf.babillard.signalement.assistant"
    _description = "Signaler un contenu du babillard"

    post_ref = fields.Integer("Publication visée", required=True)
    message_ref = fields.Integer("Commentaire visé")
    # Une valeur posée avec les défauts, pas un calcul : un calcul sans dépendance
    # n'est pas évalué sur l'enregistrement neuf du dialogue, et le titre restait
    # vide à l'écran. Vu sur la capture, pas par les essais.
    post_titre = fields.Char("Publication", readonly=True)
    # Une valeur par défaut, pas un champ calculé : un calcul sans dépendance
    # n'est pas évalué sur l'enregistrement neuf que le dialogue ouvre, et
    # « Signalé par » s'affichait vide. Vu sur la capture, pas par les essais.
    auteur_nom = fields.Char(
        "Signalé par", readonly=True, default=lambda s: s.env.user.name)
    motif = fields.Text("Motif", required=True)

    def _publication(self):
        """La publication visée, lue avec les droits de la personne qui signale."""
        self.ensure_one()
        post = self.env["bf.babillard.post"].browse(self.post_ref).exists()
        if post:
            post.check_access("read")
        return post

    @api.model
    def default_get(self, champs):
        """Les valeurs par défaut viennent du contexte : elles se vérifient ici.

        C'est le chemin que prend `onchange` sur un enregistrement neuf.
        """
        valeurs = super().default_get(champs)
        post_ref = valeurs.get("post_ref")
        if post_ref:
            post = self.env["bf.babillard.post"].browse(post_ref).exists()
            if not post:
                valeurs.pop("post_ref", None)
            else:
                post.check_access("read")
        if valeurs.get("post_ref"):
            valeurs["post_titre"] = self.env["bf.babillard.post"].browse(
                valeurs["post_ref"]).name
        if valeurs.get("message_ref") and not self._message_de_la_publication(
                valeurs["message_ref"], valeurs.get("post_ref")):
            valeurs.pop("message_ref", None)
        return valeurs

    @api.model
    def _message_de_la_publication(self, message_ref, post_ref):
        message = self.env["mail.message"].sudo().browse(message_ref).exists()
        return bool(message and post_ref
                    and message.model == "bf.babillard.post"
                    and message.res_id == post_ref)

    @api.constrains("post_ref", "message_ref")
    def _check_contenu_lisible(self):
        """On ne signale que ce qu'on peut lire."""
        for assistant in self:
            post = assistant._publication()
            if not post:
                raise AccessError(_("Cette publication n'existe plus."))
            if assistant.message_ref and not self._message_de_la_publication(
                    assistant.message_ref, assistant.post_ref):
                raise AccessError(_("Ce commentaire n'appartient pas à cette "
                                    "publication."))

    def action_envoyer(self):
        self.ensure_one()
        self._check_contenu_lisible()
        signalement = self.env["bf.babillard.signalement"].sudo().create({
            "post_id": self.post_ref,
            "message_id": self.message_ref or False,
            "motif": self.motif,
            "auteur_signalement_id": self.env.uid,
        })
        sans_destinataire = signalement.sans_destinataire
        # Le dialogue s'efface : son motif n'a pas à attendre la purge.
        self.sudo().unlink()
        if sans_destinataire:
            return self._fermer(
                "warning",
                _("Votre signalement est enregistré, mais personne d'autre que la "
                  "personne visée ne peut le recevoir ici. Adressez-vous à la "
                  "personne désignée par la politique de votre organisation, ou à "
                  "la CNESST."))
        return self._fermer(
            "success", _("Votre signalement est parti vers la personne désignée."))

    @api.model
    def _fermer(self, genre, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": genre,
                "message": message,
                "sticky": genre == "warning",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
