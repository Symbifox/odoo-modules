"""Garde de classe : aucune phrase rangée en base par un calcul.

🔴 Vingt-deux champs calculés STOCKÉS de la suite
assemblaient une phrase traduisible. Une phrase stockée se range dans la langue
de qui a déclenché le calcul, et le lecteur suivant la lit telle quelle : une
gestionnaire francophone lisait l'anglais dès qu'un collègue anglophone avait
enregistré en dernier. Le catalogue `en_CA.po` n'y pouvait rien, les chaînes y
étaient ; le défaut ne se voyait qu'en regardant l'écran dans l'autre langue.

⚠️ La liste vient du REGISTRE, pas d'une énumération tapée à la main : un champ
ajouté demain par n'importe quel module de la suite tombe dans la garde sans que
personne ait à y penser. Les exceptions sont nommées et motivées ; un nom ajouté
ici doit dire pourquoi sa valeur n'est pas une phrase.

Ne garde que les modules installés au moment de l'essai : sur un banc qui monte
les dix-neuf modules ensemble, elle voit toute la suite.
"""
from odoo.tests.common import TransactionCase, tagged

SUITE = ("bf_property", "bf_rental")

NOT_A_SENTENCE = {
    ("bf.property.unit", "owner_display"):
        "des noms de personnes joints par une virgule : aucune langue",
    ("bf.rental.notice", "silence_article"):
        "« art. 1945 » s'écrit de même en anglais, et c'est la forme que le "
        "catalogue retient partout ailleurs (« Permanent admission to "
        "residential care (art. 1974) »)",
    ("bf.property.attestation", "works_planned_10y"):
        "une proposition MODIFIABLE (readonly=False) tirée du carnet : c'est "
        "le texte de la pièce que le syndicat remet, il appartient à qui la "
        "rédige, et le rendre à la lecture effacerait ce qu'on y a corrigé",
}


@tagged("post_install", "-at_install")
class TestNoStoredSentence(TransactionCase):

    def _suite_fields(self):
        for model_name in self.env.registry:
            for field in self.env[model_name]._fields.values():
                if (field._module or "").startswith(SUITE):
                    yield field

    def test_no_stored_computed_text_outside_the_named_exceptions(self):
        stored_text = sorted({
            (field.model_name, field.name)
            for field in self._suite_fields()
            if field.type in ("char", "text", "html")
            and field.compute
            and field.store
            and not field.related
            and not field.translate
        })
        unexpected = [key for key in stored_text if key not in NOT_A_SENTENCE]
        self.assertFalse(
            unexpected,
            "Calculés stockés qui rangent du texte : s'ils assemblent une "
            "phrase, elle se figera dans la langue du calculateur. Rendre la "
            "phrase à la lecture (non stocké, @api.depends_context('lang')), ou "
            "nommer l'exception dans NOT_A_SENTENCE avec sa raison.",
        )

    def test_a_compute_method_never_mixes_stored_and_read_time_fields(self):
        """Odoo 18 le signale à l'installation, et on l'a pris pour du bruit.

        « inconsistent 'store' for computed fields » : lire le champ non stocké
        recalcule et RÉÉCRIT ses voisins stockés, avec les droits du lecteur.
        C'était la pile de fin d'installation d'une base de démonstration, sur
        `art1087_receivables` et `attestation_authority`.
        """
        computed = self.env.registry.field_computed
        mixed = sorted({
            "%s: %s" % (
                field.model_name,
                ", ".join(sorted(sibling.name for sibling in computed[field])),
            )
            for field in self._suite_fields()
            if field in computed
            and len({sibling.store for sibling in computed[field]}) > 1
        })
        self.assertFalse(mixed, "Méthodes de calcul qui mêlent stocké et non stocké")
