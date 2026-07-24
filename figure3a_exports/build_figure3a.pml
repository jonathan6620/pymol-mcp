reinitialize
load /Users/jward/code/pymol-mcp/figure3a_exports/8PMF.pdb, fig3a

bg_color white
set orthoscopic, on
set ray_trace_mode, 1
set ray_trace_color, black
set ray_trace_gain, 0.14
set ray_trace_disco_factor, 0.05
set antialias, 2
set ambient, 0.48
set direct, 0.52
set specular, 0
set reflect, 0
set ray_shadows, off
set depth_cue, 0
set ray_trace_fog, 0
set cartoon_sampling, 14
set cartoon_flat_sheets, on

hide everything, all

select protein_helix, fig3a and chain A and resi 270-294
select recognition_sidechains, fig3a and chain A and resi 278+282+285+289 and (sidechain or name CA)
select dna_backbone, fig3a and (chain B or chain D) and resi 3-10 and name P+OP1+OP2+O5'+C5'+C4'+C3'+O3'
select featured_bases, fig3a and ((chain B and resi 7+8) or (chain D and resi 5+6))
select featured_base_atoms, featured_bases and not name P+OP1+OP2+O5'+C5'+C4'+C3'+O3'
select ordered_waters, fig3a and resn HOH and ((chain B and resi 111+127+142+144+189) or (chain A and resi 335) or (chain D and resi 159+167))

show cartoon, protein_helix
color lightpink, protein_helix

show sticks, recognition_sidechains
set stick_radius, 0.14, recognition_sidechains
color lightpink, recognition_sidechains

show ribbon, dna_backbone
set ribbon_width, 1.3
set ribbon_radius, 0.16
color orange, dna_backbone

show sticks, featured_base_atoms
set stick_radius, 0.12, featured_base_atoms
color lightpink, featured_base_atoms
color red, featured_base_atoms and elem O
color blue, featured_base_atoms and elem N

show spheres, ordered_waters
set sphere_scale, 0.22, ordered_waters
color red, ordered_waters

distance water_chain_1, fig3a and chain B and resi 111 and resn HOH, fig3a and chain B and resi 189 and resn HOH
distance water_chain_2, fig3a and chain B and resi 189 and resn HOH, fig3a and chain B and resi 142 and resn HOH
distance water_chain_3, fig3a and chain B and resi 111 and resn HOH, fig3a and chain A and resi 335 and resn HOH
distance water_chain_4, fig3a and chain A and resi 335 and resn HOH, fig3a and chain B and resi 127 and resn HOH
distance water_chain_5, fig3a and chain B and resi 127 and resn HOH, fig3a and chain B and resi 144 and resn HOH
distance contact_a7, fig3a and chain B and resi 127 and resn HOH, fig3a and chain B and resi 7 and name N7
distance contact_c8, fig3a and chain B and resi 144 and resn HOH, fig3a and chain B and resi 8 and name N4
distance contact_thr278, fig3a and chain B and resi 142 and resn HOH, fig3a and chain A and resi 278 and name OG1
distance bridge_a7, fig3a and chain D and resi 159 and resn HOH, fig3a and chain B and resi 7 and name N6
distance bridge_g17, fig3a and chain D and resi 159 and resn HOH, fig3a and chain D and resi 5 and name O6
distance bridge_water, fig3a and chain D and resi 159 and resn HOH, fig3a and chain D and resi 167 and resn HOH
distance contact_thr285, fig3a and chain D and resi 167 and resn HOH, fig3a and chain A and resi 285 and name O

set dash_color, grey30
set dash_width, 2.0
set dash_gap, 0.28
set dash_length, 0.16
hide labels, water_chain_1
hide labels, water_chain_2
hide labels, water_chain_3
hide labels, water_chain_4
hide labels, water_chain_5
hide labels, contact_a7
hide labels, contact_c8
hide labels, contact_thr278
hide labels, bridge_a7
hide labels, bridge_g17
hide labels, bridge_water
hide labels, contact_thr285

label fig3a and chain A and resi 278 and name CA and not alt B, "Thr278"
label fig3a and chain A and resi 282 and name CA and not alt B, "Asn282"
label fig3a and chain A and resi 285 and name CA and not alt B, "Thr285"
label fig3a and chain A and resi 289 and name CA and not alt B, "Arg289"
label fig3a and chain B and resi 7 and name C1' and not alt B, "A7"
label fig3a and chain B and resi 8 and name C1' and not alt B, "C8"
label fig3a and chain D and resi 5 and name C1' and not alt B, "G17"
label fig3a and chain D and resi 6 and name C1' and not alt B, "T18"
set label_size, 9
set label_color, black
set label_outline_color, white

delete protein_helix
delete recognition_sidechains
delete dna_backbone
delete featured_bases
delete featured_base_atoms
delete ordered_waters
deselect

orient fig3a and ((chain A and resi 270-294) or ((chain B or chain D) and resi 3-10))
turn z, 70
zoom fig3a and ((chain A and resi 276-290) or ((chain B or chain D) and resi 4-9)), 0
viewport 1200, 1000

png /Users/jward/code/pymol-mcp/figure3a_exports/figure3a_proof.png, width=1200, height=1000, dpi=150, ray=1
png /Users/jward/code/pymol-mcp/figure3a_exports/figure3a_BARHL2_AC_8PMF_3200x2400.png, width=3200, height=2400, dpi=300, ray=1
save /Users/jward/code/pymol-mcp/figure3a_exports/figure3a_BARHL2_AC_8PMF.pse
