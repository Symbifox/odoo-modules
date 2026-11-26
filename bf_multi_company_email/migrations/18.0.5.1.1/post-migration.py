"""Bascule en anglais le nom du cron de provisionnement, que `noupdate` gele.

Pourquoi une migration pour UNE chaine
--------------------------------------
`data/signature_link_cron.xml` est dans un `<data noupdate="1">` : voulu, pour
qu'une societe qui a retouche la planification ne se la fasse pas remettre a
chaque montee. Le prix est que la source anglaise posee dans ce fichier
n'atteint JAMAIS la base : la rangee garde le libelle francais charge a
l'installation, et `-u` n'y touche pas. Mesure sur une base d'essai :
`select a.name` rendait encore `{"en_US": "Signatures : provisionner et
repointer les liens rapides"}` apres une montee complete, catalogue compris.

Le catalogue ne peut pas le reparer non plus : il est indexe sur le msgid, donc
sur la source ANGLAISE, et la rangee porte du francais dans la fente `en_US`.
Les deux fentes s'ecrivent donc ici, a la main.

La rangee visee est celle de `ir.actions.server` : `ir.cron` delegue son `name`
(`_inherits`), et c'est l'identifiant XML derive `…_ir_actions_server` qui la
nomme.

L'ORDRE DES DEUX ECRITURES N'EST PAS LIBRE
------------------------------------------
Ecrire un champ traduisible dans sa langue SOURCE remet le jsonb a la seule
fente `en_US` et jette les traductions. L'anglais passe donc d'abord, le
francais ensuite ; l'inverse perdrait le francais sans un mot.

CE QUI EST GARDE
----------------
Une valeur `en_US` qui n'est plus le francais livre est une retouche (ou un
rejeu de cette migration) : on n'y touche pas. La migration est donc
rejouable, et elle ne reecrit pas par-dessus la decision de quelqu'un.
"""
import logging

_logger = logging.getLogger(__name__)

XMLID = ("bf_multi_company_email."
         "ir_cron_signature_link_provision_ir_actions_server")
FR = "Signatures : provisionner et repointer les liens rapides"
EN = "Signatures: provision and repoint the quick links"


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    action = env.ref(XMLID, raise_if_not_found=False)
    if not action:
        _logger.warning("%s introuvable : nom du cron laisse tel quel", XMLID)
        return
    actuel = action.with_context(lang="en_US").name
    if actuel != FR:
        _logger.info("nom du cron deja en %r : rien a faire", actuel)
        return
    action.with_context(lang="en_US").write({"name": EN})
    if "fr_CA" in dict(env["res.lang"].get_installed()):
        action.update_field_translations("name", {"fr_CA": FR})
    _logger.info("nom du cron bascule en anglais, francais garde au catalogue")
