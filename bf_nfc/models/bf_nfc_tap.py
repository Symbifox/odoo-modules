"""Le journal : une ligne par tapotement, y compris ceux qui n'ont rien fait.

🔴 **Le journal retient la personne et la pastille, jamais l'adresse IP.** Sur des
liens tracés, une large part des clics enregistrés portent une adresse interne
(le conteneur, le mandataire), et ce malgré ``proxy_mode``. Une adresse ne dit
pas qui a tapé ; elle dit quelle machine a ouvert l'URL, ce qui n'est pas la même
question.

⚠️ La ligne survit à la pastille (``ondelete="set null"``) et garde son code en
clair : savoir qu'une pastille retirée a servi 40 fois est le genre de chose
qu'on veut encore pouvoir lire un an après.
"""
from odoo import fields, models


class BfNfcTap(models.Model):
    _name = "bf.nfc.tap"
    _description = "Tapotement de pastille NFC"
    _order = "tapped_at desc, id desc"

    tag_id = fields.Many2one("bf.nfc.tag", string="Pastille", ondelete="set null", index=True)
    tag_code = fields.Char(string="Code", readonly=True)
    gesture_code = fields.Char(string="Geste", readonly=True)
    user_id = fields.Many2one("res.users", string="Par", readonly=True, index=True)
    door = fields.Selection(
        [
            ("app", "Application"),
            ("session", "Navigateur"),
            ("signed", "Pastille signée"),
        ],
        string="Porte", readonly=True,
    )
    status = fields.Selection(
        [
            ("ok", "Fait"),
            ("duplicate", "Doublon écarté"),
            ("refused", "Refusé"),
            ("error", "Erreur"),
        ],
        string="Résultat", readonly=True, index=True,
    )
    titre = fields.Char(readonly=True)
    message = fields.Char(readonly=True)
    url = fields.Char(readonly=True)
    device_label = fields.Char(string="Appareil", readonly=True)
    counter = fields.Integer(
        string="Compteur de la puce", readonly=True,
        help="Compteur de lecture d'une pastille signée. Il ne remonte jamais.",
    )
    # 🔴 L'heure du TAPOTEMENT, qui n'est pas celle de l'écriture en base. Un
    # tapotement fait sans réseau arrive plus tard : la ronde de 3 h du matin
    # dans un sous-sol doit rester à 3 h, pas à l'heure où le téléphone a
    # retrouvé le réseau. `create_date` garde l'heure de réception.
    tapped_at = fields.Datetime(
        string="Tapée le", readonly=True, index=True, default=fields.Datetime.now,
    )
    offline = fields.Boolean(
        string="Envoyé en différé", readonly=True,
        help="Tapé sans réseau, envoyé ensuite. L'heure du tapotement est celle "
             "que le téléphone a notée, bornée par le serveur.",
    )
    # Une clé tirée par le téléphone pour CE tapotement. Le même envoi rejoué
    # (réseau coupé au moment de la réponse, file hors ligne relancée) rend la
    # ligne déjà écrite au lieu de refaire le geste.
    nonce = fields.Char(readonly=True, index=True, copy=False)
    choice_key = fields.Char(string="Choix", readonly=True)
    device_id = fields.Many2one("bf.nfc.device", string="Appareil apparié",
                                readonly=True, ondelete="set null")

    def _compute_display_name(self):
        for tap in self:
            tap.display_name = "%s · %s" % (tap.tag_code or "?", tap.titre or "")
