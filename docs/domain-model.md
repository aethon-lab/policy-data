# Domain model

The core separates a canonical `person` from authority-scoped source identities
and chamber/legislature-scoped mandates. Parliamentary group membership and
member votes attach to a mandate, so historic facts never inherit a person's
current affiliation.

A roll call links to typed parliamentary items. Items can represent bills,
amendments, articles, decree-laws, and enacted laws, and can carry official URLs
and typed relations. This provides the stable seam for later policy categories
and impact analysis without changing roll-call identity.

Future election records attach candidacies to the canonical person through a
reviewed source identity. Elections, constituencies, and policy interpretation
remain outside the first release; the parliamentary schema does not encode them
as inferred vote facts.

Every exposed fact resolves through `fact_lineage` to exact source records and
content-addressed artifacts. Source, normalized, derived, and interpreted facts
are distinct epistemic layers.
