"""18.0.1.1.0 : faire recréer le gabarit de relance par le chargement.

Le gabarit corrigé ne pouvait pas atteindre un locataire par un simple `-u`, et
la première parade essayée ne marchait pas. Les deux sont écrites ici, parce que
la mauvaise a l'air aussi juste que la bonne.

🔴 **Ce qui NE marche PAS : basculer `ir_model_data.noupdate` à faux.**
`odoo/tools/convert.py` saute un enregistrement existant dès que l'ATTRIBUT
`noupdate="1"` du fichier XML est posé, sans jamais lire la colonne en base.
Éprouvé au banc : après la bascule et le `-u`, le corps était toujours l'ancien,
en `en_US` comme en `fr_CA`, et le drapeau restait à faux puisque rien ne l'avait
réécrit. Un contrôle `email_from LIKE '%email_formatted%'` y rendait même un faux
vert : l'ancien gabarit portait déjà `user.email_formatted` en repli.

✅ **Ce qui marche : retirer la paire enregistrement + `ir.model.data`.**
Le chargement ne trouve plus l'identifiant, et un bloc `noupdate` CRÉE un
enregistrement absent. Le gabarit renaît avec le corps neuf, et la clé `fr_CA`
figée — la seconde raison du défaut : le chargement n'écrit que `en_US`, et les
gens des locataires sont en `fr_CA` — disparaît avec lui.

Sûr ici : toutes les clés étrangères vers `mail_template` sont en `SET NULL` ou
`CASCADE` (relevé au banc), aucune action serveur ne vise ce gabarit, et il n'a
qu'un jour d'existence sans personnalisation (le registre était vide).

`post-migrate` serait trop tard : le chargement des données a lieu entre les deux.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT id, res_id FROM ir_model_data
         WHERE module = 'bf_training'
           AND name = 'mail_template_training_reminder'
           AND model = 'mail.template'
    """)
    row = cr.fetchone()
    if not row:
        return
    xmlid_id, gabarit_id = row
    cr.execute("DELETE FROM mail_template WHERE id = %s", (gabarit_id,))
    cr.execute("DELETE FROM ir_model_data WHERE id = %s", (xmlid_id,))
