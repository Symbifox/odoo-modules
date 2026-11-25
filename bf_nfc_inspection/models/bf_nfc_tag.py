"""La grille que porte une pastille de relevé, et le guet des relevés manqués.

🔴 La grille est un CHAMP de la pastille, jamais un paramètre : ce qu'une pastille
fait se décide à sa création (cf. ``bf.nfc.tag._params`` du socle).
"""
from odoo import api, fields, models

from .langue import langue_de


class BfNfcTag(models.Model):
    _inherit = "bf.nfc.tag"

    checklist_id = fields.Many2one("bf.nfc.checklist", string="Inspection "
                                                              "checklist",
                                   ondelete="restrict", tracking=True)
    reading_ids = fields.One2many("bf.nfc.reading", "tag_id", string="Readings")
    last_reading_date = fields.Datetime(string="Last reading", compute="_compute_last_reading")
    last_missed_alert = fields.Datetime(string="Last missed-reading alert", readonly=True, copy=False)

    @api.depends("reading_ids.tapped_at")
    def _compute_last_reading(self):
        for tag in self:
            tag.last_reading_date = max(tag.reading_ids.mapped("tapped_at"), default=False)

    @api.model
    def _domaine_du_guet(self):
        """Les pastilles que le guet examine. Un satellite élargit (le point de tournée)."""
        return [("active", "=", True), ("checklist_id", "!=", False),
                ("gesture_id.kind", "=", "reading")]

    def _grille_du_guet(self):
        """La grille dont le rythme compte pour cette pastille."""
        self.ensure_one()
        return self.checklist_id

    @api.model
    def _cron_guetter_releves(self):
        """Une activité par pastille dont la grille a un rythme et qui n'a pas été relevée.

        ⚠️ Une seule alerte par période manquée : le guet tourne chaque jour, et une
        activité recréée chaque matin devient du bruit qu'on apprend à ignorer.
        ⚠️ Une pastille neuve n'est pas en retard le jour de sa pose : la période se
        compte depuis sa création quand elle n'a jamais été relevée.
        """
        maintenant = fields.Datetime.now()
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        pastilles = self.sudo().search(self._domaine_du_guet())
        for tag in pastilles:
            grille = tag._grille_du_guet()
            if not grille or grille.periode == "aucune":
                continue
            debut = grille._debut_de_periode(maintenant)
            reference = tag.last_reading_date or tag.create_date
            if not debut or reference >= debut:
                continue
            if tag.last_missed_alert and tag.last_missed_alert >= debut:
                continue
            responsable = grille.responsible_id or tag.create_uid
            tag.last_missed_alert = maintenant
            # Toute la note dans la langue de la personne responsable : le travail planifié n'a
            # pas de langue, et le nom de la grille comme le rythme se lisaient en anglais.
            env = tag.with_context(lang=langue_de(self.env, responsable)).env
            tag.activity_schedule(
                activity_type_id=todo.id if todo else False, user_id=responsable.id,
                summary=env._("Missed reading: %s", tag.place or tag.name),
                note=env._("The checklist “%(grille)s” must be read "
                           "%(rythme)s, and this tag's last reading was on "
                           "%(quand)s.",
                           grille=grille.with_env(env).name,
                           rythme=dict(grille._fields["periode"]._description_selection(env))[grille.periode].lower(),
                           quand=fields.Datetime.context_timestamp(tag, reference).strftime("%Y-%m-%d")))
