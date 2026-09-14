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
    ("delay", "I reply more slowly"),
    ("away", "I am out of the office"),
]


class BfAbsenceHouseMessage(models.Model):
    _name = "bf.absence.house.message"
    _description = "House absence message"
    _order = "sequence, id"

    sequence = fields.Integer(string="Sequence", default=10)
    tone = fields.Selection(
        selection=TONS,
        string="Tone",
        required=True,
        help="\"I reply more slowly\" announces a delay, \"I am out of "
             "the office\" announces an absence. The first holds even "
             "when you end up replying, which is the most common "
             "case.\n\n⚠️ Neither text agrees in gender: Odoo does not "
             "carry a person's gender, and a house message goes out under "
             "anybody's name.",
    )
    name = fields.Char(
        string="Audience",
        required=True,
        translate=True,
        default=lambda self: self.env._("Everyone"),
        help="The label of the message line created in the responder. For "
             "you, not for the sender.",
    )
    body_html = fields.Html(
        string="Message",
        required=True,
        translate=True,
        sanitize=True,
        help="Available markers: {nom}, {retour}, {motif}. The stand-in "
             "is set by the sentence below, not by a marker.",
    )
    backup_html = fields.Html(
        string="Stand-in sentence",
        translate=True,
        sanitize=True,
        help="Added to the message ONLY when a stand-in is named. "
             "[[releve]] is replaced by the name, [[courriel]] by the "
             "address or the free text.\n\n⚠️ Double brackets, never the "
             "percent form: in an Odoo data file, a percent placeholder "
             "is a REFERENCE to an XML identifier, and the module refuses "
             "to install.",
    )
    active = fields.Boolean(string="Active", default=True)

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
    @api.depends_context("lang")
    def _compute_display_name(self):
        tons = dict(self._fields["tone"]._description_selection(self.env))
        for message in self:
            message.display_name = "%s (%s)" % (
                tons.get(message.tone, ""), message.name or "")
