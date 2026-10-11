"""Proposer un regroupement, et ne l'écrire que si on le confirme.

Pourquoi ça existe
------------------
Sur un coffre réel bien rempli, les trois champs de rangement (étiquette,
client, projet) étaient vides sur **la totalité** des fiches. L'émetteur, qui
sert de dernier recours à l'affichage, n'en regroupait qu'une petite minorité :
la grande majorité des tokens étaient seuls sous leur émetteur. Autrement dit,
le coffre n'était pas mal rangé, il n'était pas rangé du tout, et personne
n'allait saisir des centaines d'étiquettes à la main.

La règle qui range vraiment
---------------------------
⚠️ Ce n'est PAS l'émetteur. Sur ce même coffre, **la grande majorité des noms de
compte sont une adresse de courriel**, pour quelques dizaines de domaines
distincts dont une poignée couvre la moitié du coffre. Le domaine du compte dit
sous quelle IDENTITÉ le token a été créé, et c'est la question qu'on se pose
vraiment devant un coffre : qu'est-ce que je détiens sous l'identité de
l'entreprise, sous la mienne, sous celle d'un client. Ranger par émetteur
redonnait autant de tas que de tokens.

🔴 **Rien n'est écrit sans confirmation.** L'assistant propose, montre les paquets
avec leur compte, laisse renommer chaque paquet et décocher ce qu'on ne veut pas,
puis écrit. Une règle qui écrit toute seule sur des centaines de fiches serait
irrattrapable, et ce sont des fiches que personne ne relit.

⚠️ **Ce qui porte déjà une étiquette n'est jamais touché.** Le rangement de
quelqu'un ne se défait pas par une devinette, même meilleure.
"""

import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError

# Une adresse dans le nom du compte. Volontairement tolérant sur la partie
# locale (les gens y mettent des points, des plus, des tirets) et strict sur le
# domaine, qui est la seule partie qu'on garde.
_COURRIEL = re.compile(r'[^@\s]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')


class BfOtpGroupWizard(models.TransientModel):
    _name = 'bf.otp.group.wizard'
    _description = "Token grouping suggestion"

    line_ids = fields.One2many(
        'bf.otp.group.wizard.line', 'wizard_id', string="Suggestions")
    total = fields.Integer(string="Tokens examined", readonly=True)
    deja_range = fields.Integer(string="Already filed", readonly=True)
    sans_proposition = fields.Integer(
        string="No suggestion", readonly=True,
        help="Neither an address in the account name nor an issuer shared "
             "with another token. Here, no guess is better than none.")

    # -------------------------------------------------------------------------
    # La proposition
    # -------------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        valeurs = super().default_get(fields_list)
        tokens = self._tokens_vises()
        propositions, deja, sans = self._proposer(tokens)
        valeurs.update({
            'total': len(tokens),
            'deja_range': deja,
            'sans_proposition': sans,
            'line_ids': [
                (0, 0, {
                    'nom': nom,
                    'nombre': len(ids),
                    'token_ids': [(6, 0, ids)],
                    'retenu': True,
                })
                for nom, ids in propositions
            ],
        })
        return valeurs

    def _tokens_vises(self):
        """Les tokens sur lesquels l'assistant travaille.

        ⚠️ Toujours bornés à la personne connectée, même quand le client envoie
        une sélection : un assistant ouvert depuis une liste reçoit des
        identifiants, et des identifiants ne sont pas une autorisation.
        """
        Token = self.env['bf.otp.token']
        domaine = [('user_id', '=', self.env.uid)]
        actifs = self.env.context.get('active_ids')
        if actifs and self.env.context.get('active_model') == 'bf.otp.token':
            domaine.append(('id', 'in', [int(i) for i in actifs]))
        return Token.search(domaine)

    @api.model
    def _domaine_du_compte(self, nom):
        """Le domaine de l'adresse contenue dans le nom du compte, ou rien."""
        trouve = _COURRIEL.search(nom or '')
        return trouve.group(1).lower() if trouve else ''

    @api.model
    def _proposer(self, tokens):
        """Rend (paquets, déjà rangés, sans proposition).

        🔴 Le compte des émetteurs se fait sur TOUT le coffre de la personne, pas
        sur la sélection. Sinon sélectionner deux lignes d'un même émetteur en
        ferait un paquet, et sélectionner l'une des deux n'en ferait plus : la
        proposition changerait selon ce qu'on a cliqué avant, ce qui est
        exactement ce qu'on ne veut pas d'une règle.
        """
        tout = self.env['bf.otp.token'].search([('user_id', '=', self.env.uid)])
        compte_emetteur = {}
        for t in tout:
            e = (t.issuer or '').strip()
            if e:
                compte_emetteur[e] = compte_emetteur.get(e, 0) + 1

        paquets, deja, sans = {}, 0, 0
        for t in tokens:
            if (t.group_name or '').strip():
                deja += 1
                continue
            domaine = self._domaine_du_compte(t.name)
            emetteur = (t.issuer or '').strip()
            if domaine:
                cle = domaine
            elif emetteur and compte_emetteur.get(emetteur, 0) > 1:
                cle = emetteur
            else:
                sans += 1
                continue
            paquets.setdefault(cle, []).append(t.id)

        # Les gros paquets d'abord : c'est là que le ménage paie, et c'est ce
        # qu'on veut voir sans faire défiler.
        ordonnes = sorted(paquets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        return ordonnes, deja, sans

    # -------------------------------------------------------------------------
    # L'écriture
    # -------------------------------------------------------------------------

    def action_appliquer(self):
        self.ensure_one()
        retenues = self.line_ids.filtered(lambda l: l.retenu)
        if not retenues:
            raise UserError(_(
                "No batch selected: there is nothing to write."))
        touches = 0
        for ligne in retenues:
            nom = (ligne.nom or '').strip()
            if not nom:
                raise UserError(_(
                    "A selected batch has no name. Name it or uncheck it."))
            # ⚠️ Re-borner à la personne connectée au moment d'écrire, et pas
            # seulement au moment de proposer : l'assistant est un modèle
            # transitoire, ses lignes sont modifiables par son propriétaire, et
            # un identifiant glissé là ne doit pas ouvrir le coffre d'un autre.
            cibles = ligne.token_ids.filtered(
                lambda t: t.user_id.id == self.env.uid
                and not (t.group_name or '').strip()
            )
            cibles.write({'group_name': nom})
            touches += len(cibles)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _("%s token(s) filed.", touches),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class BfOtpGroupWizardLine(models.TransientModel):
    _name = 'bf.otp.group.wizard.line'
    _description = "Suggested batch"
    _order = 'nombre desc, nom'

    wizard_id = fields.Many2one(
        'bf.otp.group.wizard', required=True, ondelete='cascade')
    nom = fields.Char(string="Grouping", required=True)
    nombre = fields.Integer(string='Tokens', readonly=True)
    retenu = fields.Boolean(string="Apply", default=True)
    token_ids = fields.Many2many('bf.otp.token', string="Target tokens")
