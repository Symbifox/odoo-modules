"""Ce qu'une base NEUVE n'a pas, et que ces essais tiennent pour acquis.

La CI fabrique une base par lot. Deux choses y manquent, présentes depuis si
longtemps sur une base de travail que personne n'y pense — et les deux tombent
loin de ce que l'essai vérifie.

1. **Les deux langues canadiennes.** Le module écrit
   `with_context(lang="fr_CA")` un peu partout : courriels, pages publiques,
   filigranes. Seul `en_US` est actif sur une base neuve, et
   `Environment.lang` (odoo/api.py) lève `UserError: Invalid language code`.
   ⚠️ `_activate_lang` bascule un drapeau et n'importe AUCUNE traduction : les
   `.po` ne sont lus qu'à l'installation, pour les langues actives alors. D'où
   le chargement explicite des termes.

2. **Une adresse d'expédition.** Depuis la 17, elle vient d'un
   `mail.alias.domain` rattaché à la société, et le module `mail` n'en livre
   aucun par défaut. Sans lui, `mail_mail._send` s'arrête sur « You must either
   provide a sender address explicitly » — y compris quand l'essai a remplacé
   `send_email` par un mouchard, puisque l'arrêt est AVANT.
"""


class BaseNeuve:

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        langues = ["fr_CA", "en_CA"]
        for code in langues:
            cls.env["res.lang"].sudo()._activate_lang(code)
        cls.env["ir.module.module"].sudo()._load_module_terms(
            ["bf_sign"], langues)

        domaine = cls.env["mail.alias.domain"].sudo().search([], limit=1)
        if not domaine:
            domaine = cls.env["mail.alias.domain"].sudo().create({
                "name": "example.com",
                "bounce_alias": "bounce",
                "catchall_alias": "catchall",
                "default_from": "notifications",
            })
        cls.env.company.sudo().alias_domain_id = domaine
        cls.env.registry.clear_cache()
