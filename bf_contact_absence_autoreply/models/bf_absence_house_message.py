"""Le message que la maison envoie quand personne n'en a écrit un.

Le répondeur d'absence (`bf.email.absence`) sait tout faire et n'avait
pourtant jamais servi : zéro enregistrement sur toutes les bases où il tourne.
La cause n'est pas la qualité, c'est qu'il fallait **avoir déjà rédigé un
gabarit** pour que quoi que ce soit s'arme. Un texte qu'on doit écrire avant
que la fonction existe est un texte que personne n'écrit.

D'où ce modèle : deux messages posés à l'installation, modifiables, et c'est
tout. Ils ne remplacent pas le « message type » personnel (`is_template` sur
`bf.email.absence`), qui garde la priorité quand il existe.

⚠️ **La relève ne passe pas par un marqueur.** `{releve}` se remplace par une
chaîne vide quand personne n'est nommé, ce qui laisse « écrivez à  () » dans un
courriel qui part chez un client. La phrase de relève est donc un champ à part,
ajoutée seulement quand il y a quelqu'un à nommer, et le nom y est écrit en
clair au moment où on arme.
"""

from odoo import api, fields, models

TONS = [
    ("delay", "Je réponds moins vite"),
    ("away", "Je suis à l'extérieur"),
]


class BfAbsenceHouseMessage(models.Model):
    _name = "bf.absence.house.message"
    _description = "Message d'absence de la maison"
    _order = "sequence, id"

    sequence = fields.Integer(string="Séquence", default=10)
    tone = fields.Selection(
        selection=TONS,
        string="Ton",
        required=True,
        help="« Je réponds moins vite » annonce un délai, « Je suis à "
             "l'extérieur » annonce une absence. Le premier tient même quand "
             "on finit par répondre, et c'est le cas le plus fréquent.\n\n"
             "⚠️ Aucun des deux textes ne s'accorde en genre : Odoo ne porte "
             "pas le genre d'une personne, et un message de maison part sous "
             "le nom de n'importe qui.",
    )
    name = fields.Char(
        string="Audience",
        required=True,
        default="Tout le monde",
        help="Le libellé de la ligne de message créée dans le répondeur. Pour "
             "vous, pas pour l'expéditeur.",
    )
    body_html = fields.Html(
        string="Message",
        required=True,
        translate=True,
        sanitize=True,
        help="Marqueurs disponibles : {nom}, {retour}, {motif}. La relève se "
             "pose par la phrase ci-dessous, pas par un marqueur.",
    )
    backup_html = fields.Html(
        string="Phrase de relève",
        translate=True,
        sanitize=True,
        help="Ajoutée au message SEULEMENT quand une relève est nommée. "
             "[[releve]] y est remplacé par le nom, [[courriel]] par "
             "l'adresse ou le texte libre.\n\n"
             "⚠️ Deux crochets et non %(...)s : dans un fichier de données "
             "Odoo, %(quelquechose)s est une RÉFÉRENCE à un identifiant XML, "
             "et le module refuse de s'installer.",
    )
    active = fields.Boolean(string="Actif", default=True)

    @api.model
    def _for_tone(self, tone):
        """Le message de maison de ce ton, ou un ensemble vide."""
        return self.sudo().search([("tone", "=", tone or "delay")], limit=1)

    def _rendered_body(self, backup_name=False, backup_contact=False):
        """Le corps à déposer dans le répondeur, relève comprise ou non.

        Le rendu se fait **à l'armement** et non à l'envoi : c'est le seul
        moment où l'on sait s'il y a quelqu'un à nommer. Les marqueurs du
        répondeur ({nom}, {retour}) restent, eux, dans le texte : ils se
        remplissent au moment de répondre, et {retour} dépend de la période.
        """
        self.ensure_one()
        corps = self.body_html or ""
        if not backup_name or not self.backup_html:
            return corps
        phrase = self.backup_html.replace("[[releve]]", backup_name)
        phrase = phrase.replace("[[courriel]]", backup_contact or "")
        if not backup_contact:
            # Sans adresse, la parenthèse resterait vide dans un courriel qui
            # part chez un client.
            phrase = phrase.replace(" ()", "").replace("()", "")
        return corps + phrase

    @api.model
    def _default_tone(self):
        """Le ton par défaut, réglable par paramètre d'instance."""
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "bf_absence.default_tone")
        return raw if raw in dict(TONS) else "delay"

    @api.depends("tone", "name")
    def _compute_display_name(self):
        tons = dict(TONS)
        for message in self:
            message.display_name = "%s (%s)" % (
                tons.get(message.tone, ""), message.name or "")
