"""🔴 Un gabarit en `noupdate="1"` ne se met JAMAIS à jour tout seul.

Vécu à l'essai : le gabarit d'invitation a été refait aux couleurs de la
maison, la version est partie en production, et les courriels ont continué de
sortir avec l'ANCIEN corps. Le `-u` charge bien le fichier de données, mais
`noupdate="1"` lui interdit d'écraser un enregistrement qui existe déjà. Le XML
disait vrai, la base disait autre chose, et c'est la base qui poste.

Le `noupdate` reste : il existe pour qu'un client qui a retouché son gabarit ne
se le fasse pas écraser à la mise à jour suivante. Ce qui change, c'est qu'une
correction de NOTRE fait s'accompagne désormais d'une migration qui retire la
paire enregistrement + `ir.model.data`, **avant** le chargement des données.
Le fichier XML la recrée ensuite, à neuf.

⚠️ `pre-migrate` et pas `post-migrate` : le chargement des données a lieu entre
les deux. Supprimer après coup laisserait le module sans gabarit.

⚠️ Le gabarit retouché par un client est perdu ici. C'est assumé pour un module
de cet âge, où aucune installation n'a encore de retouche; le jour où ce ne
sera plus vrai, il faudra comparer au corps d'origine avant de remplacer.
"""

XMLID = ("bf_employee_experience_pulse", "mail_template_pulse_invite")


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        "SELECT id, res_id FROM ir_model_data WHERE module = %s AND name = %s",
        XMLID,
    )
    ligne = cr.fetchone()
    if not ligne:
        return
    donnee_id, gabarit_id = ligne
    cr.execute("DELETE FROM mail_template WHERE id = %s", (gabarit_id,))
    cr.execute("DELETE FROM ir_model_data WHERE id = %s", (donnee_id,))
