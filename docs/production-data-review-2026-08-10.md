# Production data review — 2026-08-10

Release: `release-992a92189d651efd51a5977f`

This review was run against the activated production SQLite release after its
manifest, checksums, foreign keys, and database integrity had passed validation.

## What is available?

| Chamber | Roll calls | Date range | Complete named-vote coverage |
| --- | ---: | --- | ---: |
| Camera | 19,253 | 2022-10-25–2026-08-06 | 0 |
| Senato | 8,102 | 2022-10-26–2026-08-05 | 1,812 |

The release contains 254,420 reconciled individual Senate votes, 668 people,
and 296,639 fact-lineage records. Camera roll calls include official totals and
source links, but are marked `partial` because the bulk archive does not include
individual deputy positions. Senate queries capped by the official endpoint are
also marked `partial`; their incomplete member rows are not published.

## Do individual counts reconcile?

Yes for records marked `complete`. Three recent examples:

| Date | Vote | Official yes/no/abstain | Stored yes/no/abstain |
| --- | --- | --- | --- |
| 2026-08-05 | DDL 2001, confidence vote | 104 / 62 / 1 | 104 / 62 / 1 |
| 2026-08-05 | MEF communications, resolution 3 | 98 / 60 / 2 | 98 / 60 / 2 |
| 2026-08-03 | Final vote | 81 / 0 / 50 | 81 / 0 / 50 |

## Can it answer “which current candidates voted for Superbonus?”

Not yet. A literal search for `superbonus` and `bonus 110` in the current
official roll-call titles and descriptions returns no records. The release also
does not yet contain election candidates, constituencies, or voter-location
mapping.

The schema is ready to link people, mandates, parliamentary items, roll calls,
and future candidacies without rewriting historical votes. The next data work is
therefore:

1. link roll calls to bills, amendments, and law texts;
2. tag the legislative measures that created or changed Superbonus;
3. acquire complete Camera member details through a respectful, resumable job;
4. ingest official candidate and constituency data; and
5. add reviewed crosswalks between parliamentary people and candidates.

Until those joins exist, the API must not infer candidate identity, electoral
eligibility, geography, or a Superbonus position from names alone.
