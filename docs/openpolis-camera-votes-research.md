# How Openpolis builds Camera vote and person data

> Implementation status (2026-08-10): Policy Data now discovers every
> non-secret Camera detail URL from the official roll-call RDF, stores the HTML
> in its content-addressed cache with four bounded workers, solves the current
> deterministic Camera browser challenge without executing remote JavaScript,
> and uses the detail page to complete missing member positions.

Research date: 2026-08-10  
Scope: OpenParlamento XIX, the example vote `vs19_704_015`, and the Giuseppe
Valditara profile. Only first-party Openpolis code, APIs and pages, plus the
official Camera source they use, were considered.

## Executive conclusion

Openpolis does not appear to have a privileged Camera feed. Its current public
code implements a dual-path importer over official Camera sources:

1. load roll-call metadata from the Camera SPARQL endpoint;
2. query individual `ocd:voto` resources one roll call at a time;
3. join the official person URI to the parliamentary membership active on the
   date of the vote;
4. reject an underfilled Camera extraction;
5. run an official `documenti.camera.it` HTML importer to fill votes that are
   absent or incomplete;
6. compute group position, cohesion, rebel status, participation and profile
   metrics after the member votes have loaded.

That is the practical explanation for the gap in Policy Data Italia: the bulk
Camera RDF archive gives us roll-call metadata, but Openpolis goes back to the
per-vote official graph and, when necessary, the official HTML detail page to
obtain member-level positions.

The live official example supplied during implementation—sitting 699, vote
70—was verified directly at
`https://documenti.camera.it/apps/votazioni/votazionitutte/schedavotazione.asp?Legislatura=XIX&RifVotazione=699_70&tipo=dettaglio`.
After the Camera proof challenge it contains 398 named rows with the columns
`Nominativo`, `Gruppo`, and `Voto`, plus the official totals: 282 present, 279
voting, 3 abstentions, 114 yes, and 165 no. The project parser extracted that
live shape successfully without copying Openpolis data.

The reusable lesson is the acquisition and validation pattern, not Openpolis's
database itself. Openpolis publishes the service code under AGPLv3, while its
API page describes the exposed data as non-commercial, attribution-required
reuse. Policy Data Italia should therefore ingest the underlying parliamentary
sources directly and retain its own provenance rather than mirroring the
Openpolis API. See the [OPDM repository](https://gitlab.openpolis.io/openpolis/opdm/opdm-service)
and [Openpolis API terms and architecture](https://service.opdm.openpolis.io/about/).

## What is confirmed

### 1. The example page is server-rendered from a first-party JSON API

The HTML for [Openpolis vote `vs19_704_015`](https://parlamento19.openpolis.it/votazioni/vs19_704_015)
contains an Angular transfer-state response from:

```text
GET https://service.opdm.openpolis.io/
    api-openparlamento/v1/19/votings/vs19_704_015/
```

The public API returned, at research time:

- sitting 704, vote 15, Camera, 2026-08-05;
- the deterministic identifier `vs19_704_015`;
- 399 individual member rows;
- 12 group/coalition aggregate rows;
- 107 `AYE`, 168 `NO`, 3 `ABST`, 64 `ABSE`, 56 `MIS`, and one
  `PRES` row;
- the official aggregate totals, outcome, vote type, group cohesion, majority
  side, election area, and rebel flag.

The member rows sum to 399 and their recorded positions reconcile with the
published aggregates. The endpoint and its person/filter parameters are
documented in the [OPDM API documentation](https://service.opdm.openpolis.io/docs/):

```text
GET /api-openparlamento/v1/{legislature}/votings/{id}/
GET /api-openparlamento/v1/{legislature}/memberships_votes/?person={slug}
```

### 2. Camera member positions are queried per roll call from SPARQL

The current importer generates a Camera query scoped to one canonical vote URI:

```sparql
?voto a ocd:voto;
  ocd:rif_votazione
    <http://dati.camera.it/ocd/votazione.rdf/{votazione}>;
  dc:type ?vote;
  ocd:rif_deputato ?d.

OPTIONAL { ?voto dc:description ?infoAssenza }
```

It joins the deputy and mandate back to a `foaf:Person`. The implementation is
in Openpolis's [SPARQL query templates](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/management/commands/raw_sparql/template_query.py)
and [member-vote importer](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/management/commands/import_voti_sparql.py).
The configured Camera endpoint is `http://dati.camera.it/sparql` in the same
public service repository.

Openpolis explicitly documented the same interpretation in its
[Camera vote import issue](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/issues/69):
`dc:type` carries the position and `dc:description` supplies the absence detail
when the type says the member did not vote.

The importer maps more than yes/no/abstain. Its canonical states include:

```text
AYE   favourable              ABSE  absent
NO    contrary                MIS   mission
ABST  abstained               PRES  presiding
PNV   present, not voting     RNV   requested, not voting
SEC   participated in secret vote
```

### 3. Identity joins are temporal, not name-only

For the SPARQL path, Openpolis takes the official Camera person identifier and
selects the OPDM membership whose assembly and start/end dates cover the vote
date. This is the key join:

```text
official person identifier
  + Camera assembly
  + membership.start <= vote.date <= membership.end
  -> parliamentary membership used for MemberVote
```

This avoids assigning an old vote to the person's current group or to the wrong
mandate. The code is in the
[member-vote importer](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/management/commands/import_voti_sparql.py).

When serializing a vote, Openpolis separately resolves the membership's group
and majority-support period at the sitting date. The API's `group` and
`supports_majority` fields therefore describe the historical state, not the
member's present affiliation. See
[`Voting.members_votes_filtered`](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/models.py#L1472)
and the [`MemberVote` model](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/models.py#L1671).
The model also enforces uniqueness on `(membership, voting)`.

### 4. Openpolis rejects underfilled Camera SPARQL results

For a Camera vote, the importer compares the extracted row count with the
number of active Camera memberships on that date, excluding the President. If
the query returns fewer rows, the importer logs the problem and does not load
that result. There is one hard-coded historical exception for 2024-04-10.

It then assigns explicit `ABSENT` rows to active members not found in a valid
extraction, with a separate President rule. This behavior is visible in the
[same importer](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/management/commands/import_voti_sparql.py).

This completeness gate is important, but it is not sufficient provenance for
our platform: Policy Data Italia should also reconcile the per-position counts
to the official roll-call totals and expose the coverage decision publicly.

### 5. Official Camera HTML is a fallback and completer

Openpolis also has a dedicated scraper for the official Camera voting site. It:

1. searches `risultatidb.Asp` by legislature, date and vote nature;
2. follows each official `a.voto` detail link;
3. extracts `RifVotazione={sitting}_{vote}`;
4. reads totals and metadata from the detail page;
5. parses member rows from `table#tabellaPartecipazioneVoto`;
6. maps the Italian labels to the canonical vote states;
7. skips a roll call that already has both member and group votes;
8. calculates group votes and derived counts after loading the rows.

The complete implementation is public as
[`import_votazioni_camera_raw.py`](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/management/commands/import_votazioni_camera_raw.py).
The official detail URL shape is:

```text
https://documenti.camera.it/apps/votazioni/votazionitutte/
schedaVotazione.asp?Legislatura=19&RifVotazione={sitting}_{vote}&tipo=dettaglio
```

The HTML path uses name heuristics against the set of memberships active on
that date. That is weaker than the SPARQL person's stable identifier, which is
why it should remain a reconciled fallback rather than the primary identity
authority.

### 6. The refresh order explains the quality of the derived data

The current `update_legislature` command runs, in order:

1. legislative acts;
2. sittings;
3. parliamentary memberships and groups;
4. historical group membership and majority periods;
5. roll-call metadata;
6. Camera member-vote SPARQL pipeline;
7. Camera HTML fallback;
8. Senate member-vote pipeline;
9. group votes, cohesion, historical member/group metrics and caches.

Its default rolling refresh window is the previous two months through the
current day. The exact orchestration is in
[`update_legislature.py`](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/management/commands/update_legislature.py).

This confirms that Openpolis derives rebel, cohesion and attendance metrics
only after temporal group joins and individual vote ingestion have completed.

### 7. Their person page is a general political-person profile

The referenced [Giuseppe Valditara page](https://parlamento19.openpolis.it/persone/giuseppe-valditara-1961-01-12)
is backed by:

```text
GET https://service.opdm.openpolis.io/
    api-openparlamento/v1/19/persons/giuseppe-valditara-1961-01-12/
```

The response includes identity, birth date/place, image, current government and
parliament roles, career history, parliamentary positions, first-signer bills,
attendance and vote summaries, rebel/key/confidence vote samples, and power
metrics/timelines.

Valditara illustrates an important modeling point: he is a current minister,
not a current XIX-legislature MP. Accordingly, current parliamentary vote
metrics are null while his ministerial role, prior Senate career and government
bills are populated. A person page should describe the person's actual current
role before presenting parliamentary sections; it should not render a generic
empty “parliamentarian” profile.

## What is inferred, not confirmed

- The dual path is evidently designed so that Camera HTML repairs cases where
  per-vote SPARQL is missing or underfilled. The code order and skip conditions
  support this, but Openpolis does not document a formal source-precedence
  policy.
- The live vote's `created_at` and `updated_at` timestamps, together with the
  rolling refresh command, strongly indicate scheduled incremental operation.
  The public code does not reveal the production scheduler's exact cadence.
- The public OpenParlamento API response does not expose record-level source
  lineage for the sample vote. Its acquisition path is proven by the importer,
  but a consumer cannot follow a specific API fact to a content-addressed raw
  artifact. That is an opportunity for Policy Data Italia rather than a flaw we
  should copy.
- Some editorial fields and “key vote” decisions are curated. They should never
  be presented as official parliamentary facts without a distinct interpreted
  or editorial layer.

## Recommended implementation for Policy Data Italia

### Camera acquisition

Use the same official-source topology with stricter evidence handling:

```text
bulk Camera roll-call graph
        |
        +--> one bounded SPARQL request per public, non-secret roll call
        |      join by official person URI + active mandate date
        |
        +--> official HTML detail fallback when SPARQL is unavailable/underfilled
               match only within active mandate set
               quarantine ambiguous names

both paths --> reconcile identity set and position totals
           --> store immutable artifact + record-level lineage
           --> publish only complete/reconciled rows
```

Implementation requirements:

- deterministic key `camera:{legislature}:{sitting}:{vote}` with the official
  Camera URI retained as an external identifier;
- resumable queue keyed by roll-call ID, with retry/backoff and bounded
  concurrency for the official sites;
- immutable raw SPARQL JSON and HTML artifacts with hashes, retrieval time and
  source URL;
- exact source-to-canonical person mapping; name matching may propose a link but
  cannot silently resolve ambiguity;
- explicit coverage states such as `complete`, `partial`, `secret`,
  `source_unavailable`, `identity_ambiguous`, and `totals_mismatch`;
- yes/no/abstain reconciliation plus checks for mission, absence, president and
  active-mandate population;
- group and majority affiliation evaluated at `occurred_at`, never copied from
  current membership;
- idempotent upserts followed by derived-metric recomputation and cache
  invalidation.

### A better human profile

The profile should answer “who is this person and why do they matter now?”
before listing data tables. A useful first version should contain:

1. identity: photo, full name, birth date/place and verified external IDs;
2. current status: current office, chamber, mandate dates and whether the
   person is currently an MP, minister, former member, or candidate;
3. representation: election area/constituency and geographic scope;
4. affiliation: current group plus a dated group/party history;
5. accountability summary: participation denominator, present/absent/mission,
   rebel count, and the dates covered;
6. important and recent votes, always showing position, group at vote, measure,
   result, date and official evidence;
7. legislative work: bills, amendments, signatures, committee memberships and
   offices, with roles such as first signer made explicit;
8. financial disclosures with year, document type, source and extraction
   status;
9. a visible data-coverage panel stating what is present, missing, delayed or
   not applicable.

Empty states must distinguish “not applicable”, “not published by the source”,
“not yet ingested”, and “the person cast no recorded vote”. These are different
facts and agents must not collapse them.

### The agent-first layer

The website can be richer without becoming the authority. The API/MCP response
must be the stable product contract. At minimum, expose:

```text
resolve_person(name, birth_date?, external_id?)
get_person(person_id, at_date?)
list_person_mandates(person_id)
list_person_votes(person_id, from?, to?, topic?, vote_type?, position?)
get_roll_call(roll_call_id)
list_roll_call_positions(roll_call_id)
get_fact_evidence(fact_id)
get_coverage(entity_or_query)
```

Every answerable fact should return:

- canonical ID and the source identifiers used for resolution;
- the time interval in which roles and affiliations apply;
- the official measure and vote URLs;
- source artifact and record lineage;
- coverage/completeness state;
- derived/interpreted status and method version when applicable.

For a question such as “Which current candidates voted for Superbonus?”, the
agent must execute a visible join rather than search profile prose:

```text
interpreted policy topic -> official measures -> roll calls -> member positions
-> canonical people -> current candidacies -> user's constituency
```

The response must say which step is incomplete. In particular, “no row” cannot
mean “voted no”, “was absent”, “was not a member”, or “we lack Camera data”.

## Sources and reuse boundary

- [Openpolis OPDM service repository, AGPLv3](https://gitlab.openpolis.io/openpolis/opdm/opdm-service)
- [OPDM architecture, public API access and data reuse statement](https://service.opdm.openpolis.io/about/)
- [OPDM REST API documentation](https://service.opdm.openpolis.io/docs/)
- [Openpolis vote example](https://parlamento19.openpolis.it/votazioni/vs19_704_015)
- [Openpolis person example](https://parlamento19.openpolis.it/persone/giuseppe-valditara-1961-01-12)
- [Current Camera/Senate update orchestration](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/management/commands/update_legislature.py)
- [Per-vote official SPARQL importer](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/management/commands/import_voti_sparql.py)
- [Official Camera HTML fallback importer](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/management/commands/import_votazioni_camera_raw.py)
- [Vote models and historical affiliation joins](https://gitlab.openpolis.io/openpolis/opdm/opdm-service/-/blob/275b49b852df453b99d040a9495219c943f13c09/project/opp/votes/models.py)

The older [GitHub `openparlamento` repository](https://github.com/openpolis/openparlamento)
is useful historical context but is not the current XIX-legislature pipeline;
its last visible commit is from 2021. The findings above are based on the
current GitLab service and live first-party API.
