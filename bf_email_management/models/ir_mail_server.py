"""Un serveur sortant qui emprunte le jeton d'un compte.

Odoo sait déjà envoyer en XOAUTH2 : `microsoft_outlook` ajoute la valeur
`outlook` à `smtp_authentication` et surcharge `_smtp_login`. 🔴 Mais ses
jetons vivent sur la fiche du SERVEUR, en `base.group_system` : un employé ne
peut ni les poser ni les lire, et il n'a même pas le droit de créer un
`ir.mail_server` (`perm_create = f` pour le groupe *User*, vérifié).

Ici, le serveur ne porte aucun jeton : il pointe vers un `bf.email.account`
et lui demande le sien au moment de la connexion. Le secret reste donc sur la
fiche du compte, sous la règle d'enregistrement du propriétaire.

🔴 Et la garde qui compte n'est pas celle du jeton, c'est `from_filter`. Odoo
choisit son serveur sortant en comparant l'expéditeur aux `from_filter` : un
serveur créé pour un employé avec un filtre vide deviendrait candidat pour
**n'importe quelle** adresse du locataire. L'assistant écrit donc ce champ
lui-même, à l'adresse du compte, et jamais depuis un formulaire.
"""

import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    smtp_authentication = fields.Selection(
        selection_add=[("bf_compte", "Jeton du compte courriel Symbifox")],
        ondelete={"bf_compte": "set default"},
    )
    bf_account_id = fields.Many2one(
        comodel_name="bf.email.account",
        string="Compte courriel",
        ondelete="cascade",
        help="Le serveur emprunte le jeton OAuth de ce compte au moment de "
             "l'envoi. Aucun secret n'est stocké ici.",
    )

    @api.constrains("smtp_authentication", "bf_account_id", "from_filter")
    def _check_serveur_de_compte(self):
        for serveur in self.filtered(lambda s: s.smtp_authentication == "bf_compte"):
            if not serveur.bf_account_id:
                raise ValidationError(_(
                    "Le serveur « %s » s'authentifie par un compte courriel "
                    "mais n'en désigne aucun.", serveur.name or ""))
            if not (serveur.from_filter or "").strip():
                raise ValidationError(_(
                    "Le serveur « %s » n'a pas de filtre d'expéditeur. Sans "
                    "lui, Odoo pourrait l'employer pour n'importe quelle "
                    "adresse du locataire.", serveur.name or ""))

    def _compute_smtp_authentication_info(self):
        miens = self.filtered(lambda s: s.smtp_authentication == "bf_compte")
        for serveur in miens:
            serveur.smtp_authentication_info = _(
                "Ce serveur emprunte le jeton OAuth du compte courriel "
                "« %s ». Il ne sert que l'adresse de ce compte.",
                serveur.bf_account_id.name or "")
        super(IrMailServer, self - miens)._compute_smtp_authentication_info()

    def _smtp_login(self, connection, smtp_user, smtp_password):
        """Le secret est EMPRUNTÉ au compte, jamais recopié ici.

        🔴 La première version écrivait le mot de passe IMAP du compte dans
        `smtp_pass`. Ce champ porte `groups='base.group_system'`, donc il n'est
        pas lisible d'un employé, mais il l'est de **tout administrateur des
        réglages** : un secret qui n'était visible que de son propriétaire
        (`ir.rule` sur `bf.email.account`, sans dérogation admin) se retrouvait
        lisible à l'écran par quelqu'un d'autre, et en clair une deuxième fois
        en base. Le même emprunt sert donc les deux modes.
        """
        if len(self) == 1 and self.smtp_authentication == "bf_compte":
            compte = self.bf_account_id.sudo()
            if compte.auth_mode == "xoauth2":
                sasl = self.env["bf.email.oauth"].chaine_xoauth2(
                    compte.login, compte._jeton_acces())
                connection.ehlo()
                connection.docmd("AUTH", f"XOAUTH2 {sasl}")
            else:
                super()._smtp_login(connection, compte.login, compte.password or "")
            return
        super()._smtp_login(connection, smtp_user, smtp_password)

    # ------------------------------------------------------------------
    # Fabrication par l'assistant
    # ------------------------------------------------------------------
    @api.model
    def _bf_assurer_pour_compte(self, compte, hote, port, securite):
        """Le serveur sortant d'un compte, créé s'il manque.

        Appelé en sudo depuis l'assistant : un employé n'a pas le droit de
        créer un `ir.mail_server`. Toutes les valeurs qui décident de la
        portée (`from_filter`, `smtp_user`, le compte lié) sont posées ICI,
        pas reçues d'un formulaire.
        """
        adresse = (compte.login or "").strip().lower()
        if not adresse or not hote:
            return self.browse()
        Serveur = self.sudo()
        existant = Serveur.search([("from_filter", "=ilike", adresse)], limit=1)
        if existant:
            return existant
        valeurs = {
            "name": adresse,
            "smtp_host": hote,
            "smtp_port": int(port or 587),
            "smtp_encryption": "ssl" if (securite or "").upper() == "SSL" else "starttls",
            "smtp_user": adresse,
            "from_filter": adresse,
            "sequence": 20,
        }
        # Les DEUX modes empruntent : aucune copie du secret ici.
        valeurs.update({"smtp_authentication": "bf_compte",
                        "bf_account_id": compte.id,
                        "smtp_pass": False})
        if compte.auth_mode == "xoauth2":
            # Microsoft n'accepte XOAUTH2 qu'en STARTTLS sur 587.
            valeurs.update({"smtp_encryption": "starttls", "smtp_port": 587})
        return Serveur.create(valeurs)
