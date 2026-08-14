# Entry authoring

Paperstack indexes papers, talks, and posts together while giving each source type its own contract. Generate the
canonical scaffold rather than copying an existing entry:

```bash
paperstack review init <key> --kind paper --id arxiv:NNNN.NNNNN --title "Verbatim title" --editor <name>
paperstack review init <key> --kind talk --id <url> --title <title> --speaker <name> [--speaker <name>] --channel <name> --published YYYY-MM-DD --editor <name>
paperstack review init <key> --kind post --id <url> --title <title> --publisher <name> --published YYYY-MM-DD --editor <name>
```

The executable contracts and scaffolds live in `src/paperstack/entry_types.py`.

## Papers

```bash
paperstack review init <key> --id arxiv:NNNN.NNNNN --title "Verbatim title" --editor <name>
```

This initializes an ungraded scaffold only. The review itself remains a reading and judgment task. Review files are
stored internally as `entries/papers/<key>.md`. See the [paper review guide](#paper-review-guide) for editorial guidance.

- Name files `<first-author surname><arXiv v1 year><first significant title word>`, lowercase; suffix collisions with `a`, `b`, and so on.
- Use the established method name or full title as the `#` heading.
- Use a registered CURIE (`arxiv:`, `doi:`, `hdl:`, `isbn:`) for `id`, or a URL when none exists.
- `tags` are lowercase and singular. Reuse before inventing.
- Include only verified affiliations in `lab`. Use a person's name in `editor`, or `model effort (harness)` for an agent.
- Use CommonMark with GFM, including tables for tabular results.

Run `paperstack review check`; `paperstack review check --style` adds prose-length warnings.

### Getting the source in front of you

The source fetcher uses `latexpand` from PATH, falling back to a vendored copy via Perl.

```bash
paperstack paper read arxiv:2604.23073            # the complete LaTeX body
paperstack paper read arxiv:2604.23073 --outline  # the section outline
paperstack paper read arxiv:2604.23073 --section 6
paperstack paper pdf arxiv:cs/9301101            # when there is no LaTeX source
yt-dlp --skip-download --write-auto-subs --sub-lang en -o talk <url>
```

- Prefer the LaTeX source; use the PDF fallback only when no source exists.
- Cross-check malformed tables with `pdftotext -layout <pdf> -`.
- Treat commented-out results as evidence only when their surviving values match the published version; a mismatched baseline may be an earlier run.
- Strip timestamps and duplicate cues from video captions.

### Paper review guide

Read the body, then write the critical read, paper summary, reason to read, and one-liner, in that order.

- Keep the review body within 2,500 visible non-whitespace characters. Use prose for argument, bullets for independent points, and tables for repeated comparisons.
- Make the summary self-contained: a reader who has not read the paper should understand its core problem, approach, evidence, and findings. Choose the form that reads best; five bullets is one option, not a target.
- In the critical read, consult prior and subsequent work as needed, then focus on what matters for interpreting, trusting, or using the paper.
- Keep the one-liner to one sentence and `Why read it` to two. `Why read it` captures significance, originality, or practical value; use `none` when there is no reason.
- `Quality` measures how much of the title and abstract's main claim survives the evidence:
    - `excellent`: the claim stands and has lasting importance
    - `good`: the claim stands
    - `fair`: only a narrower claim stands
    - `poor`: the claim is not established
- Grade the advertised claim, not the narrower verdict. Use `fair` only when narrowing scope preserves the core claim; materially replacing it is `poor`.
- For SOTA or efficiency claims, check the strongest comparable result and name the denominator.
- If an official protocol fits the claim, an unjustified custom replacement caps `Quality` at `fair` unless matched, interpretable anchors restore comparability.
- Disclosed weaknesses still count when the paper claims past them. Side contributions do not raise the grade.
- On `fair` or `poor`, add `Read it anyway.` only with a checked citation count from Hugging Face or Semantic Scholar.

#### For robotics papers

- A benchmark counts only when success requires the claimed capability. LIBERO-only evidence caps Quality at `poor`; so does an uncounted real-world result added to it.
- Judge the hardest benchmark, note omissions, and account for benchmark age and test-set proximity.
- Treat margins within evaluation noise as ties; check SOTA claims against the [VLA Evaluation Harness](https://allenai.github.io/vla-evaluation-harness/leaderboard/).
- Recover exact values and trial counts where possible; otherwise state that they are unavailable.
- Compare baselines only under the same training and evaluation protocol.

### Canonical paper template

```markdown
---
id: arxiv:NNNN.NNNNN
title: verbatim
venue: only once accepted
lab: [only when the group is a signal]
project: only when it exists
github: only when it exists
quality: excellent | good | fair | poor
tags: [tag, tag]
editor: person name | "model effort (harness)"
---

# Short name

Keep the review body within 2,500 visible non-whitespace characters. Use prose,
bullets, subheadings, and tables according to the information.

**One-liner.** The paper in one sentence: what it is, not what is wrong with it. Written last.

**Why read it.** What a reader walks away with, or `none`. At most two sentences, naming what you take rather than explaining it.

**Read it anyway.** `fair` and `poor` only. Why the paper spreads regardless: hype, a competitor, a coming reference point. Delete this line otherwise.

## What it is / What it shows

Make this self-contained: a reader who has not read the paper should understand
its core problem, approach, evidence, and findings. Choose the form that reads
best; five bullets is one option, not a target.

## Critical read and limits

**Verdict.** State the narrowest claim that survives. Grade the advertised
title/abstract claim, not this narrower verdict.

Consult prior and subsequent work as needed. Surface what matters most for
interpreting, trusting, or using the paper; choose the structure and depth it
warrants.
```

## Talks

Talk entries live in `entries/talks/`. They are contextual records, not paper reviews, and therefore never carry
`quality`. A lecture or interview need not make a scientific claim.

Required metadata is `speaker`, `channel`, and `published`. Required prose is:

Repeat `--speaker` for talks with multiple speakers.

```markdown
**One-liner.** What the talk is.

**Why watch it.** What the viewer takes away.

## What it covers
## Useful ideas and context
## Notes and caveats
```

Separate the speaker's account from independently established evidence. Preserve useful framing, chronology, and
first-hand context; identify promotional framing, missing comparisons, or claims that cannot be checked from the talk.

## Posts

Post entries live in `entries/posts/` and never carry paper `quality`. Required metadata is `publisher` and
`published`. Required prose is:

```markdown
**One-liner.** What the post is.

**Why read it.** What the reader takes away.

## What it reports
## Useful details
## Provenance and caveats
```

Record the reusable technical details and the provenance or commercial constraints that shape the report.
Evidence-bearing claims should still be checked in prose without implying that the post passed through a paper review
process.
