# Household Family: Healthy Fox (`bf_household_family_health`)

Bridge between `bf_household_family` and `bf_health`. Installs by itself.
LGPL-3. Labels in English, French (Canada) in `i18n/fr_CA.po`.

- A person followed in Healthy Fox **is** a child of the household: created from Healthy Fox,
  the dependent creates the child; created for a child, it takes the child's first name,
  birth and second parent. Only the child's primary parent starts health tracking (they hold
  the records, as in Healthy Fox).
- The child's parents are the dependent's parents: naming, removing or swapping a parent in
  the family changes the holder and second parent in Healthy Fox, which rewrites the records'
  owner and detaches a parent who leaves.
- The birth Healthy Fox follows decides the move at 14: while health is followed, only Blue
  Fox corrects it, in the family as in Healthy Fox. The birth day stays free.
- At the move, the teen's account becomes the child's own account.
- **Emergency card**: when the person (or the child's parent) ticks the box, the card shows
  the name and dosage of active medications and the name of active or managed conditions.
  Nothing else leaves Healthy Fox. Healthy Fox has no allergy record: allergies are typed on
  the card.
- Outside a household (an account not in the household group), a dependent stays alone, as
  without this bridge.
- In Healthy Fox alone, the second parent is any other internal account of the health group. In a
  household, this bridge copies it to the child's second parent, and the family refuses a teen as a
  parent: the second parent is an adult member.
