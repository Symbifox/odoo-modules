"""La page Réglages des pastilles, et la pose des clés des pastilles signées.

⚠️ **Pas un bloc de ``res.config.settings``.** Les Réglages d'Odoo sont réservés à
l'administrateur système ; les pastilles, elles, se gèrent par le groupe Gestion,
qui n'y a pas accès. Une page propre, sous Pastilles > Configuration, les met à
portée de qui les administre vraiment.

⚠️ Les schémas d'appariement restent réservés à l'administrateur système : c'est
la liste des adresses d'application qui reçoivent un code d'échange vivant, et
l'élargir sans comprendre PKCE ouvrirait une redirection.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..models import chiffrement
from ..models.bf_nfc_tag import (DIFFERE_DEFAUT_HEURES, FENETRE_DEFAUT, PARAM_DIFFERE_HEURES,
                                 PARAM_FENETRE)

PARAM_SCHEMAS = "bf_nfc.schemas_appariement"
SCHEMAS_DEFAUT = "com.bluefoxconsultant.pastilles://"

ORIGINES = [
    ("environnement", "Hors de la base (variable d'environnement)"),
    ("configuration", "Hors de la base (fichier de configuration)"),
    ("base", "Dans la base"),
    ("absente", "Pas encore tirée"),
]


def _exiger_la_gestion(env):
    if not env.user.has_group("bf_nfc.group_nfc_manager"):
        raise AccessError(_("Les réglages des pastilles sont réservés à la gestion."))


class BfNfcConfig(models.TransientModel):
    _name = "bf.nfc.config"
    _description = "Réglages des pastilles NFC"

    fenetre_doublon = fields.Integer(
        string="Fenêtre anti-doublon (s)",
        help="Deux tapotements identiques de la même personne dans cette fenêtre ne font "
             "le geste qu'une fois. Zéro : aucune garde.")
    differe_max_heures = fields.Integer(
        string="Différé accepté jusqu'à (h)",
        help="Un tapotement fait sans réseau est accepté s'il arrive dans ce délai.")
    schemas_appariement = fields.Char(
        string="Schémas d'appariement", groups="base.group_system",
        help="Adresses d'application autorisées à recevoir un code d'appariement, "
             "séparées par des virgules.")
    origine_cle = fields.Selection(ORIGINES, string="Clé de chiffrement", readonly=True)
    avertissement_cle = fields.Char(readonly=True)
    cles_ids = fields.Many2many("bf.nfc.sdm.key", string="Clés des pastilles signées",
                                compute="_compute_cles")
    type_count = fields.Integer(compute="_compute_cles")

    @api.model
    def default_get(self, champs):
        _exiger_la_gestion(self.env)
        valeurs = super().default_get(champs)
        icp = self.env["ir.config_parameter"].sudo()
        origine = chiffrement.origine_de_la_cle(self.env)
        valeurs.update({
            "fenetre_doublon": int(icp.get_param(PARAM_FENETRE, FENETRE_DEFAUT)),
            "differe_max_heures": int(icp.get_param(PARAM_DIFFERE_HEURES, DIFFERE_DEFAUT_HEURES)),
            "origine_cle": origine,
            "avertissement_cle": _(
                "La clé qui chiffre les clés des puces est rangée dans la base : le chiffrement "
                "protège contre la lecture à l'écran et par l'API, pas contre une copie de la "
                "base. Posez BF_NFC_FERNET_KEY dans l'environnement du serveur pour la sortir."
            ) if origine in ("base", "absente") else False,
        })
        if self.env.user.has_group("base.group_system"):
            valeurs["schemas_appariement"] = icp.get_param(PARAM_SCHEMAS) or SCHEMAS_DEFAUT
        return valeurs

    def _compute_cles(self):
        cles = self.env["bf.nfc.sdm.key"].search([])
        types = self.env["bf.nfc.target.type"].search_count([])
        for page in self:
            page.cles_ids = cles
            page.type_count = types

    def action_enregistrer(self):
        self.ensure_one()
        _exiger_la_gestion(self.env)
        if self.fenetre_doublon < 0 or self.differe_max_heures < 0:
            raise UserError(_("Les durées ne peuvent pas être négatives."))
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(PARAM_FENETRE, str(self.fenetre_doublon))
        icp.set_param(PARAM_DIFFERE_HEURES, str(self.differe_max_heures))
        if self.env.user.has_group("base.group_system"):
            icp.set_param(PARAM_SCHEMAS, (self.schemas_appariement or "").strip() or SCHEMAS_DEFAUT)
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "type": "success", "message": _("Réglages enregistrés."),
            "next": {"type": "ir.actions.act_window_close"}}}

    def action_poser_cles(self):
        _exiger_la_gestion(self.env)
        return {
            "type": "ir.actions.act_window",
            "name": _("Poser les clés des pastilles signées"),
            "res_model": "bf.nfc.sdm.key.wizard",
            "view_mode": "form",
            "target": "new",
        }

    def action_types_de_fiche(self):
        return self.env["ir.actions.act_window"]._for_xml_id("bf_nfc.action_bf_nfc_target_type")


class BfNfcSdmKeyWizard(models.TransientModel):
    """Saisit une paire de clés, la chiffre à l'écriture, et ne relit jamais le clair.

    🔴 Un assistant est un enregistrement en base, le temps de sa vie. Les deux
    champs saisis ne sont donc pas stockés : leur inverse chiffre à la volée, et
    seule la forme chiffrée touche la table. Le bouton pose la paire et supprime
    l'assistant.
    """
    _name = "bf.nfc.sdm.key.wizard"
    _description = "Pose des clés des pastilles signées"

    company_id = fields.Many2one("res.company", string="Société", required=True,
                                 default=lambda self: self.env.company)
    # 🔴 Les deux champs saisis ne sont PAS stockés : leur inverse chiffre tout de
    # suite, et seule l'empreinte chiffrée se range dans la table de l'assistant.
    # Avant, le client web enregistrait le formulaire (une transaction) puis
    # appelait le bouton (une seconde) : les 32 caractères hexadécimaux
    # séjournaient en clair entre les deux, et y restaient pour de bon dès que la
    # seconde transaction échouait, jusqu'au ménage des transients. Une sauvegarde
    # prise dans cette fenêtre emportait la clé des puces.
    meta_key = fields.Char(string="Clé de métadonnées (SDMMetaRead)", store=False,
                           compute="_compute_saisie", inverse="_inverse_meta_key")
    file_key = fields.Char(string="Clé de fichier (SDMFileRead)", store=False,
                           compute="_compute_saisie", inverse="_inverse_file_key")
    meta_key_enc = fields.Char(groups="base.group_system")
    file_key_enc = fields.Char(groups="base.group_system")
    remplace = fields.Boolean(compute="_compute_remplace")

    def _compute_saisie(self):
        """Rien ne se relit : un champ de clé est un champ d'écriture."""
        for assistant in self:
            assistant.meta_key = False
            assistant.file_key = False

    def _inverse_meta_key(self):
        for assistant in self:
            assistant.sudo().meta_key_enc = self.env["bf.nfc.sdm.key"]._chiffrer_une_cle(
                assistant.meta_key, _("de métadonnées"))

    def _inverse_file_key(self):
        for assistant in self:
            assistant.sudo().file_key_enc = self.env["bf.nfc.sdm.key"]._chiffrer_une_cle(
                assistant.file_key, _("de fichier"))

    @api.depends("company_id")
    def _compute_remplace(self):
        Cle = self.env["bf.nfc.sdm.key"].sudo()
        for assistant in self:
            assistant.remplace = bool(Cle.search_count([("company_id", "=", assistant.company_id.id)]))

    def action_poser(self):
        self.ensure_one()
        _exiger_la_gestion(self.env)
        societe = self.company_id
        moi = self.sudo()
        if not (moi.meta_key_enc and moi.file_key_enc):
            raise UserError(_("Saisissez les deux clés."))
        self.env["bf.nfc.sdm.key"]._poser_chiffre(societe, moi.meta_key_enc, moi.file_key_enc)
        self.unlink()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "type": "success",
            "message": _("Clés posées pour %s. Elles ne se réafficheront pas.", societe.name),
            "next": {"type": "ir.actions.act_window_close"}}}
