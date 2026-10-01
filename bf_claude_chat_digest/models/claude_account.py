"""Le courriel du compte, pour savoir lequel on lit.

Le répertoire de configuration reste la clé du compte ; le courriel n'est qu'une
étiquette, mais c'est la seule qui se reconnaît au premier coup d'œil quand deux
abonnements portent le même nom de maison.

Il vient de la sonde, qui le lit dans le `.claude.json` du répertoire et le
glisse dans la charge utile sous `account_email`. Un relevé qui ne le porte pas
laisse la valeur en place : une sonde plus ancienne ne doit pas l'effacer.
"""

from odoo import fields, models


class ClaudeAccount(models.Model):
    _inherit = "claude.account"

    courriel = fields.Char(
        string="Courriel du compte", readonly=True,
        help="Le compte Claude connecté dans ce répertoire, tel que la sonde "
             "l'a lu au dernier relevé.",
    )

    def _absorber(self, charge):
        super()._absorber(charge)
        courriel = (charge or {}).get("account_email")
        if isinstance(courriel, str) and courriel.strip():
            self.write({"courriel": courriel.strip()[:255]})
