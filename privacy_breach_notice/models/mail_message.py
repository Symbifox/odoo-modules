"""Le fil d'un avis est un registre : n'y écrit que le système, ou qui peut écrire sur l'avis.

Odoo laisse un abonné créer des messages sans droit d'écriture, et laisse tout lecteur
s'abonner ; plusieurs chemins du cœur créent aussi des messages en superutilisateur avec des
valeurs fournies par l'appelant (`send_mail` et ses `email_values`, l'activité marquée faite, la
note d'un envoi de SMS en masse). Exempter `env.su` laissait donc tout passer. La garde juge
l'ACTEUR réel, sudo ou non : le système (OdooBot, la passerelle, la fédération, la page
d'accusé, qui tournent en SUPERUSER_ID), un administrateur, ou un utilisateur qui a l'écriture
sur l'avis. Elle juge le résultat (pas `vals`, que les défauts du contexte contournent), et la
réécriture ou la suppression d'un message existant autant que sa création. Ce qui se rattache à un
message existant (notification, courriel, pièce jointe) suit la même règle : le « renvoi » d'Odoo
renvoyait le courriel officiel à un tiers sans créer de message (voir mail_attached.py).
"""
from odoo import SUPERUSER_ID, _, api, models
from odoo.exceptions import AccessError

WATCHED = "privacy.breach.notice"

# Ce qu'un lecteur peut changer à un message du fil : son étoile, son épingle, son « à traiter ».
# Tout le reste (contenu, auteur, destinataires, notifications, serveur, liens) est le registre.
ALLOWED = {"starred_partner_ids", "pinned_at", "mail_tracking_needs_action"}


class MailMessage(models.Model):
    _inherit = "mail.message"

    def _breach_register_actor_is_system(self):
        # Une route `auth='none'` n'a pas d'utilisateur : ce n'est pas le système.
        return self.env.uid == SUPERUSER_ID or (bool(self.env.uid) and self.env.user._is_system())

    def _breach_check_register(self, res_ids, existing_only=False):
        """L'acteur réel peut-il écrire sur ces avis ? Un avis absent compte comme refusé : un
        message posé d'avance sur un numéro à venir entrerait au fil du prochain avis."""
        res_ids = sorted(set(filter(None, res_ids)))
        if not res_ids or self._breach_register_actor_is_system():
            return
        # Jugé dans les sociétés de l'acteur qui portent ces avis, pas dans celles qu'il a cochées :
        # le cœur laisse un assigné solder son activité hors des sociétés cochées.
        companies = self.env.user.company_ids & self.env[WATCHED].sudo().browse(res_ids).exists().company_id
        notices = self.env[WATCHED].with_env(self.env(su=False)).with_context(
            allowed_company_ids=(companies or self.env.user.company_id).ids).browse(res_ids)
        existing = notices.exists()
        if existing_only:
            notices = existing
        elif notices - existing:
            raise self._breach_register_refusal()
        try:
            notices.check_access("write")
        except AccessError:
            # Le message d'Odoo nomme les fiches refusées en mode débogage ; celui-ci, non.
            raise self._breach_register_refusal() from None

    # -- Messages d'avis créés sur ce curseur ---------------------------------------------------
    # Odoo complète un message juste après l'avoir créé : il rattache les pièces d'une activité
    # soldée, bf_email corrige le corps d'un courriel classé. Le curseur retient qui a créé quel
    # message d'avis pendant sa vie (une requête ; un passage de cron entier pour un worker cron) ;
    # l'appelant n'y a aucun accès (ce n'est pas une clé de contexte).
    def _breach_fresh_ids(self):
        cr = self.env.cr
        fresh = getattr(cr, "_breach_fresh_messages", None)
        if fresh is None:
            fresh = set()
            cr._breach_fresh_messages = fresh
        return fresh

    def _breach_is_fresh(self):
        fresh = self._breach_fresh_ids()
        return all((self.env.uid, m.id) in fresh for m in self)

    def _breach_check_frozen(self, messages):
        """Un message déjà au fil d'un avis envoyé ne se réécrit ni ne s'efface, pas même par le
        gestionnaire : seul le système y touche. Son auteur peut le compléter dans la transaction
        qui l'a créé ; le brouillon reste à qui peut écrire sur l'avis."""
        if self._breach_register_actor_is_system():
            return
        ours = messages.sudo().filtered(lambda m: m.model == WATCHED and m.res_id)
        if not ours:
            return
        notices = self.env[WATCHED].sudo().browse(ours.mapped("res_id")).exists()
        sent = notices.filtered(lambda n: n.state != "draft")
        frozen = ours.filtered(lambda m: m.res_id in sent.ids)
        if frozen and not frozen.with_env(self.env)._breach_is_fresh():
            raise AccessError(_("Le fil d'un avis de violation envoyé est un registre : ses messages ne se "
                                "modifient ni ne se suppriment. Ajoutez une note qui corrige."))
        self._breach_check_register((notices - sent).ids, existing_only=True)

    def _breach_changed_fields(self, vals):
        """Les champs gardés dont la valeur change vraiment : l'envoi réécrit `message_id` à
        l'identique après le renvoi d'un courriel, ce n'est pas une réécriture du registre."""
        changed = set()
        for fname in set(vals) - ALLOWED:
            field = self._fields.get(fname)
            if field is None or field.type in ("one2many", "many2many"):
                changed.add(fname)
                continue
            for record in self.sudo():
                try:
                    new = field.convert_to_record(field.convert_to_cache(vals[fname], record), record)
                except Exception:  # noqa: BLE001 — valeur inconvertible : jugée comme un changement
                    changed.add(fname)
                    break
                if record[fname] != new:
                    changed.add(fname)
                    break
        return changed

    def _breach_register_refusal(self):
        return AccessError(_("Le fil d'un avis de violation est un registre : on n'y écrit qu'avec "
                             "le droit d'écrire sur l'avis."))

    def _breach_res_ids(self):
        return self.sudo().filtered(lambda m: m.model == WATCHED and m.res_id).mapped("res_id")

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        self._breach_check_register(messages._breach_res_ids())
        self._breach_fresh_ids().update(
            (self.env.uid, m.id) for m in messages.sudo().filtered(lambda m: m.model == WATCHED))
        return messages

    def write(self, vals):
        ours = self.sudo().filtered(lambda m: m.model == WATCHED and m.res_id)
        guarded = (not self._breach_register_actor_is_system() and bool(set(vals) - ALLOWED)
                   and (bool({"model", "res_id"} & set(vals)) or bool(ours))
                   and bool(self._breach_changed_fields(vals)))
        if guarded:
            self._breach_check_frozen(self)
        res = super().write(vals)
        if guarded and {"model", "res_id"} & set(vals):
            self._breach_check_register(self._breach_res_ids())
        return res

    def unlink(self):
        if not self._breach_register_actor_is_system():
            # Un avis supprimé n'a plus de fil à protéger : son nettoyage passe.
            self._breach_check_frozen(self)
        return super().unlink()

    def _message_reaction(self, content, action, partner, guest, store=None):
        """Une réaction est un texte libre posé sur le message : au fil d'un avis, comme le reste."""
        self._breach_check_register(self._breach_res_ids())
        return super()._message_reaction(content, action, partner, guest, store=store)
