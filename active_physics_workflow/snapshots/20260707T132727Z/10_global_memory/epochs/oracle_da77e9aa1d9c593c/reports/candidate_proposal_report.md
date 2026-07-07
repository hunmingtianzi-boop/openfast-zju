# Global Loop Candidate Proposal

Generated: 2026-07-07T09:05:06+00:00

- Candidate id: `GLV2_RNA_BALANCE_20260707T090506Z0000`
- Parent: `GLV3_MOOR_EA1005_20260707T033530Z`
- Parameter group: `rna_balance`
- This command is intentionally not executed by the proposal mode:
  `python 03_scripts/03_generate_candidates.py --candidate GLV2_RNA_BALANCE_20260707T090506Z0000 --stage global_loop_v2_rna_balance --parent GLV3_MOOR_EA1005_20260707T033530Z --vector-json {"hydrodyn_edits":[],"moordyn":{},"initial_conditions":{"proposal_placeholder":"select RNA balance delta before registration"},"tmax":null,"decision_meta":{"global_loop_v2_group":"rna_balance","requires_full_sixdof":true}} --notes global_loop_v2 proposal group=rna_balance; full 6DOF validation required`
- Edit vector values before registration when the physical parameter delta is selected.
