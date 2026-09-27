"""Le mode de notification d'un compte d'exploitation.

Sans ce réglage, la liste du quart existe et le téléphone reste muet. C'est le
différenciateur annoncé de la suite, et il tient à un champ : un travail cédulé
pousse **0** avis vers un compte en `notification_type = 'email'` et **1** vers
un compte en `'inbox'`, parce que le message d'ouverture est de type
`notification` et qu'Odoo écarte de la poussée les destinataires en mode
courriel pour ce type. Mesuré. Le défaut d'un interne neuf est `'email'`.

🔴 **C'est un DÉFAUT À LA CRÉATION, pas une implication de groupe**, et l'écart
entre les deux est tout le sujet. Odoo 18 pilote `notification_type` par
l'appartenance à `mail.group_mail_notification_type_inbox` : faire impliquer ce
groupe par le groupe Exploitation « marche », et c'est précisément le piège.
Mesuré :

- la personne qui se remet en courriel obtient bien `'email'` dans la colonne,
  **mais la ligne d'appartenance au groupe survit à son geste** ;
- `notification_type` étant calculé sur `groups_id`, **le premier recalcul venu
  la rebascule en `'inbox'`** — et le recalcul se déclenche sur n'importe quelle
  écriture de groupe, un administrateur qui lui accorde tout autre chose y
  suffit. Sans un mot, et sans que rien ne l'ait annoncé.

Imposer, passe encore : on peut le dire à la personne. Imposer en ayant l'air de
ne pas imposer — lui laisser cocher son choix, l'afficher respecté, puis le
reprendre à la faveur d'un geste sans rapport — n'est pas défendable pour un
réglage PERSONNEL. Un défaut à la création, lui, ne se rejoue jamais : le compte
neuf part audible, et le premier qui n'en veut pas le change une fois pour
toutes.

🔴 **Et le défaut doit voir le groupe PAR LE CHEMIN DE L'ÉCRAN.** Une première version
de la surcharge ne lisait que `groups_id`, alors que le
formulaire des utilisateurs poste les champs REIFIÉS (`in_group_<id>`,
`sel_groups_<a>_<b>_<c>`) et que c'est `UsersView.create` de `base` qui les
convertit en `groups_id` — APRÈS cette surcharge dans le MRO. Mesuré côte à
côte : `create(groups_id=[(6, 0, [ops])])` rendait `inbox`, et
`create(in_group_<ops>=True)` rendait `email` avec le groupe pourtant accordé.
Le différenciateur ne se déclenchait donc sur AUCUN compte créé normalement, et
les cinq tests passaient parce que leur fabrique passe toujours par `groups_id`.

⚠️ Un test qui ne prend qu'un chemin d'écriture n'éprouve que ce chemin. Celui
de l'écran s'éprouve maintenant aussi, et il lit le nom du champ reifié dans
l'arch plutôt que de le taper : le nom porte l'identifiant du groupe, qui change
d'une base à l'autre.

⚠️ **La contrepartie est assumée** : un compte qui EXISTE DÉJÀ et à qui l'on
donne le groupe Exploitation plus tard n'est pas touché — il porte peut-être un
choix délibéré, et l'écraser serait l'imposition qu'on vient de refuser. C'est
la lecture du quart (`silent_user_ids`) qui nomme ces comptes-là, pour qu'on
aille le DEMANDER à la personne au lieu de trancher à sa place.
"""
from odoo import api, models

INBOX_GROUP = "mail.group_mail_notification_type_inbox"
OPERATIONS_GROUP = "bf_property_operations.group_bf_property_operations"


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model_create_multi
    def create(self, vals_list):
        """Un compte d'exploitation neuf naît audible.

        ⚠️ Écrit dans `create()` et non en `default=` : un défaut de champ ne
        voit pas les groupes que porte la création, et il s'estamperait dans
        l'historique du compte comme une valeur choisie.
        """
        ops_group = self.env.ref(OPERATIONS_GROUP, raise_if_not_found=False)
        if ops_group:
            inbox_group = self.env.ref(INBOX_GROUP, raise_if_not_found=False)
            for vals in vals_list:
                if self._bf_wants_default_inbox(vals, ops_group, inbox_group):
                    vals["notification_type"] = "inbox"
        return super().create(vals_list)

    @api.model
    def _bf_wants_default_inbox(self, vals, ops_group, inbox_group):
        """Le défaut ne s'applique qu'à un compte d'exploitation neuf et muet.

        Quatre refus, et chacun protège un choix qui n'est pas le nôtre :
        un mode déjà demandé à la création, un compte de portail (que la
        contrainte SQL d'Odoo interdit en boîte Odoo de toute façon), un compte
        sans le groupe Exploitation, et un compte à qui on donne DÉJÀ le groupe
        de la boîte Odoo — celui-là est réglé par le chemin natif.
        """
        if vals.get("notification_type"):
            return False
        if vals.get("share"):
            return False
        group_ids = self._bf_group_ids_from_vals(vals)
        if inbox_group and inbox_group.id in group_ids:
            return False
        if not group_ids:
            return False
        # Le groupe peut arriver par implication — un gestionnaire de la suite
        # tient aussi l'exploitation — et pas seulement en clair dans les vals.
        groups = self.env["res.groups"].browse(sorted(group_ids)).exists()
        return ops_group in (groups | groups.trans_implied_ids)

    @api.model
    def _bf_group_ids_from_vals(self, vals):
        """Les groupes qu'une création demande, par les DEUX chemins.

        ⚠️ Seules les commandes x2many qui ATTRIBUENT comptent : `link` (4),
        `set` (6) et `create` (0) n'ont pas le même porteur d'identifiants, et
        lire naïvement `command[1]` pour toutes rendrait le premier identifiant
        du `set` au lieu de sa liste.

        🔴 Et `groups_id` ne suffit pas. Le formulaire des utilisateurs ne le
        poste pas : il poste les champs REIFIÉS que `_update_user_groups_view`
        fabrique — `in_group_<id>` pour un groupe isolé, `sel_groups_<a>_<b>`
        pour une catégorie à choix unique, dont la valeur EST l'identifiant du
        groupe choisi. Leur conversion en `groups_id` se fait dans
        `UsersView.create` de `base`, qui vient après cette surcharge : les
        lire ici est le seul moyen de voir ce que l'écran demande.
        """
        group_ids = set()
        for command in vals.get("groups_id") or []:
            if not isinstance(command, (list, tuple)) or not command:
                continue
            if command[0] == 4 and len(command) > 1:
                group_ids.add(command[1])
            elif command[0] == 6 and len(command) > 2:
                group_ids.update(command[2] or [])
        for key, value in vals.items():
            if not value:
                # Une case décochée et un choix vide ne demandent rien.
                continue
            if key.startswith("in_group_"):
                # `in_group_<id>` est un booléen : le groupe est dans le NOM.
                suffix = key[len("in_group_"):]
                if suffix.isdigit():
                    group_ids.add(int(suffix))
            elif key.startswith("sel_groups_"):
                # `sel_groups_<a>_<b>_<c>` est une sélection : le groupe est
                # dans la VALEUR, et le nom ne porte que les choix possibles.
                if isinstance(value, int) and not isinstance(value, bool):
                    group_ids.add(value)
        return group_ids
