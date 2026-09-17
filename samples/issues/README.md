# Deliberately-Flawed Lab (network-only)

A second, self-contained Terraform deployment that plants classic Azure
networking mistakes, so you have a topology worth reading the diagram for.
Separate directory = separate state, independent of the clean `samples/` lab.

Deploy this **after** you've confirmed a clean scan works on the main sample.
Tear down the clean sample first if you don't want both live at once.

## Deploy

```bash
cd samples/issues
tofu init        # or: terraform init
tofu plan
tofu apply       # type yes
```

Then in Azure Mapper: scan the subscription and read the resulting diagram.
The resource-group filter helps here — isolating `LAB-HUB-RG` makes the
one-way peering and the missing spoke-to-spoke links easy to see.
Tear down with `tofu destroy` when finished.

## The planted issues (your answer key)

Use this to check what you can spot in the diagram. Every issue is detectable
from topology alone — no NSGs or route tables involved.

| # | Issue | Where | Category |
|---|-------|-------|----------|
| 1 | **Overlapping address space** — `isolated-vnet` (10.20.0.0/24) sits inside `SpokeA_VNET` (10.20.0.0/16); they can never be peered without re-addressing | isolated-vnet vs SpokeA_VNET | addressing |
| 2 | **One-way peering** — `hub -> spoke-c` exists but `spoke-c -> hub` is missing, so the link never reaches "Connected" and traffic doesn't flow | hub-vnet → spoke-c-vnet | peering |
| 3 | **Transitive-routing trap** — `SpokeA` and `spoke-b` both peer to the hub but not to each other; Azure peering is **not** transitive, so A↔B traffic silently fails | SpokeA_VNET, vnet-spoke-b | peering |
| 4 | **Orphaned VNets** — `isolated-vnet` and `empty-vnet` have no peerings at all | isolated-vnet, empty-vnet | design |
| 5 | **Empty VNet** — `empty-vnet` has address space but no subnets | empty-vnet | design |
| 6 | **Wasteful sizing** — `oversized-vnet` is a /12 holding a single /29 subnet | oversized-vnet | addressing |
| 7 | **Inconsistent naming** — `LAB-HUB-RG` vs `lab_spokes_rg`; `hub-vnet` vs `SpokeA_VNET` vs `vnet-spoke-b` vs `spoke-c-vnet` | resource groups, VNets | naming |

## Healthy elements (should NOT be flagged as broken)

- `hub <-> SpokeA`, `hub <-> spoke-b`, `hub <-> oversized` are all complete
  bidirectional peerings. These render as green double-headed edges — the
  hub-and-spoke pattern working as intended, not something broken.

## Note

All resources deploy cleanly. Overlapping address space (#1) is only used
between VNets that are **not** peered — Azure rejects peerings between
overlapping VNets, so the overlap is a latent design problem rather than a
deploy-time error: nothing fails until someone tries to connect the two.
