# Global Loop Candidate Proposal

Generated: 2026-07-07T02:55:13+00:00

- Candidate id: `GLV2_MOORING_20260707T025513Z0000`
- Parent: `HEAVE095_ROLL090_CMP_0612144710`
- Parameter group: `mooring`
- This command is intentionally not executed by the proposal mode:
  `python 03_scripts/03_generate_candidates.py --candidate GLV2_MOORING_20260707T025513Z0000 --stage global_loop_v2_mooring --parent HEAVE095_ROLL090_CMP_0612144710 --vector-json {"hydrodyn_edits":[],"moordyn":{"proposal_placeholder":"select physical mooring delta before registration"},"initial_conditions":{},"tmax":null,"decision_meta":{"global_loop_v2_group":"mooring","requires_full_sixdof":true}} --notes global_loop_v2 proposal group=mooring; full 6DOF validation required`
- Edit vector values before registration when the physical parameter delta is selected.
