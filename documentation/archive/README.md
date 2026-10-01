# Archive

These are the original starter documents, kept for reference only. **Do not build from them.** The documents one level up replace them.

- `original-project-brief.md`: the first vision brief.
- `original-architecture-and-technical-spec.md`: the first technical spec. Its "Suggested phased build order" was dropped and replaced by [`../09-build-roadmap.md`](../09-build-roadmap.md).

The review that led to the rewrite is summarized in [`../10-decisions-log.md`](../10-decisions-log.md). The main problems it found:

1. Track 1 had no trainable targets ("trend score" and "perform well" were never defined).
2. The features were weak (raw points, yards and turnover margin) and nothing handled the start of the season.
3. Some example outputs needed data that isn't available for free (cornerback-vs-receiver matchups).
4. The plan for Track 2 to feed Track 1 assumed current-season frame-level tracking data, which isn't public.
5. The example graph queries didn't need a graph, and the schema had gaps.
6. The number-check step was too simple, and nothing checked the digest against what actually happened.
7. Operational details were missing: TNF timing, injury-report timing, byes and odd weeks, tools, fail-soft enrichment.
