# Contributing improvements learned from sessions

Raw PyMOL sessions are private inputs, not repository contributions. Distill a
session locally into a small lesson package that explains the failure, points at
a public or synthetic benchmark, and contains no prompts, paths, coordinates,
renders, or raw history.

## Promotion process

1. Capture the complete session under the private evaluation root.
2. Extract candidate findings locally.
3. Reproduce each finding with a public accession or synthetic fixture.
4. Write a lesson in `benchmarks/lessons/` and a scenario in
   `benchmarks/scenarios/`.
5. Review the complete lesson payload and set `reviewed_for_release` only after
   that review.
6. Run the privacy validator and public benchmark.
7. Submit the lesson, regression scenario, implementation change, and
   before/after public result. Never submit the source session.
8. Evaluate the candidate locally against held-out private scenarios and share
   only aggregate deltas.

The lesson schema is `benchmarks/schemas/lesson-package-v1.json`. Runtime
validation is deliberately stricter than path redaction: public lessons reject
absolute paths and molecular/session filenames, and their privacy declaration
must state that no raw artifacts are present.

## Results

Detailed benchmark results remain local. Use
`pymol_mcp.lessons.public_result` to produce a shareable projection containing
only boolean checks, aggregate trajectory counters, safe image metrics, and the
PyMOL version. Use `compare_results` to report improvements and regressions
without exposing either run's private diagnostics.

The same operations are available from the command line:

```bash
uv run python -m pymol_mcp.lessons validate benchmarks/lessons/LESSON.json
uv run python -m pymol_mcp.lessons publish \
  benchmarks/artifacts/SCENARIO/results/detailed.json \
  --lesson benchmarks/lessons/LESSON.json \
  --output benchmarks/artifacts/SCENARIO/results/public.json
uv run python -m pymol_mcp.lessons compare \
  benchmarks/artifacts/SCENARIO/results/baseline-public.json \
  benchmarks/artifacts/SCENARIO/results/candidate-public.json \
  --output benchmarks/artifacts/SCENARIO/results/comparison.json
```

The comparison includes per-check regressions/improvements and deltas for safe
trajectory counters. Negative render-iteration or camera-change deltas indicate
a more efficient workflow when final correctness is unchanged.

The BARHL2 Figure 2b package is the first example. It records five independent
lessons: camera-side ambiguity, missing alternate-conformer backbone traces,
GUI camera capture, replay source failure, and renderer byte instability.
