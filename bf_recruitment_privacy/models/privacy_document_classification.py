# Part of bf_recruitment_privacy. Voir LICENSE.
"""Rendre classifiables les trois modèles porteurs de renseignements personnels.

🔴 **Deux lignées de `privacy_consent` existent, et elles ne s'étendent pas de
la même façon.** Mesuré le 2026-08-31 en installant ce pont sur la démo :

| Lignée | Version | Point d'extension | `ALLOWED_MODELS` |
|---|---|---|---|
| Arbre locataire | 18.0.4.0.11 | `_privacy_classifiable_models()` | 19 modèles |
| Catalogue publié | 18.0.4.3.2 | **aucun** | 22 modèles, dont `sign.request` |

La version publiée est **plus haute** et n'a pourtant pas le crochet : la
contrainte y lit `self.ALLOWED_MODELS` en direct. Le catalogue a choisi de faire
grossir la constante (c'est ainsi que `sign.request` y est entré) ; l'arbre
locataire a choisi le crochet. Ce n'est pas un retard de version, ce sont deux
lignées.

⚠️ **Conséquence pour un module de catalogue : écrire contre le crochet seul ne
marche pas là où le module doit vivre.** Un pont qui se contente de surcharger
`_privacy_classifiable_models()` est **silencieusement inerte** sur la version
publiée : sa méthode n'est jamais appelée, et la classification de ses modèles
est refusée par une `ValidationError` qui parle de « modèles pouvant contenir
des renseignements personnels ». `bf_sign_privacy` est dans ce cas sur la démo ;
il ne s'en aperçoit pas parce que ses deux modèles ont été ajoutés à la main à
la constante du catalogue.

Ce fichier tient donc les deux lignées : il **définit** le crochet quand il
manque, et il fait passer la contrainte par lui.
"""

from odoo import _, api, models
from odoo.exceptions import ValidationError

_RECRUITMENT_MODELS = {
    "hr.candidate",
    "hr.applicant",
    "bf.interview",
}


class PrivacyDocumentClassification(models.Model):
    _inherit = "privacy.document.classification"

    def _privacy_classifiable_models(self):
        """Les modèles classifiables, sur l'une ou l'autre lignée.

        On surcharge la méthode, jamais la constante `ALLOWED_MODELS`. La
        surcharge compose avec celle des autres ponts ; redéfinir la constante
        ferait perdre leurs modèles au dernier module chargé.

        ⚠️ Le `except AttributeError` n'est pas de la prudence décorative. Sur
        la lignée publiée, aucun ancêtre ne définit cette méthode : l'appel à
        `super()` lève. Et l'ordre de chargement décide QUI lève : si un autre
        pont se retrouve au-dessus de nous dans la MRO, c'est son propre
        `super()` qui casse, pas le nôtre. On retombe alors sur la constante,
        qui est justement l'endroit où le catalogue inscrit ses modèles.

        ⚠️ `bf.interview.rating` reste dehors, et c'est voulu. Une notation
        porte bien un commentaire sur une personne, mais la classifier
        permettrait à une campagne d'en détruire une seule en laissant la séance
        debout : le score agrégé changerait sans que rien ne le dise, et
        l'agrégat anonymisé ne serait pas recalculé. La notation se détruit avec
        sa séance, jamais toute seule.

        ⚠️ `hr.candidate` est classifiable mais n'est jamais classée
        automatiquement : la personne suit ses candidatures et part avec la
        dernière. Le cas à classer à la main est celui d'un CV reçu
        spontanément, sans aucune candidature ouverte.

        ⚠️ `bf.interview.guide` et ses critères restent dehors aussi. Une grille
        est une décision d'entreprise, pas un renseignement sur quelqu'un. La
        classifier ferait apparaître le catalogue des grilles dans les campagnes
        de destruction, où une suppression emporterait par contrainte tout ce
        qui s'y rattache.
        """
        try:
            base = set(super()._privacy_classifiable_models())
        except AttributeError:
            base = set(self.ALLOWED_MODELS)
        return base | _RECRUITMENT_MODELS

    @api.constrains("res_model")
    def _check_allowed_model(self):
        """Faire passer la contrainte par le crochet, sur les deux lignées.

        ⚠️ **Cette surcharge change un comportement partagé, et il faut le
        savoir.** Sur la lignée publiée, la contrainte du socle lit
        `self.ALLOWED_MODELS` sans jamais appeler le crochet ; la remplacer ici
        rétablit le point d'extension pour **tous** les ponts, pas seulement
        celui-ci. Le changement est strictement un sur-ensemble : le crochet
        retombe sur la constante, donc rien de ce qui était permis ne devient
        interdit. Sur la lignée locataire, où le crochet existe déjà, cette
        méthode fait exactement ce que faisait celle du socle.

        La sortie propre serait que les deux lignées de `privacy_consent` se
        rejoignent. Tant qu'elles divergent, un module de catalogue ne peut pas
        dépendre d'un crochet que sa dépendance publiée n'a pas.
        """
        allowed = self._privacy_classifiable_models()
        for record in self:
            if record.res_model and record.res_model not in allowed:
                raise ValidationError(_(
                    "Le modèle « %(model)s » ne peut pas être classifié. Seuls "
                    "les modèles pouvant contenir des renseignements personnels "
                    "sont autorisés.",
                    model=record.res_model,
                ))
