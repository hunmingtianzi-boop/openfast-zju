# Formal Peak/Phase/Leak Diagnostics

Candidate: `AMP_SH_HAF15_Y15052_R8715_07041136`

- Summary CSV: `D:\OpenFast\FOCAL_C4_physics_workflow\07_reports\formal_peak_phase_leak_diagnostics_0704.csv`
- Peak pair CSV: `D:\OpenFast\FOCAL_C4_physics_workflow\07_reports\formal_peak_pairs_0704.csv`

## DOF Summary

### FD_SURGE
- decay start model/ref: -19.142 / -23.2154; abs ratio=0.825; raw time shift=-0.050s
- first peak abs ratio=0.824; first peak dt=0.020s; mean abs dt first 5=30.072s
- mean peak abs ratio first 3=1.047; eq delta=3.02811 m
- top model leak ratios: `[["Sway",0.24887989402257485],["Roll",0.18951827006147925],["Heave",0.1567269156785698]]`
- energy quarters ref: `[1.0,0.04628404605879527,0.010464431664839399,0.003996590626692693]`
- energy quarters model: `[1.0,0.028391904269278165,0.005975229615075032,0.014852368711191827]`

### FD_SWAY
- decay start model/ref: -14.869 / -15.1099; abs ratio=0.984; raw time shift=-0.050s
- first peak abs ratio=0.997; first peak dt=0.820s; mean abs dt first 5=22.518s
- mean peak abs ratio first 3=1.417; eq delta=2.68359 m
- top model leak ratios: `[["Yaw",0.4087523755462862],["Surge",0.3057201275836692],["Roll",0.2387807118067128]]`
- energy quarters ref: `[1.0,0.07156207379202753,0.020459982928428863,0.007484741167852371]`
- energy quarters model: `[1.0,0.04218063683112672,0.019626333252557096,0.00824320529667704]`

### FD_HEAVE
- decay start model/ref: -9.001 / -8.2867; abs ratio=1.086; raw time shift=-0.050s
- first peak abs ratio=1.085; first peak dt=0.020s; mean abs dt first 5=0.454s
- mean peak abs ratio first 3=0.790; eq delta=4.5395 m
- top model leak ratios: `[["Roll",0.8800282503026133],["Pitch",0.46144151735615],["Sway",0.4298704030927173]]`
- energy quarters ref: `[1.0,0.01906631150656196,0.005368303767421463,0.0020039300025677382]`
- energy quarters model: `[1.0,0.02106381423010141,0.007475736561621981,0.0036560719968770083]`

### FD_ROLL
- decay start model/ref: 14.265 / 11.9597; abs ratio=1.193; raw time shift=-0.050s
- first peak abs ratio=1.186; first peak dt=0.020s; mean abs dt first 5=11.164s
- mean peak abs ratio first 3=1.217; eq delta=2.5565 deg
- top model leak ratios: `[["Sway",0.46942437968411854],["Surge",0.2777032104131175],["Heave",0.24580365053434305]]`
- energy quarters ref: `[1.0,0.05207422092186064,0.007681059731073641,0.0014126768381752318]`
- energy quarters model: `[1.0,0.0695129928118171,0.019162891155432682,0.005712976708602027]`

### FD_PITCH
- decay start model/ref: -10.4907 / -9.03115; abs ratio=1.162; raw time shift=-0.050s
- first peak abs ratio=1.156; first peak dt=0.010s; mean abs dt first 5=0.420s
- mean peak abs ratio first 3=1.096; eq delta=0.849765 deg
- top model leak ratios: `[["Surge",0.46336479581854095],["Roll",0.34124397882879426],["Heave",0.2849426010003921]]`
- energy quarters ref: `[1.0,0.14383903303704684,0.04079882896548281,0.013148705604688735]`
- energy quarters model: `[1.0,0.12147102477653615,0.05183439094303145,0.019026251724699968]`

### FD_YAW
- decay start model/ref: -14.5032 / -18.0201; abs ratio=0.805; raw time shift=-0.050s
- first peak abs ratio=0.805; first peak dt=0.010s; mean abs dt first 5=0.774s
- mean peak abs ratio first 3=0.843; eq delta=-0.6816 deg
- top model leak ratios: `[["Surge",0.4539930227576798],["Roll",0.2669873885688345],["Heave",0.21016781255470618]]`
- energy quarters ref: `[1.0,0.06961501905117892,0.022775764677072385,0.010254662707638371]`
- energy quarters model: `[1.0,0.07799823421018436,0.04071769711294512,0.016910591599826393]`
