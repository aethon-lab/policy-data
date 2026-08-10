# Data dictionary

| Entity | Meaning |
| --- | --- |
| `person` | Chamber- and legislature-independent human identity. |
| `source_identity` | Person identifier scoped to an official authority. |
| `mandate` | A person's service in one chamber and legislature. |
| `political_group` | Official parliamentary group in one chamber and legislature. |
| `membership` | Start-inclusive, end-exclusive mandate membership in a group. |
| `parliamentary_item` | Typed official subject such as a bill, amendment, law, or article. |
| `roll_call` | Official named/electronic vote in one sitting. |
| `vote` | Source-observed member position attached to a mandate. |
| `source_record` | Exact upstream record locator inside one immutable artifact. |
| `fact_lineage` | Link from a fact to source record and resolution rule. |
| `release` | Immutable normalized snapshot built from an artifact fingerprint. |

`not_recorded` is a query-time gap diagnostic and is not stored as a source
vote. Unknown official values are quarantined until their mapping is reviewed.
