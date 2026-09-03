"""Socle commun aux essais : les deux langues canadiennes doivent EXISTER.

⚠️ Le module écrit `with_context(lang="fr_CA")` un peu partout — courriels,
pages publiques, filigranes. Sur une base de travail les deux langues sont
installées depuis longtemps et personne n'y pense. Sur une base NEUVE, celle
que la CI fabrique par lot, seul `en_US` est actif, et `Environment.lang`
(odoo/api.py) lève alors `UserError: Invalid language code: fr_CA`. Cent
soixante-quatre essais du module tombaient là-dessus, sur une cause qui n'a
rien à voir avec ce qu'ils vérifient.

`_activate_lang` bascule le drapeau et RIEN d'autre : les `.po` ne sont
importés qu'à l'installation, pour les langues actives à ce moment-là. Les
essais qui lisent du texte traduit ont donc besoin du chargement explicite qui
suit.
"""


class LanguesActives:

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        langues = ["fr_CA", "en_CA"]
        for code in langues:
            cls.env["res.lang"].sudo()._activate_lang(code)
        cls.env["ir.module.module"].sudo()._load_module_terms(
            ["bf_sign"], langues)
        cls.env.registry.clear_cache()
