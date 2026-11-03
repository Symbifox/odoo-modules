# Recevables en retard — section du digest quotidien

Module-pont entre `bf_invoice_followup` et `daily_todo_digest`. `auto_install`,
donc il s'installe seul dès que les deux sont présents.

## Ce qu'il fait

1. **Ajoute une section « Factures en retard »** au digest quotidien, à la même
   place que les activités, les tâches et les rencontres : facture, client,
   échéance, jours de retard, solde dû, palier de relance. La ligne des jours
   vire à l'ambre à 30 jours et au rouge à 60.
2. **Désactive le cron du digest séparé** (`bf_invoice_followup.cron_weekly_followup_digest`)
   au `post_init_hook`, et le réactive à la désinstallation. Le cron des
   **activités** de relance (`cron_overdue_followup_activities`) n'est pas
   touché — les activités qu'il crée remontent déjà dans le digest.

## Qui voit la section

Seuls les destinataires du digest qui ont coché « Voir les recevables en retard
dans le digest quotidien » dans leurs préférences, c'est-à-dire exactement ceux
qui recevaient le courriel séparé. Le pont ne réutilise pas ce champ pour
élargir l'accès à de l'information financière.

Le champ vit dans `bf_invoice_followup` ; le pont n'en change que le libellé et
l'aide. ⚠️ Désinstaller le pont ne suffit **pas** à rendre le libellé d'origine :
`ir_model_fields.field_description` garde la dernière valeur écrite et rien ne
rejoue la définition du module propriétaire tant qu'il n'est pas remis à jour.
L'`uninstall_hook` réécrit donc les textes d'origine lui-même, et seulement si la
valeur en base est encore exactement celle du pont.

## Réglages

Sur `daily.digest.config` (Digest quotidien → configuration) :

| Champ | Défaut | Rôle |
|---|---|---|
| `include_overdue_invoices` | vrai | Coupe la section pour tout le monde. |
| `overdue_invoice_min_days` | 0 | Plancher de retard, au sens « échue depuis **au moins** N jours » (`bf_days_overdue >= N`). 0 = toutes les factures échues, ce que le courriel séparé envoyait ; une facture due aujourd'hui n'y est pas. Amorcé depuis `ir.config_parameter` `bf_invoice_followup.digest_min_days` s'il existe. |

## Ce qui change de comportement

* **Cadence.** Le courriel séparé partait selon une fréquence par usager
  (7 / 14 / 30 jours, `bf_followup_digest_interval`). La section, elle, paraît
  dans le digest **du jour** — comme les tâches et les activités en retard, dont
  elle est la même famille. Le champ de fréquence et l'horodatage du dernier
  envoi sont masqués dans les préférences : ils ne décrivent plus rien.
* **Poussée ntfy.** Le `_push_ntfy` du digest séparé partait avec son cron ; il
  s'arrête donc lui aussi. Sur une base où `bf_invoice_followup.ntfy_url` n'est
  pas réglé, il ne partait de toute façon pas. Réactiver le cron rend les deux.
* **Portée société.** Le courriel séparé ne filtrait pas par société ; la
  section filtre sur la société du destinataire, comme les autres ponts du
  digest. Sur une base à une seule société, rien ne change ; en multi-société,
  une facture d'une autre société ne paraît plus dans le digest d'un usager qui
  n'y est pas rattaché.

## Jours de retard : calculés ici, pas lus

Le nombre de jours affiché est dérivé du même « aujourd'hui » que la recherche,
pas lu dans `account.move.bf_days_overdue`. Ce calculé n'est **pas stocké** et ne
déclare pas `depends_context=('tz',)` alors qu'il appelle `context_today()` : sa
valeur est donc mise en cache dans le premier fuseau qui la lit et peut
contredire le filtre. Mesuré : sur un banc en UTC avec un rendu ancré à Montréal,
l'écart est d'un jour.

## Note d'implantation

Le marqueur `<!-- Divider -->` se trouve entre deux `<tr>` de la table
d'enveloppe : une section insérée **nue** en sortirait (*foster parenting*) et
serait rendue au-dessus de la carte. Comme les autres ponts du digest, celui-ci
enveloppe donc sa section dans son propre `<tr><td>`.

## Dépendances

`bf_invoice_followup`, `daily_todo_digest`.
