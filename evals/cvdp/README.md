# CVDP: the public number

`run.py` scores NVIDIA's CVDP v1.1 benchmark natively under `bin/eda`
(docs/design.md section 3). It is not the official harness, and a leaderboard
submission needs the docker harness, which is out of scope.

## What is pinned

- The dataset: `nvidia/cvdp-benchmark-dataset` on Hugging Face at revision
  `5b807d945f6a99aa645f7e43a64a2115e281b4bf`, file
  `cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl` (302 problems),
  checked by sha256 before use.
- The positive control: `NVlabs/cvdp_benchmark` at commit
  `8e894cf74414ab1eaea1e2b4e80a02f123df07b6`, the one public non-agentic
  example that ships its solution (`--dataset example`).

Both are cached outside the repo, in `$CVDP_CACHE` or else `$XDG_CACHE_HOME/chip-flow/cvdp`. A
`--dataset-file` run is recorded as unpinned.

## The subset rule

A problem is in the subset when all of these hold (`exclusion()` in `run.py`
is the code, and its reason names are the ones below):

1. Its first category is a code-generation one: cid002 code completion, cid003
   spec to RTL, cid004 code modification, cid007 code improvement or cid016 bug
   fixing (`category`).
2. Every docker-compose service uses the stock open-source image
   (`__OSS_SIM_IMAGE__` or `__OSS_PNR_IMAGE__`), or a Dockerfile that only adds
   pytest on top of it (`image`, `dockerfile-extra`, `dockerfile-missing`,
   `compose`).
3. Every service's command is one plain `pytest` or `python3` call, with no
   `&&`, `;` or pipe (`command`).
4. The env file does not set a simulator other than Icarus (`simulator`).
5. No live harness code under `src/` names a commercial tool (`commercial-tool`)
   or Verilator (`verilator`). A helper function nothing calls does not count.
   Yosys is allowed, because `eda` has it.

At the pinned revision that keeps 277 of 302: 24 need Verilator and 1 needs a
compound command. `--select-only` prints the current split.

## What a run scores

Each problem's services run in a scratch directory with container paths
(`/code`, `/src`, `/rundir`) rewritten into it. A problem passes when every
service exits 0 and its cocotb tests ran and passed. The result, dated under
`evals/results/cvdp/`, has the pass rate overall and by category, the subset
size, the mode and the caveat.

The public set ships no reference solutions, so the mode says what was scored:

- `solutions`: files from `--solutions DIR`, laid out `DIR/<problem id>/<path>`.
  This is the model score; a session writes the answers, the runner scores them.
- `null`: each problem's own input, unchanged. This is a floor, and
  `tests_ran` says how many problems the native harness reached.
- `borrowed` (`--borrow`): each problem gets the working RTL that a sibling
  problem of its family ships. A pass shows the native harness can pass that
  problem; a fail may just be the older code not meeting the newer spec.
- `reference`: the rows' own solutions, which only `--dataset example` has.

Exit 0 means every problem reached a verdict, 1 means some hit a runner error
(each is a finding), and 2 means the run could not start.

## Reading it beside the leaderboard

The design says the ladder page shows this number beside the leaderboard
figures the project proposal quotes. Those figures are not in this repo yet,
so they belong here when they are copied in, with their source. Read them with
care either way: the official harness runs the whole non-agentic set in its
containers, and this runner scores a subset natively, so the two are not the
same measurement.
