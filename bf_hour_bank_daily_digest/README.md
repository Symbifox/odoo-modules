# Banque d'heures — section du digest quotidien (`bf_hour_bank_daily_digest`)

Module-pont qui injecte une section **« Banques d'heures »** dans le courriel du
digest quotidien (`daily_todo_digest`).

S'auto-installe lorsque `bf_hour_bank` **et** `daily_todo_digest` sont tous deux
présents.

## Ce que la section rapporte

**Paliers franchis.** Les `hour.bank.threshold.event` déclenchés sur l'horizon
retenu (7 jours par défaut) : banque, palier, valeur mesurée, date. Le libellé du
palier est reconstruit à partir du **cliché** porté par l'événement
(`mode_snapshot`, `value_snapshot`), pas à partir du palier vivant : la ligne est
en `ondelete='set null'` et sa valeur peut être modifiée après coup, si bien que
le libellé courant ne décrit plus forcément ce qui a déclenché.

Un courriel d'alerte part déjà aux destinataires de la banque au moment du
déclenchement. La section est le rappel interne du matin, pas le mécanisme
d'alerte.

**Banques sans palier.** Les banques actives qu'aucun palier actif ne surveille,
soit parce que `threshold_mode` est resté à `disabled` — sa valeur par défaut —,
soit parce que le mode est posé sans aucune ligne active, ce qui ne déclenche pas
davantage. Ce rappel revient chaque matin tant que ce n'est pas corrigé, et c'est
voulu : une banque que personne ne surveille n'avertira jamais personne, quoi que
vos propres ententes promettent au client.

Sans événement ni banque non surveillée, aucune section n'est rendue.

## Réglages

Sur la fiche de configuration du digest (`daily.digest.config`) :

| Champ | Défaut | Rôle |
|---|---|---|
| `include_hour_bank_thresholds` | vrai | Active la section entière |
| `hour_bank_threshold_days` | 7 | Horizon de rappel des paliers franchis |
| `hour_bank_flag_unset` | vrai | Affiche le rappel des banques sans palier |

## Quand le digest part

La section voyage dans un digest **qui part**. Depuis la version 18.0.3.0.0 du
digest quotidien, le courriel part dès qu'une section est allumée, la nôtre
comprise : un palier franchi suffit donc à faire partir le courriel, même un
jour sans activité, sans tâche et sans rencontre. Sur une version antérieure,
la décision ne regardait que les sections du cœur, et un palier seul ne
réveillait personne.

## Dépendances

`bf_hour_bank`, `daily_todo_digest`.

⚠️ Ce pont est sous **LGPL-3**, mais `bf_hour_bank`, dont il ne se
charge pas, est sous **Business Source License 1.1** : la licence du
pont ne vous donne donc pas celle de son parent. Le fichier `LICENSE`
de chaque module est celui qui gouverne.

## Licence

Distribué sous licence **LGPL-3**. Voir le fichier `LICENSE`.
