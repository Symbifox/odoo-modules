import json
import logging
import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Ceiling on the personality text composed into the prompt. The bridge cuts at
# the same length; refusing here is what keeps that cut from being silent.
PERSONALITY_MAX_CHARS = 2000

# Valeur d'affichage d'une clé déjà enregistrée. `default_get` et le `compute`
# la poussent dans le champ pour ne JAMAIS renvoyer le secret au navigateur ;
# l'`inverse` doit donc l'ignorer, sinon un simple « Enregistrer » dans
# Paramètres la ré-encrypterait par-dessus la vraie clé.
MASQUE_CLE = "••••••••"

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception
    _logger.warning(
        "cryptography package not installed. "
        "API key encryption will not be available for bf_claude_chat."
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    claude_enabled = fields.Boolean(
        string="Enable Claude AI",
        config_parameter="bf_claude_chat.enabled",
        default=True,
    )
    claude_streaming = fields.Boolean(
        string="Stream responses (live tokens)",
        config_parameter="bf_claude_chat.streaming",
        default=True,
        help="Stream the answer token by token with tool and thinking activity "
             "(console-like). When off, the chat falls back to a single "
             "buffered reply.",
    )
    # Éteint d'office, locataire par locataire.
    claude_closure = fields.Boolean(
        string="Conversations aim to close",
        config_parameter="bf_claude_chat.closure_enabled",
        default=False,
        help="At the end of each turn Gen says where the conversation stands, "
             "and offers to archive it once everything is done. A nightly "
             "pass follows up on conversations left without news for two "
             "days. Nothing is ever archived without a click.",
    )
    claude_auto_brief = fields.Boolean(
        string="Brief me when I open a record",
        config_parameter="bf_claude_chat.auto_brief",
        default=True,
        help="The first time the panel opens on a record with no conversation "
             "yet, Claude reads the record, its chatter and its latest activity "
             "and opens with a short situation report plus next actions, "
             "without waiting for a question.",
    )
    claude_model = fields.Selection(
        [
            ("sonnet", "Sonnet (fast, recommended)"),
            ("opus", "Opus (most capable)"),
            ("haiku", "Haiku (fastest, lightweight)"),
        ],
        string="Claude Model",
        config_parameter="bf_claude_chat.model",
        default="sonnet",
    )
    claude_max_turns = fields.Integer(
        string="Max Turns per Message",
        config_parameter="bf_claude_chat.max_turns",
        default=45,
        help="Maximum number of tool-use cycles Claude can perform per message. "
             "Higher values allow more complex queries but take longer.",
    )
    claude_api_key = fields.Char(
        string="Anthropic API Key",
        compute="_compute_api_key",
        inverse="_inverse_api_key",
        readonly=False,
        help="Optional. If set, this tenant uses its own API key (pay-per-use) "
             "instead of the shared Max plan. A key already stored shows as "
             "dots: the key itself never travels back to the browser, and "
             "saving the dots changes nothing. Use « Remove the key » below to "
             "go back to the shared Max plan.",
    )
    claude_tenant = fields.Char(
        string="Tenant Slug",
        config_parameter="bf_claude_chat.tenant",
        default="pme",
        help="Tenant identifier sent to the bridge to select the correct "
             "MCP tools and system prompt (e.g. 'bf' or 'pme').",
    )
    claude_bridge_socket = fields.Char(
        string="Bridge Socket Path",
        # Le paramètre appartient à bf_ai_bridge, qui porte le transport : ce
        # réglage est celui que TOUS les modules appelants lisent désormais.
        config_parameter="bf_ai_bridge.socket",
        default="/run/claude-bridge/bridge.sock",
        help="Unix socket path for the Claude bridge service. Shared by every "
             "Blue Fox module that talks to the bridge (meetings, invoice OCR, "
             "contact enrichment).",
    )
    claude_timeout = fields.Integer(
        string="Response Timeout (seconds)",
        config_parameter="bf_claude_chat.timeout",
        default=660,
        help="Maximum time to wait for a Claude response.",
    )
    # A Char, not a Text: res.config.settings only binds config_parameter to
    # char/boolean/integer/float/selection/many2one/datetime fields. The view
    # renders it as a textarea (widget="text"); a Char has no length limit.
    claude_personality = fields.Char(
        string="Gen's personality",
        config_parameter="bf_claude_chat.personality",
        help="How Gen speaks on this instance: the name it goes by, tu or vous, "
             "the default language, how long its answers run, how it presents "
             "itself. Composed into every conversation, before the steering "
             "instructions. It shapes the tone, never what Gen may do: tool "
             "access and safety rules are set on the bridge, not here. "
             "2,000 characters at most.",
    )

    # Qui voit quels compteurs. Vide = tous les
    # usagers internes, comme avant.
    claude_meter_plan_group_id = fields.Many2one(
        "res.groups", string="Plan counters visible to",
        config_parameter="bf_claude_chat.meter_plan_group_id",
        help="Who sees the Claude plan windows (session, week). Empty: every "
             "internal user.",
    )
    claude_meter_usage_group_id = fields.Many2one(
        "res.groups", string="Conversation tokens visible to",
        config_parameter="bf_claude_chat.meter_usage_group_id",
        help="Who sees the tokens a conversation used. Empty: every internal user.",
    )
    claude_meter_cost_group_id = fields.Many2one(
        "res.groups", string="Cost visible to",
        config_parameter="bf_claude_chat.meter_cost_group_id",
        help="Who sees what a conversation would cost at API rates. Empty: "
             "every internal user.",
    )

    # Le forfait Max qui paie les tours de cette
    # instance. La bascule vit dans le pont : ces deux champs la lisent et
    # l'écrivent par lui, ils ne la copient pas en base.
    claude_plan_account_id = fields.Many2one(
        "claude.account", string="Plan paying for Gen",
        compute="_compute_plan_switch", inverse="_inverse_plan_switch",
        help="Empty: this instance's own plan. Another account: Gen turns are "
             "paid by that Max plan, for the users below (all if empty). Only "
             "the accounts the bridge allows for this instance are accepted.",
    )
    claude_plan_user_ids = fields.Many2many(
        "res.users", string="Switched users",
        compute="_compute_plan_switch", inverse="_inverse_plan_switch",
        domain=[("share", "=", False)],
        help="Users whose Gen turns go to the plan above. Empty: everyone.",
    )
    claude_plan_switch_error = fields.Char(compute="_compute_plan_switch")
    claude_plan_switch_available = fields.Boolean(compute="_compute_plan_switch")
    # Ce que le pont disait À L'OUVERTURE du formulaire.
    # Les réglages renvoient tous leurs champs à chaque enregistrement ; sans
    # cet instantané, un formulaire resté ouvert pendant une bascule faite
    # ailleurs (skill, autre admin) la retirait en enregistrant autre chose.
    # Calculé avec les autres (une seule lecture du pont, et seulement quand le
    # formulaire le demande).
    #
    # 🔴 Il porte le MÊME `inverse` que les deux autres, et ce n'est pas un
    # détail : Odoo groupe les champs par méthode d'`inverse` et n'alimente le
    # cache que des champs du groupe COURANT avant d'appeler la méthode
    # (`models.create`). Avec un `inverse` à lui, cet instantané valait donc
    # `False` dans `_inverse_plan_switch` — sur le chemin `create`, qui est le
    # SEUL que la page Paramètres emprunte. Le garde-fou ne servait à rien, et
    # un formulaire périmé pouvait revenir sur une bascule faite ailleurs.
    # Mesuré.
    claude_plan_switch_snapshot = fields.Char(
        compute="_compute_plan_switch", inverse="_inverse_plan_switch")

    @api.model
    def default_get(self, fields_list):
        """Alimente la façade de la clé API, que le formulaire ne calcule jamais.

        🔴 La page Paramètres est bâtie par `onchange`, et la couche web sème à
        `False` tout champ de la vue que `default_get` ne rend pas
        (`addons/web/models/models.py`). Le `compute` d'un champ de façade ne
        tourne donc JAMAIS à l'affichage : le formulaire portait une clé vide
        quoi que porte la base, et l'enregistrement, par l'`inverse`, écrivait
        ce vide sur `bf_claude_chat.api_key_encrypted` ET sur
        `bf_claude_chat.api_key`. Un seul enregistrement des Paramètres, depuis
        l'onglet de n'importe quel autre module, suffisait à retirer la clé
        sans un mot (même cause que le défaut voisin de la rétention des courriels).

        Les champs voisins portent `config_parameter` : `default_get` les rend
        tout seul, ils n'ont jamais eu le défaut.

        ⚠️ La bascule de forfait n'est PAS alimentée ici, à dessein, et elle n'a
        pas le défaut. Sa lecture passe par le pont (appel réseau) et
        `_add_missing_default_values` appelle `default_get` à CHAQUE
        `res.config.settings.create()` de n'importe quel module : l'y mettre
        ferait sortir sur le réseau à chaque enregistrement de n'importe quel
        onglet. Elle n'en a pas besoin, parce que son `@api.depends("company_id")`
        est une dépendance RÉELLE : `company_id` porte un défaut, il entre donc
        dans les valeurs « changées » du premier `onchange`, et le
        `record.modified(todo)` de la couche web invalide ce qui en dépend.
        Mesuré : sur le chemin du formulaire, ces
        champs sont calculés (un pont absent rend son erreur, ce qu'un champ
        semé à `False` ne pourrait pas faire). Et un `create({})` ne les porte
        pas, donc leur `inverse` ne tourne pas.

        ⚠️ Ça ne suffit PAS à les mettre hors de danger : un pont muet à
        l'ouverture rend un instantané vide, et c'est `_inverse_plan_switch`
        qui refuse alors d'écrire. Voir sa docstring.
        """
        res = super().default_get(fields_list)
        if "claude_api_key" in fields_list:
            res["claude_api_key"] = MASQUE_CLE if self._claude_cle_posee() else False
        return res

    @api.model
    def _claude_cle_posee(self):
        """Vrai si une clé UTILISABLE est en base. Source unique du masque.

        🔴 Deux choses qu'une simple présence ne dit pas, trouvées par la
        mesure :

        1. **Déchiffrable, pas seulement non vide.** Après une rotation de clé
           Fernet, ou si la clé hors base a disparu, le paramètre chiffré ne
           vaut plus rien : afficher un masque affirmerait qu'une clé est posée
           pendant que Gen repart sur le forfait partagé. On déchiffre donc,
           pour que l'écran dise vrai — c'est le coût que cet écran payait déjà
           avant, le `compute` déchiffrant à chaque ouverture.
        2. **Le paramètre en clair hérité compte aussi.** `bf_claude_chat.api_key`
           n'est plus balayé à chaque enregistrement (c'est le prix de la garde
           qui empêche l'effacement), donc un secret en clair peut rester en
           base. L'écran doit le MONTRER, sinon le bouton qui le retire reste
           hors de portée de la seule personne qui pourrait l'utiliser.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        chiffre = ICP.get_param("bf_claude_chat.api_key_encrypted", "")
        if chiffre and self._decrypt_api_key(self.env, chiffre):
            return True
        return bool(ICP.get_param("bf_claude_chat.api_key", ""))

    # ⚠️ Une dépendance réelle : sans elle, le chargement du formulaire
    # (`onchange` d'un enregistrement neuf) ne calcule pas ces champs et le
    # bloc restait masqué.
    @api.depends("company_id")
    def _compute_plan_switch(self):
        etat = self.env["claude.account"]._plan_switch_read()
        for rec in self:
            rec.claude_plan_account_id = etat["account"]
            rec.claude_plan_user_ids = etat["users"]
            rec.claude_plan_switch_error = etat["error"]
            rec.claude_plan_switch_available = etat["available"]
            rec.claude_plan_switch_snapshot = "" if etat["error"] else json.dumps(
                self.env["claude.account"]._plan_switch_key(etat["account"], etat["users"]))

    def _inverse_plan_switch(self):
        """Ne touche à la bascule que si le formulaire l'avait LUE.

        🔴 Un instantané vide ne veut pas dire « bascule vide » : il veut dire
        que le formulaire n'a rien lu. Deux chemins y mènent, et le second a
        été mesuré :

        1. le champ n'est pas dans les valeurs reçues, donc il vaut `False`
           dans l'`inverse` (Odoo n'alimente le cache que du groupe d'`inverse`
           courant) ;
        2. le pont était MUET à l'ouverture des Paramètres — le calcul a bien
           tourné, mais il a rendu son erreur, donc un instantané vide et un
           compte vide — et il répond de nouveau à l'ENREGISTREMENT. Sans cette
           garde, `_plan_switch_write` relit alors une bascule en place, voit
           qu'on lui demande le vide, et la RETIRE : un formulaire qui ne l'a
           jamais vue l'efface en enregistrant un réglage d'un autre onglet.

        ⚠️ La porte est `_plan_switch_lu` et non « l'instantané est vide » :
        un instantané non vide mais ILLISIBLE la franchissait et retirait la
        bascule tout pareil. Les deux cas disent la même chose — le formulaire
        n'a rien lu — et une seule lecture les range.

        ⚠️ Et elle ne se referme QUE si rien n'est demandé non plus. Un
        changement DÉLIBÉRÉ doit passer, et se faire refuser bruyamment quand
        le pont n'en veut pas : deux essais de ce module l'exigent, et une
        garde qui s'arrêtait au seul instantané les faisait tomber en étouffant
        le refus. « Rien lu ET rien demandé » ne touche à rien ; « rien lu mais
        quelque chose de demandé » va au pont, qui tranche.

        On ne fait RIEN plutôt que de lever quand rien n'est demandé : la page
        Paramètres de tous les modules doit rester enregistrable.
        """
        Compte = self.env["claude.account"]
        for rec in self:
            lu = Compte._plan_switch_lu(rec.claude_plan_switch_snapshot)
            demande = bool(rec.claude_plan_account_id or rec.claude_plan_user_ids)
            if lu is None and not demande:
                continue
            if lu is not None and Compte._plan_switch_unchanged(
                    rec.claude_plan_switch_snapshot,
                    rec.claude_plan_account_id, rec.claude_plan_user_ids):
                continue
            Compte._plan_switch_write(rec.claude_plan_account_id, rec.claude_plan_user_ids)

    # A constraint rather than a set_values() override: the settings form saves
    # the record before it runs set_values(), so the refusal lands on the save
    # itself, and set_values() on a full instance reaches every module's
    # override, some of which call out to the network (onlyoffice_odoo checks
    # its document server), which no test should trigger.
    @api.constrains("claude_personality")
    def _check_personality_size(self):
        for rec in self:
            size = len((rec.claude_personality or "").strip())
            if size > PERSONALITY_MAX_CHARS:
                raise ValidationError(_(
                    "Gen's personality is limited to %(max)s characters; this "
                    "text has %(size)s. Shorten it: the bridge cuts at the same "
                    "length, and a cut would be silent.",
                    max=PERSONALITY_MAX_CHARS, size=size))
    # --- Encryption helpers ---

    @staticmethod
    def _get_encryption_key():
        """Get Fernet key from env var or odoo.conf. Never from DB."""
        if not Fernet:
            return None
        # Priority 1: environment variable
        key = os.environ.get("BF_CLAUDE_CHAT_FERNET_KEY")
        if key:
            return key.encode()
        # Priority 2: odoo.conf
        try:
            from odoo.tools import config
            key = config.get("bf_claude_chat_fernet_key")
            if key:
                return key.encode()
        except Exception:
            pass
        _logger.warning(
            "bf_claude_chat: no Fernet key configured. "
            "Set BF_CLAUDE_CHAT_FERNET_KEY env var or "
            "bf_claude_chat_fernet_key in odoo.conf."
        )
        return None

    @staticmethod
    def _encrypt_api_key(value):
        if not value:
            return ""
        if not Fernet:
            raise UserError(
                "Le paquet 'cryptography' est requis pour stocker "
                "la cle API de maniere securisee."
            )
        key = ResConfigSettings._get_encryption_key()
        if not key:
            raise UserError(
                "Cle de chiffrement manquante. Configurez "
                "BF_CLAUDE_CHAT_FERNET_KEY dans l'environnement "
                "ou bf_claude_chat_fernet_key dans odoo.conf."
            )
        return Fernet(key).encrypt(value.encode()).decode()

    @staticmethod
    def _decrypt_api_key(env, encrypted):
        if not encrypted:
            return ""
        key = ResConfigSettings._get_encryption_key()
        if not key:
            _logger.warning("bf_claude_chat: cannot decrypt API key - no Fernet key")
            return ""
        try:
            return Fernet(key).decrypt(encrypted.encode()).decode()
        except InvalidToken:
            _logger.warning(
                "bf_claude_chat: API key decryption failed "
                "(key rotation or legacy plaintext value)"
            )
            return ""
        except Exception:
            _logger.exception("bf_claude_chat: API key decryption error")
            return ""

    # --- Computed field ---

    def _compute_api_key(self):
        """Ne rend JAMAIS la clé au navigateur : un masque, ou rien.

        ⚠️ Même source que `default_get`, à dessein : l'un alimente le
        formulaire, l'autre les lectures par l'ORM, et deux lectures séparées
        finiraient par dire deux choses.
        """
        posee = self._claude_cle_posee()
        for rec in self:
            rec.claude_api_key = MASQUE_CLE if posee else False

    def _inverse_api_key(self):
        """Chiffre et range la clé saisie. Ignore le masque et le vide.

        ⚠️ Le vide vaut « inchangé » et non « retire la clé » : il faut qu'un
        enregistrement venu d'un autre onglet, ou un
        `res.config.settings.create()` qui ne porte pas ce champ, ne puisse
        rien effacer. Pour retirer la clé, il y a le bouton « Remove the key »,
        qui le dit.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        for rec in self:
            saisi = (rec.claude_api_key or "").strip()
            if not saisi or saisi == MASQUE_CLE:
                continue
            ICP.set_param(
                "bf_claude_chat.api_key_encrypted", self._encrypt_api_key(saisi)
            )
            # Clear any legacy plaintext key
            ICP.set_param("bf_claude_chat.api_key", "")
            _logger.warning(
                "bf_claude_chat : clé API posée par l'écran des Paramètres "
                "(par uid %s)", self.env.uid,
            )

    def action_claude_remove_api_key(self):
        """Retire la clé de ce locataire : Gen repart sur le forfait partagé.

        Un geste explicite, parce que l'`inverse` ne peut plus le faire : vider
        le champ y vaut « inchangé », faute de quoi tout enregistrement des
        Paramètres pourrait retirer la clé en silence.
        """
        self.ensure_one()
        self.check_access("write")
        ICP = self.env["ir.config_parameter"].sudo()
        # ⚠️ On regarde la PRÉSENCE, pas la lisibilité : `_claude_cle_posee()`
        # déchiffre, et il rend donc faux devant un chiffré illisible. Le
        # bouton répondait alors « aucune clé à retirer » en laissant ce
        # chiffré en base, hors de portée de l'écran — et si la clé Fernet
        # revenait, l'ancienne clé API reprenait vie.
        if not ICP.get_param("bf_claude_chat.api_key_encrypted", "") \
                and not ICP.get_param("bf_claude_chat.api_key", ""):
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "info", "sticky": False,
                    "title": _("No key to remove"),
                    "message": _("This instance already runs on the shared "
                                 "Max plan."),
                },
            }
        ICP.set_param("bf_claude_chat.api_key_encrypted", "")
        ICP.set_param("bf_claude_chat.api_key", "")
        _logger.warning(
            "bf_claude_chat : clé API RETIRÉE par l'écran des Paramètres "
            "(par uid %s) — retour au forfait partagé", self.env.uid,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success", "sticky": False,
                "title": _("Key removed"),
                "message": _("Gen's turns go back to the shared Max plan. "
                             "Reopen Settings to see the field empty."),
            },
        }

