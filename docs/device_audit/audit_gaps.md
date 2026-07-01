# SMI device-completeness audit

_CSS screens vs profile devices vs baseline._  

- CSS files parsed: **180**, resolved CSS records: **6765** (+411 unresolved)

- Profile records: **10204** (baseline: **1611**), instances: 137, skipped: 18

- Total reconciled records: **16484**


## Totals by category

| Category | Records |
|---|---:|
| On screen, NOT modelled by profile (gap) | 6280 |
| On screen + modelled, NOT in baseline (capture candidate) | 110 |
| On screen + modelled + baselined (covered) | 375 |
| In baseline, not on any audited screen | 1236 |
| Modelled only (not screened, not baselined) | 8483 |

## Biggest gaps (`css_only`) and capture candidates (`profile_not_baseline`) by subsystem

| Subsystem | css_only | profile_not_baseline | covered | total |
|---|---:|---:|---:|---:|
| mana | 1643 | 14 | 47 | 1704 |
| ct | 929 | 1 | 0 | 930 |
| es | 866 | 44 | 0 | 910 |
| op | 896 | 13 | 245 | 1154 |
| diagnostics | 885 | 0 | 0 | 885 |
| ut | 508 | 3 | 5 | 516 |
| va | 323 | 0 | 40 | 363 |
| det | 157 | 17 | 16 | 190 |
| other | 50 | 0 | 0 | 50 |
| pps | 23 | 0 | 6 | 29 |
| linkam | 0 | 18 | 0 | 72 |
| pilatus | 0 | 0 | 0 | 2343 |
| electrometers | 0 | 0 | 16 | 609 |
| xbpms | 0 | 0 | 0 | 6 |
| prosilica | 0 | 0 | 0 | 6620 |
| energy | 0 | 0 | 0 | 2 |
| manipulators | 0 | 0 | 0 | 24 |
| amptek | 0 | 0 | 0 | 21 |
| attenuators | 0 | 0 | 0 | 48 |
| waxschamber | 0 | 0 | 0 | 8 |

## Capture candidates: modelled + on screen, not in baseline (110)

These are the lowest-effort wins -- the profile already has the device; it is just not in `sd.baseline`.

| record | subsystem | profile device |
|---|---|---|
| `XF:12ID-ES{LINKAM}:SysReset` | ct | LThermal |
| `XF:12IDC-ES:2{Det:900KW}Stats1:Histogram_RBV` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}Stats1:MaxValue_RBV` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}Stats1:MeanValue_RBV` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}Stats1:MinValue_RBV` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}Stats1:Sigma_RBV` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:Acquire` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:AcquirePeriod` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:AcquireTime` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:ArrayCounter` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:ArrayRate_RBV` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:AsynIO` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:DetectorState_RBV` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:ImageMode` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:NumExposures` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:NumImages` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:NumImagesCounter_RBV` | det | pil900KW |
| `XF:12IDC-ES:2{Det:900KW}cam1:TriggerMode` | det | pil900KW |
| `XF:12ID2-ES{DDSM100-Ax:X1}Mtr` | es | bc_smaract |
| `XF:12ID2-ES{DDSM100-Ax:X2}Mtr` | es | bc_smaract |
| `XF:12ID2-ES{Mdrive-Ax:1}Mtr` | es | MDrive |
| `XF:12ID2-ES{Mdrive-Ax:2}Mtr` | es | MDrive |
| `XF:12ID2-ES{Mdrive-Ax:3}Mtr` | es | MDrive |
| `XF:12ID2-ES{Mdrive-Ax:4}Mtr` | es | MDrive |
| `XF:12ID2-ES{Mdrive-Ax:5}Mtr` | es | MDrive |
| `XF:12ID2-ES{Mdrive-Ax:6}Mtr` | es | MDrive |
| `XF:12ID2-ES{Mdrive-Ax:7}Mtr` | es | MDrive |
| `XF:12ID2-ES{Mdrive-Ax:8}Mtr` | es | MDrive |
| `XF:12ID2-ES{Pmp:1}Cmd:Run-Cmd` | es | syringe_pu |
| `XF:12ID2-ES{Pmp:1}Cmd:Stop-Cmd` | es | syringe_pu |
| `XF:12ID2-ES{Pmp:1}Val:Dia-RB` | es | syringe_pu |
| `XF:12ID2-ES{Pmp:1}Val:Dir-Sel` | es | syringe_pu |
| `XF:12ID2-ES{Pmp:1}Val:Rate-SP` | es | syringe_pu |
| `XF:12ID2-ES{Pmp:1}Val:Vol-SP` | es | syringe_pu |
| `XF:12ID2A-DM{DM1-IOL1:E1213}:DI1-Sts` | es | diagA_pos |
| `XF:12ID2A-DM{DM1-IOL1:E1213}:DI2-Sts` | es | diagA_pos |
| `XF:12ID2A-DM{DM1-IOL1:E1213}:DI3-Sts` | es | diagA_pos |
| `XF:12ID2A-DM{DM1-IOL1:E1213}:DO2-Cmd` | es | diagA_pos |
| `XF:12ID2A-DM{DM1-IOL1:E1213}:DO4-Cmd` | es | diagA_pos |
| `XF:12ID2A-DM{DM1-IOL1:E1213}:DO6-Cmd` | es | diagA_pos |
| `XF:12ID2B-DM{DM2-IOL1:E1213}:DI1-Sts` | es | diagB_pos |
| `XF:12ID2B-DM{DM2-IOL1:E1213}:DI2-Sts` | es | diagB_pos |
| `XF:12ID2B-DM{DM2-IOL1:E1213}:DI3-Sts` | es | diagB_pos |
| `XF:12ID2B-DM{DM2-IOL1:E1213}:DO2-Cmd` | es | diagB_pos |
| `XF:12ID2B-DM{DM2-IOL1:E1213}:DO4-Cmd` | es | diagB_pos |
| `XF:12ID2B-DM{DM2-IOL1:E1213}:DO6-Cmd` | es | diagB_pos |
| `XF:12IDC-ES:2{IO}AI:1-I` | es | moxa_out |
| `XF:12IDC-ES:2{IO}AI:2-I` | es | moxa_out |
| `XF:12IDC-ES:2{IO}AI:3-I` | es | moxa_out |
| `XF:12IDC-ES:2{IO}AI:4-I` | es | moxa_out |
| `XF:12IDC-ES:2{IO}AI:5-I` | es | moxa_out |
| `XF:12IDC-ES:2{IO}AI:6-I` | es | moxa_out |
| `XF:12IDC-ES:2{IO}AI:7-I` | es | moxa_out |
| `XF:12IDC-ES:2{IO}AI:8-I` | es | moxa_out |
| `XF:12IDC-ES:2{IO}AO:1-RB` | es | moxa_in |
| `XF:12IDC-ES:2{IO}AO:1-SP` | es | moxa_in |
| `XF:12IDC-ES:2{IO}AO:2-RB` | es | moxa_in |
| `XF:12IDC-ES:2{IO}AO:2-SP` | es | moxa_in |
| `XF:12IDC-ES:2{IO}AO:3-RB` | es | moxa_in |
| `XF:12IDC-ES:2{IO}AO:3-SP` | es | moxa_in |
| `XF:12IDC-ES:2{IO}AO:4-RB` | es | moxa_in |
| `XF:12IDC-ES:2{IO}AO:4-SP` | es | moxa_in |
| `XF:12ID-ES{LINKAM}:CONFIG` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:CTRLLR:ERR` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:DISABLE` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:DSC` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:LNP_MODE:SET` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:LNP_SPEED` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:LNP_SPEED:SET` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:MODEL` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:POWER` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:RAMPRATE` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:RAMPRATE:SET` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:RAMPTIME` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:SETPOINT:SET` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:STAGE:CONFIG` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:STAGE:MODEL` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:STARTHEAT` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:STATUS` | linkam | LThermal |
| `XF:12ID-ES{LINKAM}:TEMP` | linkam | LThermal |
| `XF:12IDA-OP:2{Slt:H-Ax:Hgap}Mtr` | mana | hfmslit |
| `XF:12IDA-OP:2{Slt:H-Ax:Hpos}Mtr` | mana | hfmslit |
| `XF:12IDA-OP:2{Slt:V-Ax:Vgap}Mtr` | mana | vfmslit |
| `XF:12IDA-OP:2{Slt:V-Ax:Vpos}Mtr` | mana | vfmslit |
| `XF:12IDA-OP:2{Slt:WB-Ax:Hgap}Mtr` | mana | wbs |
| `XF:12IDA-OP:2{Slt:WB-Ax:Hpos}Mtr` | mana | wbs |
| `XF:12IDA-OP:2{Slt:WB-Ax:Vgap}Mtr` | mana | wbs |
| `XF:12IDA-OP:2{Slt:WB-Ax:Vpos}Mtr` | mana | wbs |
| `XF:12IDC-ES:2{BS:WAXS-Ax:y}Mtr` | mana | pil900KW |
| `XF:12IDC-ES:2{Det:Amptek-Ax:X}Mtr` | mana | amptek_pos |
| `XF:12IDC-ES:2{Det:Amptek-Ax:Y}Mtr` | mana | amptek_pos |
| `XF:12IDC-ES:2{Det:Amptek-Ax:Z}Mtr` | mana | amptek_pos |
| `XF:12IDC-ES:2{WAXS:1-Ax:Arc}Mtr` | mana | pil900KW |
| `XF:12IDC:2{Sh:E-Ax:Y}Mtr` | mana | fs_motor |
| `XF:12ID2C-ES{MCS:2-Ax:1}Mtr` | op | waxs_bs |
| `XF:12IDC-ES:2:ACT0:CMD:STOP` | op | bdm |
| `XF:12IDC-ES:2:ACT0:CMD:TARGET` | op | bdm |
| `XF:12IDC-ES:2:ACT0:POSITION` | op | bdm |
| `XF:12IDC-ES:2:ACT0:REF_POSITION` | op | bdm |
| `XF:12IDC-ES:2:ACT1:CMD:STOP` | op | bdm |
| `XF:12IDC-ES:2:ACT1:CMD:TARGET` | op | bdm |
| `XF:12IDC-ES:2:ACT1:POSITION` | op | bdm |
| `XF:12IDC-ES:2:ACT1:REF_POSITION` | op | bdm |
| `XF:12IDC-ES:2:ACT2:CMD:STOP` | op | bdm |
| `XF:12IDC-ES:2:ACT2:CMD:TARGET` | op | bdm |
| `XF:12IDC-ES:2:ACT2:POSITION` | op | bdm |
| `XF:12IDC-ES:2:ACT2:REF_POSITION` | op | bdm |
| `XF:12IDA-BI:2{EM:BPM1}DAC3` | ut | fs |
| `XF:12IDC-ES:2{PSh:ES}pz:sh:close` | ut | fs |
| `XF:12IDC-ES:2{PSh:ES}pz:sh:open` | ut | fs |

## Unresolved CSS PVs (411 unique)

Screen PVs whose macros could not be fully resolved (runtime-supplied macros, or resolver blind spots). Listed for completeness.

- `XF:12IDA-OP{$(Dev)}$(Sig)-Sts`  (common/eps/ln-eps-info.opi)
- `XF:12IDA-OP{$(Dev)}$(Sig)-I`  (common/eps/ln-eps-info.opi)
- `XF:12IDA-OP{$(Dev)}$(Sig)_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP{$(Dev)}$(Sig)_HiHi-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-VA:0{$(Dev)}$(Sig)-Sts`  (common/eps/ln-eps-info.opi)
- `XF:12IDA-VA:0{$(Dev)}$(Sig)-I`  (common/eps/ln-eps-info.opi)
- `XF:12IDA-VA:0{$(Dev)}$(Sig)_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-VA:0{$(Dev)}$(Sig)_HiHi-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:0{Msk:FAM}T:I_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:0{Msk:FAM}T:O_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:0{Msk:FAM}T:TI_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:0{Msk:FAM}T:BI_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Slt:WB}T:HI_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Slt:WB}T:HO_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Slt:WB}T:VI_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Slt:WB}T:VO_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mono:DCM}T:Crys:1_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mono:DCM}T:Crys:2_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mono:DCM-Ax:R}T:I_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mono:DCM-Ax:P}T:I_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mono:DCM-Ax:Ygap}T:I_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:0{BS:WB}T:O_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{DI}T:Return_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{DI}T:Supply_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP{$(Dev)}L:19Lo-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}L:19Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}L:19Hi-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}L:19Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}L:23Lo-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}L:23Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}L:23Hi-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}L:23Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}T:05Lo-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}T:05Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}T:05Hi-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}T:05Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}T:06Lo-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}T:06Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}T:06Hi-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}T:06Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}P:01Lo-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}P:01Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}P:01Hi-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}P:01Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}P:03Lo-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}P:03Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}P:03Hi-I`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-OP{$(Dev)}P:03Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `XF:12IDA-UT{DI}P:Supply_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{DI}P:Return_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{EPS:Main}F_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{EPS:1}F_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{EPS:2}F_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{EPS:3}F_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mir:HF-Ax:Sc}T:Sc_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mir:HF-Ax:Io}T:Io_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mir:HF-Ax:P}T:P_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Slt:H-Ax:Scan}T:Scan_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Slt:H-Ax:Gap}T:Gap_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mir:VF-Ax:Sc}T:Sc_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mir:VF-Ax:Io}T:Io_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mir:VF-Ax:P}T:P_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Slt:V-Ax:Scan}T:Scan_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Slt:V-Ax:Gap}T:Gap_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mir:VD-Ax:Sc}T:Sc_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mir:VD-Ax:Io}T:Io_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:2{Mir:VD-Ax:P}T:P_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{PPS:1A}F_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{PPS:2A}F_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{PPS:1B}F_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-UT{PPS:2B}F_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-OP:0{BS:WB}T:I_$(Wrng)-RB`  (common/eps/eps-ind.opi)
- `XF:12IDA-VA:0{$(Dev)}$(Sig)_$(Alrm)-RB`  (common/eps/eps-ind.opi)
- `$(Sys){$(Dev)}L:19Lo-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}L:19Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}L:19Hi-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}L:19Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}L:23Lo-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}L:23Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}L:23Hi-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}L:23Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}T:05Lo-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}T:05Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}T:05Hi-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}T:05Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}T:06Lo-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}T:06Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}T:06Hi-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}T:06Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}P:01Lo-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}P:01Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}P:01Hi-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}P:01Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}P:03Lo-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}P:03Lo-SP`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}P:03Hi-I`  (12id/ut/_cryo_config_line.opi)
- `$(Sys){$(Dev)}P:03Hi-SP`  (12id/ut/_cryo_config_line.opi)
- `$(nom):NomClose-Cmd`  (12id/op/12ID_beam_param.opi)
- `$(nom):NomOpen-Cmd`  (12id/op/12ID_beam_param.opi)
- `$(Sys)$(Dev)$(Mtr).HLS`  (12id/op/motor/_motor_small.opi)

_…and 311 more._
