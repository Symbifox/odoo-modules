from odoo import _, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_note_default_reminder = fields.Selection(
        [
            ("none", "None"),
            ("today", "Today"),
            ("tomorrow", "Tomorrow"),
            ("2days", "+2 days"),
            ("1week", "+1 week"),
        ],
        string="Default reminder (Notepad)",
        default="today",
        help="Reminder preselected in the quick creation dialog (Alt+N).",
    )

    # Mise en page des notes : une préférence de l'usager, suivie par
    # Symbifox, la page /notes et le mobile. Chaque appareil peut la surcharger
    # localement ; le serveur ne garde que le choix de l'usager.
    bf_note_layout = fields.Selection(
        [("cards", "Cards"), ("minimal", "Minimal"), ("list", "List")],
        string="Notes layout",
        default="cards",
        help="Cards: masonry grid with the color as background, like Google Keep. "
             "Minimal: one line per note, the color as a stripe. List: detailed rows.",
    )
    bf_note_density = fields.Selection(
        [("comfortable", "Comfortable"), ("compact", "Compact")],
        string="Notes density",
        default="comfortable",
    )
    bf_note_color_style = fields.Selection(
        [("fill", "Background"), ("stripe", "Stripe")],
        string="Note color shown as",
        default="fill",
    )
    bf_note_group_by_color = fields.Boolean(string="Group notes by color")

    # 🔴 Un compte non humain est un
    # usager interne comme un autre pour l'ORM. Rien, dans une fiche d'usager,
    # ne dit « ceci est un robot » : ni `share` (qui ne separe que le portail),
    # ni un employe (`hr` n'est pas une dependance, et un humain peut n'en pas
    # avoir), ni l'absence de mot de passe (un
    # humain qui entre par SSO n'en a pas non plus). C'est donc une DECISION
    # d'administration, portee par une case, et non un devinement.
    bf_note_no_shared_notes = fields.Boolean(
        string="Does not receive shared notes",
        help="Tick this for a service account (API, automation, Gen): the notepad "
             "never names it as a person to share a note with, and a note shared "
             "with a group or a channel never reaches it either.",
    )

    def _bf_note_no_shares(self):
        """Ce compte est-il hors du partage des notes ?

        Deux sources, et une seule reponse : la case de la fiche, et le
        parametre systeme `bf_bloc_notes.comptes_exclus` d'avant ce correctif,
        garde pour le locataire qui l'a pose (la migration 18.0.4.6.0 en fait
        des cases, et ne touche a rien d'autre).

        ⚠️ Lu sans `sudo()` : la valeur d'un booleen de `res.users` ne depend
        pas de qui lit, et un compte interne la lit deja. Un `sudo()` ici ne
        protegerait rien et deposerait une valeur de superutilisateur dans le
        cache partage, exactement le defaut B4 de la meme relecture.
        """
        self.ensure_one()
        if self.bf_note_no_shared_notes:
            return True
        return self.id in self.env["bf.note.share"]._comptes_exclus()

    BF_NOTE_PREF_FIELDS = [
        "bf_note_default_reminder",
        "bf_note_layout",
        "bf_note_density",
        "bf_note_color_style",
        "bf_note_group_by_color",
    ]

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + self.BF_NOTE_PREF_FIELDS

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + self.BF_NOTE_PREF_FIELDS

    def action_open_bf_note_preferences(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Notepad preferences"),
            "res_model": "res.users",
            "res_id": self.env.user.id,
            "view_mode": "form",
            "view_id": self.env.ref("bf_bloc_notes.bf_note_user_pref_view_form").id,
            "target": "new",
        }
