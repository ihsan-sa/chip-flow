# chip-flow

Claude Code skills for chip design with open tools: an engine plus `/vde`, `/ade` and `/msde` under `skills/`.
`docs/design.md` is the plan and the contract; read the section your milestone names, not the whole thing.

- Every tool and script runs through `bin/eda` (`eda python <script>`), never the host's python or a docker image.
- Scripts keep one contract: JSON out, exit 0 pass, 1 findings, 2 error with a `remediation`, no prompts.
- A gate that did not run is a refusal, never a pass. Don't weaken a check to make a design pass.
- `tests/check.sh` is the landing gate: keep it under two minutes, and put slow runs in their own script - `tests/check-engine.sh` runs the pytest suite's non-`slow`-marked tests, `tests/check-slow.sh` runs the real-tool `slow` ones separately.
- This repo may go public: no person's name, host name, token or absolute home path in it.
