# Ownership chart (`bf_org_chart_ownership`)

Who owns what, for how much, since when, and where the information came from.

## The percentage lives on the link

That is what separates ownership from hierarchy, and what puts it out of reach
of Odoo's hierarchy view: that view follows **one** parent per row and has
nowhere to read a share. A company has several owners, each for a percentage,
sometimes by class of shares.

`bf.ownership` therefore carries: owner, owned company, percentage, share
class, voting rights, effective date, end date, and **the source** (public
registry, client statement, shareholders' agreement, estimate to confirm).

## What is enforced, and what is not

**100 % is not required.** A half-known structure is the normal case for a
prospect: refusing 60 % until the other 40 % is known would mean recording
nothing at all. The form shows "partial structure" and the drawing tints it;
nothing is refused.

What is refused:

| Entry | Why |
|---|---|
| a company owning itself | meaningless |
| a closed loop (A owns B owns A) | almost always owner and owned swapped |
| a percentage outside [0, 100] | SQL check |

Rows are partitioned by company: a record rule keeps one company's ownership
data out of another's reach, and the partition holds inside the **drawing**, not
only in the list.

## What gets drawn

From any company in the group, the chart walks **up** through owners and
**down** through holdings, eight levels deep. The company you are looking at
carries a thick border, without which you cannot tell which box you came from.
A non-voting stake is drawn as a dashed line.

## What it is not

It does not replace a corporation's share register. It draws what you know; it
is not evidence. The footer of every output says so.
