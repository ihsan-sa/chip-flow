# nested_not_released (release)

An msde release refuses unless both nested workspaces - `digital/` (skill
vde) and `analog/` (skill ade) - exist, carry the right skill, and still
verify as released (`attest.py verify`). The finding's module is
`nested_digital` or `nested_analog`; its message gives the reason.

**What to do:** re-enter that side. `no nested workspace` means P2 never
ran for it. `not released` means it never released, or something changed
after it did - an edit to its RTL or netlist, an `interface_edit`
cascade's `spec_edit`, a re-harden. Drive that side through its own
router (`task_router.py --skill <vde|ade> --verb resume --workspace
<ws>/<side>`) to a fresh release, then re-run the top gates the change
touched, then `release` here.

**Trap:** a stale nested release is the planted fault this gate must
catch. Never waive it, and never re-run the nested `attest.py build`
without re-running the gates it attests.
