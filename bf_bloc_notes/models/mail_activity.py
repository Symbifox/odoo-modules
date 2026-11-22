"""🔴 Une activité ne se pose pas sur la note d'un autre.

Mesuré en relecture : un destinataire en écriture a
posé sur la note d'une collègue une activité assignée à Gen. L'écriture sur
`bf.note` ouvre toute la surface `mail.thread`, et `mail.activity` juge la fiche
PARENTE — « create : règle d'accès ET droit d'écriture (ou `mail_post_access`)
sur le document lié » —, exactement comme `ir.attachment` pour les pièces (B1).

⚠️ La garde est posée ICI, à `create` et à `write`, et non au seul
`activity_schedule` : le dialogue « Planifier une activité » du chatter crée la
`mail.activity` directement par RPC, et `mail.activity` est en écriture complète
pour `base.group_user` à l'ACL de `mail`. Une garde se juge là où elle est
APPELÉE, et `activity_schedule` n'est qu'un appelant parmi d'autres (plans
d'activités, assistants, actions serveur).

La phrase du refus, elle, ne se réécrit pas : elle vit à un seul endroit,
`bf.note._exige_de_gerer_le_fil`, avec l'abonnement.

⛔ Ce que cette garde NE ferme PAS, et c'est voulu : retirer une activité
(`unlink`, donc « marquer comme fait ») et en décaler l'échéance restent à qui
la porte. Sans quoi l'auteur ne pourrait plus assigner une activité à son
destinataire — un geste légitime de l'auteur — sans la lui rendre intouchable.
Ce qui se ferme, c'est de la POSER et de la RÉASSIGNER.
"""
from odoo import api, models

#: Les clés d'une écriture qui pose ou qui réassigne. Le reste (échéance, note,
#: résumé, `active`) reste à qui porte l'activité.
CLES_QUI_POSENT = ("res_model", "res_model_id", "res_id", "user_id")


class MailActivity(models.Model):
    _inherit = "mail.activity"

    @api.model
    def _bf_note_modele_vise(self, vals):
        """Le modèle que ces valeurs posent, ou ``None`` si elles n'en posent pas.

        ⚠️ Les deux clés : l'écran envoie `res_model_id`, le code `res_model`
        (un `related` stocké de l'autre). Ne lire qu'une des deux laisserait le
        second chemin passer.
        """
        if vals.get("res_model"):
            return vals["res_model"]
        if vals.get("res_model_id"):
            modele = vals["res_model_id"]
            modele = modele.id if hasattr(modele, "id") else modele
            return self.env["ir.model"].sudo().browse(int(modele)).model
        return None

    def _bf_note_exige_de_gerer(self, vals):
        """Refuse de poser ou de réassigner sur une note qu'on ne gère pas.

        Les notes visées sont celles d'AVANT (ce que ces activités portent) et
        celle d'APRÈS (ce que l'écriture poserait) : déplacer une activité d'une
        note à l'autre est un geste sur les deux.
        """
        if self.env.su:
            return
        ids = set()
        for activite in self.sudo():
            if activite.res_model == "bf.note" and activite.res_id:
                ids.add(activite.res_id)
        modele = self._bf_note_modele_vise(vals)
        if modele is None and "res_id" in vals:
            # 🔴 Une ecriture qui ne change que `res_id` garde le modele
            # d'avant : sans cette reprise, deplacer une activite de SA note
            # vers celle d'un autre passait. Trouve par l'essai, pas par la
            # relecture.
            modeles = {activite.res_model for activite in self.sudo()}
            modele = modeles.pop() if len(modeles) == 1 else None
        if modele == "bf.note" and vals.get("res_id"):
            ids.add(int(vals["res_id"]))
        if not ids:
            return
        # 🔴 Sous l'usager RÉEL, jamais en sudo : `_exige_de_gerer_le_fil` passe
        # la main dès que `self.env.su`.
        notes = self.env["bf.note"].with_user(self.env.user).browse(sorted(ids)).exists()
        notes._exige_de_gerer_le_fil(geste="activite")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self.browse()._bf_note_exige_de_gerer(vals)
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) & set(CLES_QUI_POSENT):
            self._bf_note_exige_de_gerer(vals)
        return super().write(vals)
