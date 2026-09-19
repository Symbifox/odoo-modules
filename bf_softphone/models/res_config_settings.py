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
        string="Hôte du PBX",
        config_parameter="bf_softphone.pbx_host",
        default="pbx.example.com",
        help="Nom d'hôte du PBX servant le WebSocket SIP (wss://<hôte>/ws).",
    )
    # Clé TURN Cloudflare — utilisée côté serveur pour forger des creds ICE
    # à TTL court. Ce sont des secrets ; ils ne quittent jamais le serveur
    # (seuls les creds éphémères générés partent vers le navigateur).
    bf_softphone_turn_key_id = fields.Char(
        string="ID de clé TURN Cloudflare",
        config_parameter="bf_softphone.turn_key_id",
    )
    bf_softphone_turn_key_token = fields.Char(
        string="Jeton de clé TURN Cloudflare",
        config_parameter="bf_softphone.turn_key_token",
    )

    # ------------------------------------------------------------------
    # Lien AMI — composition d'appel pour les clients sans pile SIP
    # ------------------------------------------------------------------
    bf_softphone_ami_host = fields.Char(
        string="Hôte AMI",
        config_parameter="bf_softphone.ami_host",
        help="Adresse par laquelle Odoo joint l'interface manager d'Asterisk. "
             "Le port n'a aucune raison d'être exposé au-delà de l'hôte.",
    )
    bf_softphone_ami_port = fields.Integer(
        string="Port AMI",
        config_parameter="bf_softphone.ami_port",
        default=5038,
    )
    bf_softphone_ami_user = fields.Char(
        string="Utilisateur AMI",
        config_parameter="bf_softphone.ami_user",
    )
    # Saisie write-only : chiffré au repos comme le secret SIP (même clé Fernet).
    # Pas de config_parameter ici — le stockage passe par set_values().
    bf_softphone_ami_password = fields.Char(
        string="Mot de passe AMI",
        help="Chiffré au repos. Laisser le masque pour ne pas changer le secret.",
    )
    bf_softphone_trunk = fields.Char(
        string="Trunk sortant",
        config_parameter="bf_softphone.trunk",
        default="trunk-sortant",
        help="Nom du endpoint PJSIP du fournisseur, tel qu'écrit dans le plan "
             "de numérotation (Dial(PJSIP/${EXTEN}@<trunk>)).",
    )
    bf_softphone_outbound_cid = fields.Char(
        string="Afficheur sortant",
        config_parameter="bf_softphone.outbound_cid",
        help="DID présenté quand le PBX fait sonner l'utilisateur PAR LE TRUNK. "
             "Il doit appartenir au compte du fournisseur : un numéro qui n'est "
             "pas à nous est remplacé — ou l'appel est refusé.",
    )
    # ------------------------------------------------------------------
    # Réveil par push — le PBX prévient Odoo avant de composer
    # ------------------------------------------------------------------
    bf_softphone_wake_enabled = fields.Boolean(
        string="Réveil par push",
        config_parameter="bf_softphone.wake_enabled",
        default=True,
        help="Fait sonner l'application mobile même fermée. Distinct de "
             "l'interrupteur des notifications de textos : couper les textos "
             "ne doit pas couper le téléphone.",
    )
    bf_softphone_pbx_wake_token = fields.Char(
        string="Jeton de réveil du PBX",
        config_parameter="bf_softphone.pbx_wake_token",
        help="Secret partagé avec le plan de numérotation (contexte [wake-sip]). "
             "Le PBX le présente pour demander le réveil d'un poste ; sans jeton "
             "configuré, la route refuse tout.",
    )
    bf_softphone_originate_context = fields.Char(
        string="Contexte de composition",
        config_parameter="bf_softphone.originate_context",
        default="from-clicktocall",
        help="Contexte du plan de numérotation exécuté une fois l'appareil de "
             "l'utilisateur décroché. Il doit forcer l'afficheur sortant sur un "
             "DID du compte, sans quoi la 2e jambe hérite de celui de la 1re.",
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
                "message": _("Le PBX répond et accepte l'authentification."),
                "sticky": False,
            },
        }
