{
    "name": "Copropriété : pont vie privée (Loi 25)",
    "summary": "Inscrire au registre des traitements ce que la suite copropriété collecte, et rattacher le consentement de l'art. 1070 al. 1 au dossier Loi 25",
    "version": "18.0.2.1.1",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core.
    "license": "Other proprietary",
    "images": ["static/description/register_en.png"],
    "application": False,
    "installable": True,
    # Pont, sur le patron de bf_cx_privacy : il s'installe seul quand les deux
    # côtés sont là, et la suite fonctionne parfaitement sans lui.
    "auto_install": True,
    "description": """
Copropriété : pont vie privée (Loi 25)
======================================

Un syndicat qui tient le registre de l'art. 1070 C.c.Q. traite des
renseignements personnels, et la Loi 25 ne fait pas d'exception pour lui. Ce
pont inscrit au registre des activités de traitement ce que la suite collecte
déjà, avec sa base légale et sa durée de conservation.

⚠️ **Trois finalités, trois régimes qui ne se confondent pas.**

1. **Le registre du syndicat** : le nom et l'adresse postale y sont par
   OBLIGATION LÉGALE (art. 1070 al. 1). Aucun consentement à demander, et en
   demander un laisserait croire qu'on peut le refuser.
2. **Les avis par texto** : le numéro n'est au registre que « si celui-ci y
   consent expressément » (même alinéa). Consentement exprès, révocable, daté.
   Le pont rattache la ligne de consentement du module au dossier Loi 25, pour
   qu'elle cesse d'être une île.
3. 🔴 **Le journal des colis et des visiteurs** : il porte des renseignements
   sur des TIERS qui n'ont rien consenti du tout. Le visiteur n'est ni
   copropriétaire ni occupant, et il n'a pas de compte pour retirer quoi que ce
   soit. Ce que la Loi 25 laisse ici, c'est l'obligation d'informer et celle de
   ne pas garder au-delà de la nécessité.

⚠️ **La durée déclarée doit être celle qui s'applique.** Le module purge déjà le
journal selon la durée choisie par chaque syndicat. Une politique de
conservation qui annoncerait autre chose serait pire que pas de politique : le
pont compare les deux et le dit à l'écran quand elles divergent.

⚠️ **Ce pont ne détruit rien lui-même.** La purge du journal existe déjà, elle
supprime, et elle est éprouvée. Router les mêmes pièces vers une deuxième
mécanique de destruction ferait courir le risque de certifier ce que personne
n'a fait.
""",
    "depends": [
        "bf_property_portal",
        "bf_property_sms",
        "privacy_consent",
    ],
    "data": [
        "data/privacy_purpose_data.xml",
        "views/bf_property_privacy_views.xml",
    ],
}
