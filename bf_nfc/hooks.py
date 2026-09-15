"""Ce qui ne peut pas s'écrire en données XML à l'installation.

⚠️ Les types de fiche par défaut (tâche, projet) ne se sèment pas en XML : le
champ ``model`` est une sélection lue dans le registre, et une base sans le module
Projet refuserait la ligne, donc l'installation entière. On sème ce qui existe.
"""
TYPES_DEFAUT = ["res.partner", "project.task", "project.project"]


def semer_les_types(env, noms):
    Type = env["bf.nfc.target.type"].sudo().with_context(active_test=False)
    deja = set(Type.search([]).mapped("model"))
    for rang, nom in enumerate(noms):
        nom = (nom or "").strip()
        if nom and nom in env and nom not in deja:
            Type.create({"model": nom, "sequence": 10 + rang})
            deja.add(nom)


def post_init_hook(env):
    semer_les_types(env, TYPES_DEFAUT)
