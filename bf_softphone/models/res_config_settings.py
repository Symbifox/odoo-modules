from odoo import _, api, fields, models

# Une seule définition de la lecture tolérante pour tout le module. La recopier
# ici la ferait diverger du contrôleur au premier correctif — et une définition
# en double se corrige toujours du mauvais côté.
from ..controllers.pbx_api import _truthy

# Masque rendu à la place d'un secret déjà enregistré. Réécrire ce masque ne
# doit JAMAIS écraser le secret (même convention que sip_password sur res.users).
_MASK = "••••••••"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Hôte du PBX — paramétrable par instance : deux locataires qui partagent
    # un PBX partagent aussi son compte de trunk, ce qui se décide avant.
    bf_softphone_pbx_host = fields.Char(
        string="PBX host",
        config_parameter="bf_softphone.pbx_host",
        default="pbx.example.com",
        help="Hostname of the PBX serving the SIP WebSocket (wss://<host>/ws).",
    )
    # Clé TURN Cloudflare — utilisée côté serveur pour forger des creds ICE
    # à TTL court. Ce sont des secrets ; ils ne quittent jamais le serveur
    # (seuls les creds éphémères générés partent vers le navigateur).
    bf_softphone_turn_key_id = fields.Char(
        string="Cloudflare TURN key ID",
        config_parameter="bf_softphone.turn_key_id",
    )
    bf_softphone_turn_key_token = fields.Char(
        string="Cloudflare TURN key token",
        config_parameter="bf_softphone.turn_key_token",
    )

    # ------------------------------------------------------------------
    # Lien AMI — composition d'appel pour les clients sans pile SIP
    # ------------------------------------------------------------------
    bf_softphone_ami_host = fields.Char(
        string="AMI host",
        config_parameter="bf_softphone.ami_host",
        help="Address Odoo uses to reach the Asterisk Manager Interface. "
             "The port has no reason to be exposed beyond the host.",
    )
    bf_softphone_ami_port = fields.Integer(
        string="AMI port",
        config_parameter="bf_softphone.ami_port",
        default=5038,
    )
    bf_softphone_ami_user = fields.Char(
        string="AMI user",
        config_parameter="bf_softphone.ami_user",
    )
    # Saisie write-only : chiffré au repos comme le secret SIP (même clé Fernet).
    # Pas de config_parameter ici — le stockage passe par set_values().
    bf_softphone_ami_password = fields.Char(
        string="AMI password",
        help="Encrypted at rest. Leave the mask to keep the secret unchanged.",
    )
    bf_softphone_trunk = fields.Char(
        string="Outbound trunk",
        config_parameter="bf_softphone.trunk",
        default="outbound-trunk",
        help="Name of the provider's PJSIP endpoint, as written in the "
             "dial plan (Dial(PJSIP/${EXTEN}@<trunk>)).",
    )
    bf_softphone_outbound_cid = fields.Char(
        string="Outbound caller ID",
        config_parameter="bf_softphone.outbound_cid",
        help="DID shown when the PBX rings the user THROUGH THE TRUNK. It "
             "must belong to the provider account: a number that is not "
             "ours gets replaced, or the call is refused.",
    )
    # ------------------------------------------------------------------
    # Réveil par push — le PBX prévient Odoo avant de composer
    # ------------------------------------------------------------------
    bf_softphone_wake_enabled = fields.Boolean(
        string="Push wake-up",
        config_parameter="bf_softphone.wake_enabled",
        default=True,
        help="Rings the mobile app even when it is closed. Separate from "
             "the text message notifications switch: turning off texts "
             "must not turn off the phone.",
    )
    bf_softphone_pbx_wake_token = fields.Char(
        string="PBX wake-up token",
        config_parameter="bf_softphone.pbx_wake_token",
        help="Secret shared with the dial plan (context [wake-sip]). The "
             "PBX presents it to request waking up an extension; with no "
             "token configured, the route refuses everything.",
    )
    bf_softphone_originate_context = fields.Char(
        string="Dialing context",
        config_parameter="bf_softphone.originate_context",
        default="from-clicktocall",
        help="Dial plan context run once the user's device picks up. It "
             "must force the outbound caller ID to a DID on the account, "
             "otherwise the 2nd leg inherits the caller ID of the 1st.",
    )

    # ------------------------------------------------------------------
    # ⚠️ Le réveil par push est écrit et relu À LA MAIN, et il le faut.
    #
    # Odoo gère mal les cases dont le défaut est VRAI. Décocher appelle
    # `set_param(clé, False)`, et `ir.config_parameter.set_param` fait
    # `unlink()` sur toute valeur fausse : la rangée DISPARAÎT. Le lecteur
    # retombe alors sur son propre défaut — vrai — la case revient cochée au
    # rechargement, et l'interrupteur ne peut jamais dire « non ».
    #
    # Le symétrique a déjà coûté cher : une sauvegarde de la page des réglages
    # a écrit la chaîne « True », que le contrôleur comparait à « 1 ». On écrit
    # donc « 1 » ou « 0 » explicitement, et on relit tolérant des deux côtés.
    # ------------------------------------------------------------------
    _WAKE_KEY = "bf_softphone.wake_enabled"

    @api.model
    def get_values(self):
        res = super().get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        res["bf_softphone_ami_password"] = (
            _MASK if ICP.get_param("bf_softphone.ami_password_enc") else False)
        # ⚠️ Après super(), ce champ vaut bool("0") — donc VRAI. Il faut le
        # relire, sinon la case décochée se rallume toute seule à l'affichage.
        res["bf_softphone_wake_enabled"] = _truthy(
            ICP.get_param(self._WAKE_KEY), defaut=True)
        return res

    def set_values(self):
        super().set_values()
        for record in self:
            ICP = record.env["ir.config_parameter"].sudo()
            # « 0 » plutôt que l'absence : une rangée qui existe est la seule
            # façon pour ce réglage de dire non.
            ICP.set_param(self._WAKE_KEY,
                          "1" if record.bf_softphone_wake_enabled else "0")
            value = record.bf_softphone_ami_password
            if not value or value == _MASK:
                continue
            ICP.set_param(
                "bf_softphone.ami_password_enc",
                record.env["res.users"]._sip_encrypt(value))

    def action_softphone_test_ami(self):
        """S'authentifie auprès du PBX et repart. Aucun appel n'est composé."""
        self.ensure_one()
        self.env["bf.softphone.ami"].ping()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("The PBX responds and accepts the authentication."),
                "sticky": False,
            },
        }
