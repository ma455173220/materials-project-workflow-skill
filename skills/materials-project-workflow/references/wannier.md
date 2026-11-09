# Wannier models and Wilson loops

Apply this when an analysis needs a Wannier tight-binding model: Berry-phase or topological quantities (Wilson loops, Chern or Z₂ indices), surface or edge spectra, or bands and properties on k meshes too dense for DFT. Whether to build the model, which energy range and features it must reproduce, and what it feeds downstream are the researcher's choices; record them in `WORKFLOW.md`.

Work in this order and let each step answer its question before the next.

## 1. What the target states are made of

Before choosing projections, use orbital-resolved projected bands to answer: which atoms and orbitals dominate the target range; which band groups are separated from all other bands at every k (frozen whole) and where entanglement starts; which semicore states lie far below with a clear gap (excluded); whether an isolated group's band count equals the candidate orbital count; and whether some states are not atom-centred.

- A matching band count is necessary, not sufficient: the group's irreducible representations at the high-symmetry points must also match those induced by the orbitals. When the representations do not match those of any choice of orbitals and centres, no Wannier functions respecting the required symmetry can be built from that group alone; a group with nonzero Chern number has no exponentially localised Wannier functions at all.
- Read the total in-sphere weight of a state before interpreting single channels. A clearly low total, compared with atom-like states in the same range, shows charge the atomic projectors describe poorly; projector ranges differ between elements, so compare with a reference state dominated by the same element where possible. It suggests, but does not prove, a non-atom-centred state; locate it (step 2) and let the fit decide. A small unexpected orbital weight that appears only on such low-weight bands may be the tail of that state, not a reason to add the orbital.
- Know how the code defines projections. In VASP with `LORBIT ≥ 10`, weights come from the PAW projectors, not from fixed spheres, so totals need not sum to one and "in-sphere" means projected weight; in non-collinear `PROCAR` files only the first block of each band is the total weight.
- Take the energy zero from the self-consistent run; the Fermi level printed by a non-self-consistent band-path run is not reliable.

## 2. Locating non-atom-centred states (when step 1 points to them)

Compute the band-resolved density of the suspicious band at several k points, compared with an atom-like band from the same range, to find where the charge concentrates; candidate sites need not be empty high-symmetry positions. Use several k points, because symmetry can force the amplitude at a site to zero at a single one. Trust a density only after checking that it is invariant under the symmetry operations that leave that k fixed; a density rebuilt from PAW pseudo-wavefunctions is meaningful only outside the PAW spheres. Whether the site needs a centre, and of which orbital character, is decided by the fit.

## 3. Projection schemes

Weigh the dominant orbitals; strongly hybridised partner orbitals (more functions, but a whole valence block can be frozen); orbitals that carry weight only on suspicious or high bands (they raise `num_wann` and the outer window and pull in free-electron, vacuum or ghost states); and centres for located non-atomic states.

- Place a centre on every site of its symmetry orbit, or symmetry and band counts break.
- With SOC each projection is a spinor pair. The quantisation axis does not change the disentangled subspace, interpolated bands or Wilson loops when both spin components of an orbital are projected, but know how the spin labels map to the DFT spin axis and to downstream conventions.
- Spin-polarised without SOC: fit each spin channel separately.
- Choose localisation by downstream use: without it (`num_iter = 0`) functions stay close to the projected orbitals and keep their character and symmetry; with it they are more localised. Without it, interpolation quality depends on how smooth the projection gauge is, and poor projections give spurious bands even when the subspace is smooth.
- When the choice is not clear, prepare a few schemes (minimal atomic set, with non-atomic centres, extended atomic set), export and fit each from the same wavefunctions, and choose by the step-6 checks rather than by expectation.
- Re-export the projection overlaps (`amn`) for every scheme. Changing projections in the `.win` file without re-exporting silently reuses the old ones when the function count is unchanged.

## 4. Bands and windows

Fixed at export (changing them means re-exporting): the k mesh, the number of bands, excluded bands, the projections and the function count. Adjustable in the Wannier step alone: windows, iteration settings, interpolation and output. Overlaps and eigenvalues can be shared between schemes only when they come from the same stored wavefunctions with the same k mesh, band count and excluded bands; a rerun of the non-self-consistent calculation changes the wavefunction phases, and mixing new projections with old overlaps gives no error.

- Exclude only semicore bands well below the model with a clear gap. Choose the DFT band count so the highest band lies above every outer window being considered at every k, with margin, because the top bands of an iterative diagonalisation converge worst; confirm the highest band actually used is converged. Leaving the outer window open makes the last computed band its edge, so the margin matters more.
- Hard constraints, checked at every k of the actual Wannier mesh, never on a band path: the frozen window holds at most `num_wann` states and the outer window at least `num_wann`.
- Within the constraints, two inexpensive choices are worth trying: the largest allowed frozen window (widest exact reproduction, at the cost of forcing in weakly projected states) and one common frozen window across schemes (compares which scheme describes the same states best).
- An outer-window edge cut through a dense set of states can make the subspace non-smooth between k points.

## 5. Export

- Use the full uniform k mesh with no symmetry reduction. In VASP this means `ISYM = -1`: `ISYM = 0` still reduces k points by time reversal.
- In VASP, every run that writes Wannier data (one-step or two-step) needs `NCORE = 1`: with `NCORE > 1` the wavefunction components are split across ranks and the overlap (`mmn`) step fails, a long-standing limitation of the interface. This overrides any site default for `NCORE` in that run only.
- Check that the `.win` k-point list and order match the calculation, not just the count. VASP does not check a k-point block already present in a user-written `wannier90.win`.
- The mesh must be dense enough for the hoppings to decay within the corresponding supercell; check the decay of |H(R)| with R.
- When exporting from stored wavefunctions, every setting that defines them (structure, potentials, cutoff, k points, band count, symmetry and spin settings) must match the run that wrote them, and the band count the code actually used must equal the stored one; without re-diagonalisation nothing corrects a mismatch, and some mismatches raise no error.
- Matching exported eigenvalues with the DFT output only shows the eigenvalues were copied; it does not prove the wavefunctions or k list are right. The end-to-end check is the off-mesh comparison in step 6.

## 6. Fit diagnostics and validation

Read the spread decomposition, each function's centre and spread, and disentanglement convergence first.

- Ω_I is gauge-invariant and measures subspace smoothness; compare it per function on the same mesh. Disentanglement is iterative and can stop in a local minimum. Ω_D and Ω_OD depend on the gauge: compare projection-only results with projection-only results; with localisation on, only their sum is minimised and either term can rise.
- Symptom, cause and response:
  - the Wannier step stops on a window error: a hard constraint fails at some k; recompute the limits from the mesh eigenvalues;
  - exact on the mesh but oscillating spurious bands between mesh points: missing or wrong projections (in projection-only runs a non-smooth gauge, large Ω_D + Ω_OD) or a non-smooth subspace (for example an outer window cut through dense states); check projections and windows first;
  - a few functions with much larger spreads than the rest: often some states lack a suitable centre (find it with steps 1 and 2), or the outer window takes in free-electron or vacuum states;
  - disentanglement reaches its iteration limit: judge by the change of Ω_I per step whether the residual matters before raising the limit or revisiting windows;
  - small mean error but wrong small features: judge the model on the features its downstream use needs.
- Validation:
  1. On the mesh, states inside the frozen window are reproduced.
  2. Off the mesh (band path or dense probe points), compare with DFT from the same charge or wavefunctions and report the mean and maximum deviation in the energy range of interest. Agreement on the mesh holds by construction only for states inside the frozen window and cannot reveal spurious bands; this is the decisive check.
  3. Features the downstream analysis needs (smallest direct gap, band overlaps) are reproduced. A feature smaller than the model's off-mesh error or the DFT numerical noise cannot be decided by the model. Differences between quantities that symmetry makes exactly equal estimate only a lower bound of that noise.
  4. Interpolate with the Wigner–Seitz distance correction, and check that downstream programs read the same model: some read only the Hamiltonian file and ignore the correction, and orbital, spinor and centre ordering conventions must match the Wannier output.
  5. Centres and spreads are physically reasonable, and spin-channel or symmetry relations hold where they apply.

## 7. Wilson loops and invariants

- From the (magnetic) symmetry, decide first which invariants are defined and which are already fixed. Z₂ needs an antiunitary operation that maps k to −k and squares to −1 on the relevant momenta; time reversal combined with a fractional translation qualifies only on the planes where that holds. An operation that maps the plane onto itself and reverses the Berry curvature there forces C = 0 on that plane; one that relates two different planes only relates their values.
- Define the occupied set by model band index, with a nonzero direct gap to the bands above at every k; find the smallest direct gap on a dense mesh first. A global indirect overlap does not stop the calculation, but then C is a property of that band set, not a quantised Hall conductance. Invariants add over band groups separated by a direct gap at every k (C as integers, Z₂ modulo 2), so a model that excludes lower bands gives the invariant of the modelled bands only: state the assumption that the excluded bands are trivial, or check the full DFT occupied set with a [symmetry-eigenvalue criterion](topology-symmetry.md).
- Compute Wannier charge centres along one reciprocal direction as a function of another; without time reversal follow the full period. Refine adaptively, especially near small direct gaps, and test convergence of both meshes and of any thresholds. Use one embedding convention consistently with the program in use: individual centres depend on whether the orbital positions inside the cell enter the Bloch phases, while C and Z₂ do not. When feasible, cross-check with an independent implementation by comparing individual centres, not only C, after confirming both use the same convention.
- At each k₂, sum the centres of all occupied bands; the sum is defined modulo one lattice period. Unwrap it as k₂ runs over one reciprocal period: its net change, in lattice periods, is C. Unwrapping assumes the sum changes by less than half a lattice period between neighbouring k₂, which only refinement supports. The sign depends on convention. Track every centre continuously before interpreting a plot.
- Where an antiunitary operation defines Z₂ on the plane (see above), Z₂ needs only half the k₂ period, between the two invariant k₂ where the centres are pairwise degenerate. Z₂ is the parity of the number of times the centres cross a reference line of constant centre position, or equivalently of the number of centres the midpoint of the largest gap between neighbouring centres jumps over. Refine wherever a centre approaches the line or the largest gap becomes small, and check that moving the line does not change the parity. With time reversal in 3D, compute the six planes kᵢ = 0 and π (reduced primitive coordinates): the weak indices are the Z₂ of the kᵢ = π planes, and the strong index Z₂(kᵢ = 0) + Z₂(kᵢ = π) mod 2 must come out the same for all three i; a mismatch shows an error.
- Optionally, the eigenvalues of the two-fold axis normal to the plane in which C is computed, at its four invariant momenta, give the parity of C (Fang, Gilbert and Bernevig 2012) as a cross-check; inversion and other symmetry-eigenvalue criteria are in the [symmetry-eigenvalue guidance](topology-symmetry.md).

## Tools

Interfaces and downstream programs have version-dependent defaults and failure modes, and some fail silently (an input block ignored, a default kept, a value printed where it is undefined). Before trusting a tool on a new system or version, confirm what it actually read from its own log and test it on a case with a known answer. Record version-specific problems in the project's own notes, not in this Skill.

Sources: [VASP LWANNIER90](https://vasp.at/wiki/LWANNIER90), [VASP LORBIT](https://vasp.at/wiki/LORBIT), [VASP ISYM](https://vasp.at/wiki/ISYM), [VASP forum: crash computing MMN with NCORE>1 (2021)](https://vasp.at/forum/viewtopic.php?p=20670), [Wannier90 user guide](https://wannier90.readthedocs.io/), [Marzari et al., Rev. Mod. Phys. 84, 1419 (2012)](https://doi.org/10.1103/RevModPhys.84.1419), [Soluyanov and Vanderbilt, Phys. Rev. B 83, 235401 (2011)](https://doi.org/10.1103/PhysRevB.83.235401), [Yu, Qi, Bernevig, Fang and Dai, Phys. Rev. B 84, 075119 (2011)](https://doi.org/10.1103/PhysRevB.84.075119), [Fu, Kane and Mele, Phys. Rev. Lett. 98, 106803 (2007)](https://doi.org/10.1103/PhysRevLett.98.106803), [Fang, Gilbert and Bernevig, Phys. Rev. B 86, 115112 (2012)](https://doi.org/10.1103/PhysRevB.86.115112).
