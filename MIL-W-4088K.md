# MIL-W-4088K — reference data

Webbing, Textile, Woven Nylon. Revision K, 21 November 1988, superseding
MIL-W-4088J. FSC 8305. Distribution Statement A, approved for public release.
Preparing activity: US Army Natick RD&E Center.

Use this to regenerate `sql/06_load_mil4088.sql` if it is missing. **Transcribe
exactly. Do not round, do not infer, do not fill gaps from memory.**

---

## Classes

| Class | Meaning |
|---|---|
| 1 | Critical use, shuttle loom, nylon 6,6 |
| 1A | Critical use, shuttleless loom, nylon 6,6 |
| 2 | Non-critical use, shuttle or shuttleless, nylon 6 or 6,6 |

Where a purchase order names no class, class 1 applies (1.2.1). Class 2 orders
may be filled with class 1 or 1A.

Table II covers class 1. Table III covers classes 1A and 2. The tables share
width, thickness, weight, breaking strength and total warp ends. They differ in
the filling count: Table III's filling yarns per inch is exactly twice Table
II's picks per inch, because classes 1A and 2 run two picks per shed
(Table III footnote 1). This holds for all 30 types and is a useful check on any
transcription.

---

## Tables II and III combined

| Type | Width in | ±tol | Thick min | Thick max | Wt max oz/yd | Break min lb | Ends F+B | Binder | Picks/in cl.1 | Filling/in cl.1A+2 | Ply W/B/F | Yarn cl.1 | Yarn cl.1A+2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| I | 0.5625 | 0.03125 | 0.025 | 0.040 | 0.28 | 500 | 92 | — | 34 | 68 | 1/—/1 | 420/68 W, 840/140 F | 420 |
| Ia | 0.75 | 0.03125 | 0.025 | 0.035 | 0.32 | 600 | 108 | — | 34 | 68 | 1/—/1 | 420/68 W, 840/140 F | 420 |
| II | 1.0 | 0.03125 | 0.025 | 0.040 | 0.42 | 600 | 134 | — | 34 | 68 | 1/—/1 | 420/68 W, 840/140 F | 420 |
| III | 1.25 | 0.03125 | 0.025 | 0.040 | 0.52 | 800 | 168 | — | 34 | 68 | 1/—/1 | 420/68 W, 840/140 F | 420 |
| IV | 3.0 | 0.125 | 0.025 | 0.040 | 1.20 | 1800 | 400 | — | 34 | 68 | 1/—/1 | 420/68 W, 840/140 F | 420 |
| VI | 1.71875 | 0.0625 | 0.030 | 0.050 | 1.15 | 2500 | 114 | — | 21 | 42 | 2/—/2 | 840/140 W F | 840 |
| VII | 1.71875 | 0.0625 | 0.060 | 0.100 | 2.35 | 6000 | 229 | 27 | 26 | 52 | 2/1/2 | 840/140 W B F | 840 |
| VIII | 1.71875 | 0.0625 | 0.040 | 0.070 | 1.60 | 4000 | 166 | — | 18 | 36 | 2/—/2 | 840/140 W F | 840 |
| VIIIa | 3.0 | 0.09375 | 0.040 | 0.070 | 2.80 | 6300 | 280 | — | 18 | 36 | 2/—/2 | 840/140 W F | 840 |
| VIIIb | 2.0 | 0.0625 | 0.040 | 0.070 | 1.80 | 4500 | 192 | — | 18 | 36 | 2/—/2 | 840/140 W F | 840 |
| VIIIc | 2.25 | 0.0625 | 0.040 | 0.070 | 2.10 | 5300 | 222 | — | 18 | 36 | 2/—/2 | 840/140 W F | 840 |
| IX | 3.0 | 0.09375 | 0.065 | 0.100 | 4.00 | 9000 | 257 | 31 | 28 | 56 | 3/2/2 | 840/140 W B F | 840 |
| X | 1.71875 | 0.09375 | 0.105 | 0.140 | 3.70 | 9500 | 257 | 31 | 22 | 44 | 3/1/2 | 840/140 W B F | 840 |
| XII | 1.71875 | 0.0625 | 0.025 | 0.040 | 0.85 | 1200 | 266 | — | 34 | 68 | 1/—/1 | 420/68 W, 840/140 F | 420 |
| XIII | 1.71875 | 0.09375 | 0.080 | 0.120 | 2.90 | 7000 | 281 | 34 | 24 | 48 | 2/1/2 | 840/140 W B F | 840 |
| XIV | 0.5 | 0.03125 | 0.070 | 0.100 | 0.80 | 1200 | 91 | — | 36 | 70 | 7/—/7 | 210/34 W F | 210 |
| XV | 2.0 | 0.0625 | 0.035 | 0.050 | 1.25 | 1500 | 88 | — | 15 | 30 | 2/—/2 | 840/140 W F | 840 |
| XVI | 1.71875 | 0.0625 | 0.045 | 0.080 | 2.00 | 4500 | 198 | — | 17 | 34 | 2/—/2 | 840/140 W F | 840 |
| XVII | 1.0 | 0.0625 | 0.045 | 0.070 | 1.15 | 2500 | 114 | — | 15 | 30 | 2/—/2 | 840/140 W F | 840 |
| XVIII | 1.0 | 0.0625 | 0.100 | 0.160 | 2.05 | 6000 | 260 | — | 18 | 36 | 2/—/2 | 840/140 W F | 840 |
| XIX | 1.75 | 0.09375 | 0.100 | 0.130 | 4.10 | 10000 | 280 | — | 18 | 36 | 3/—/2 | 840/140 W F | 840 |
| XX | 1.0 | 0.09375 | 0.170 | 0.210 | 3.25 | 9000 | 162 | 26 | 19 | 38 | 5/1/3 | 840/140 W B F | 840 W+B, 420 F |
| XXI | 1.25 | 0.0625 | 0.065 | 0.085 | 1.70 | 3600 | 260 | — | 25 | 50 | 5/—/10 | 210/34 W F | 210 |
| XXII | 1.71875 | 0.09375 | 0.090 | 0.120 | 3.50 | 9500 | 259 | — | 18 | 36 | 3/—/2 | 840/140 W F | 840 |
| XXIII | 1.125 | 0.09375 | 0.200 | 0.300 | 3.70 | 12000 | 324 | 27 | 15 | 30 | 3/2/3 | 840/140 W B F | 840 W+B, 420 F |
| XXIV | 1.9375 | 0.09375 | 0.055 | 0.075 | 2.25 | 5500 | 244 | — | 17 | 34 | 2/—/3 | 840/140 W F | 840 W+B, 420 F |
| XXV | 1.0 | 0.0625 | 0.080 | 0.125 | 1.50 | 4500 | 169 | 20 | 22 | 44 | 2/1/2 | 840/140 W B F | 840 |
| XXVI | 1.75 | 0.0625 | 0.150 | 0.180 | 4.90 | 15000 | 236 | — | 16 | 32 | 5/—/3 | 840/140 W F | 840 W+B, 420 F |
| XXVII | 1.71875 | 0.0625 | 0.085 | 0.110 | 2.90 | 6500 | 215 | — | 24 | 48 | 3/—/2 | 840/140 W F | 840 |
| XXVIII | 2.25 | 0.09375 | 0.080 | 0.110 | 3.80 | 8700 | 257 | 31 | 22 | 44 | 3/1/2 | 840/140 W B F | 840 |

Yarn codes: W = warp, B = binder, F = filling. Denier/filament before dyeing.

---

## Weaves (clause 3.7)

- 2 up 2 down herringbone twill, 1 reversal at centre  
  Types: I, Ia, II, III, IV, VI, VIII, VIIIa, VIIIb, VIIIc, XII, XV, XVI, XVII
- Double plain weave; binder ends 2 up 2 down, 1 end as 1  
  Types: VII, IX, X, XIII, XXV, XXVIII
- 5 up 1 down 1 up 5 down herringbone twill, 1 reversal at centre  
  Types: XIV, XVIII, XIX, XXI
- Per figure 2, cross-section filling  
  Types: XX, XXIII
- 1/3 twill body with back filling; double plain selvage (figure 3)  
  Types: XXII
- 2 up 2 down herringbone twill, three reversals  
  Types: XXIV
- 5/1/1/5/1/1 twill, 7 harness + 4 selvage = 11, no reversal  
  Types: XXVI
- Per figure 6, two ends weaving as one  
  Types: XXVII

---

## Identification yarns (Table I)

Coloured warp ends woven in to identify the type permanently. Cable numbers are
Color Association of the United States.

- **Type VI** — Red 70081, 2 at centre of warp
- **Type VII** — Yellow 70068, 2 at each selvage
- **Type VIII** — Black, 2 at centre of warp
- **Type XII** — Red 70081, 1 at each selvage
- **Type XIII** — Black, 2 at each selvage
- **Type XIX** — Green 70063, 2 at centre of warp
- **Type XXII** — Black, 2 at each selvage and 2 at centre
- **Type XXVI** — Yellow 70068, 2 at centre of warp
- **Type XXVII** — Black, 1 at each selvage

All other types: none specified unless the procurement document calls for it
(3.5.2.1). When webbing is piece dyed, polyester yarn of approximate denier is
used for identification ends.

---

## Requirements applying to every type

| Clause | Requirement |
|---|---|
| 3.3.1 | Nylon bright, high tenacity, light and heat resistant, not bleached. Nylon 6,6 for classes 1 and 1A; nylon 6 or 6,6 for class 2 |
| 3.3.1.2 | Minimum 2½ turns per inch final twist. **Types XXII and XXVII: minimum 1½ turns per inch** |
| 3.4 | Colours: Natural White; Natural White special purpose; Olive Drab 7; Camouflage Green 483; Air Force Sage Green 1531; Air Force Yellow 1365 |
| 3.4.2 | Shade matched under filtered tungsten at 7500 ± 200 K, 100 ± 20 foot-candles; good match at 2300 ± 200 K incandescent |
| 3.4.3 | Light fastness not below "good"; laundering not below "fair"; crocking not lower than AATCC Chromatic Transference 3.5. Shall not be bleached |
| 3.4.4 | Camouflage Green 483 over 1¼ in wide in types VIII, VIIIa, VIIIb, VIIIc, XII, XIII must meet spectral reflectance limits, 600–860 nm |
| 3.6.1 | **No individual specimen may fall below the table minimum breaking strength.** This is not an average |
| 3.6.1.1 | Types XXII and XXVII must retain at least 90% of minimum breaking strength after abrasion |
| 3.6.2 | Lateral curvature no more than ¼ inch within a yard. Five specimens, averaged, to nearest 1/32 in |
| 3.8 | pH of water extract not less than 5.0 nor more than 8.5 |
| 3.9 | Put-up: types XVIII and XXVI 90–110 yd, max 1 piece. Types XX and XXIII 60–70 yd, max 2 pieces each ≥10 yd. All others 90–110 yd, max 3 pieces each ≥10 yd |
| 3.10 | Roll ticket printed, not handwritten: stock number, nomenclature, specification number, yardage, contract number and date of manufacture, contractor, contracting agency |

---

## Spectral reflectance, Camouflage Green 483 (3.4.4)

| nm | Min % | Max % | nm | Min % | Max % |
|---|---|---|---|---|---|
| 600 | 3 | 10 | 740 | 7 | 52 |
| 620 | 3 | 10 | 760 | 11 | 60 |
| 640 | 3 | 10 | 780 | 17 | 64 |
| 660 | 3 | 11 | 800 | 24 | 67 |
| 680 | 3 | 13 | 820 | 32 | 70 |
| 700 | 4 | 28 | 840 | 37 | 71 |
| 720 | 5 | 40 | 860 | 40 | 73 |

Measured 600–860 nm at 20 nm intervals against barium sulfate. Failure at four
or more wavelengths for any colour is a test failure (4.5.2).

---

## End item tests (Table VII)

| Characteristic | Clause | Method |
|---|---|---|
| Thickness | Tables II and III | FED-STD-191 5030 |
| Weight per linear yard | Tables II and III | FED-STD-191 5041, to nearest 0.01 oz |
| Ends, face and back warp | Tables II and III | FED-STD-191 5050 |
| Ends, binder warp | Tables II and III | FED-STD-191 5050 |
| Picks per inch | Tables II and III | FED-STD-191 5050 |
| Breaking strength, original | 3.6.1 | FED-STD-191 4108 |
| Breaking strength after abrasion | 3.6.1.1 | FED-STD-191 5309 then 4108 |
| Curvature | 3.6.2 | MIL-W-4088K 4.5.1, 5 determinations |
| pH | 3.8 | FED-STD-191 2811 |
| Colorfastness — light | 3.4.3 | FED-STD-191 5660 |
| Colorfastness — laundering | 3.4.3 | FED-STD-191 5614 |
| Colorfastness — crocking | 3.4.3 | FED-STD-191 5651 |
| Spectral reflectance | 3.4.4 | MIL-W-4088K 4.5.2 |
| Yarn denier | 3.3.1.1 | FED-STD-191 4021 |
| Yarn twist | 3.3.1.2 | FED-STD-191 4054 |
| Weave | 3.7 | Visual, pass or fail |

---

## Sampling

**Visual and length examination (Table V).** Inspection level III.
AQL 0.40 defects per hundred units for major, 1.5 for total.

| Lot size, yards | Rolls sampled | Accept number |
|---|---|---|
| up to 1200 | 3 | 0 |
| 1201–3200 | 5 | 0 |
| 3201–10,000 | 8 | 0 |
| 10,001–35,000 | 13 | 0 |
| 35,001–150,000 | 20 | 1 |
| 150,001 and over | 32 | 2 |

If a lot has fewer than three rolls, every roll is examined.

**End item testing (4.4.3).**

| Lot size, yards | Sample units |
|---|---|
| 800 or less | 2 |
| 801–22,000 | 3 |
| 22,001 and over | 5 |

Sample unit size: types I, Ia, II, III → 10 yards. Types XXII, XXVII → 25
yards. All others → 20 yards.

**Component testing (4.4.1.1)**, lot in pounds: ≤800 → 2; 801–22,000 → 3;
22,001+ → 5. Sample unit 500 yards of yarn.

---

## Visual defects (Table VI)

| Examine | Defect | Class |
|---|---|---|
| Abrasion marks | Rupture of yarns, or nap obscuring any yarn over 10% of width or 1 inch | Major |
| Yarns (filling) | Two yarns per shed, class 1 only | Major |
| Broken or missing end | Two or more, or a single end over 6 inches | Major |
| Broken or missing end | Single under 6 inches but over ¼ inch | Minor |
| Broken or missing pick | Two or more regardless of extent | Major |
| Coarse or light filling bar | Visible stiffness or thickness difference over ¼ inch lengthwise | Major |
| Coarse or light filling bar | ¼ inch or less lengthwise | Minor |
| Twist or distortion | Will not lay flat under manual pressure | Minor |
| Cut, hole or tear | Any | Major |
| Drop-ply | More than 2 ends over 9 linear inches | Major |
| Drop-ply | 1 or 2 ends over 9 linear inches | Minor |
| Edges | Frayed, slack or poorly constructed over ¼ inch | Major |
| Floats or skips | Three or more of ½ inch, or single over 1 inch | Major |
| Floats or skips | Three or more under ½ inch, or single over ½ not over 1 inch | Minor |
| Hitchback crack | Visible opening between picks, or warpwise light and heavy places | Minor |
| Jerked-in filling, slough-off, slug | Visible loop of filling pulled in at edges | Minor |
| Kinks | More than 3 in any linear inches | Major |
| Knots | More than 1 in any 9 linear inches | Minor |
| Knots | One every 2 yards with untrimmed ends extending from surface | Minor |
| Mispick, double pick | Two or more across full width | Major |
| Mispick, double pick | Single across full width | Minor |
| Slack end | Two or more, jerked in between picks, or visible loops | Major |
| Slack end | Single jerked in between picks or visible loops | Minor |
| Slub, slug, gout | More than twice the thickness of the yarn or ply | Minor |
| Smash | Any | Major |
| Spot, stain or streak | Any clearly visible | Minor |
| Tight end | Clearly visible up to 12 inches | Major |
| Wrong draw | Extending more than 9 inches | Major |
| Dropped knitted stitch on edge | Shuttleless looms, classes 1A and 2 | Major |
| Catch-cord missing | Shuttleless looms, classes 1A and 2 | Major |
| Width | Beyond specified tolerances | Minor |

"Clearly visible" means at normal inspection distance, about 3 feet.

---

## Intended use (6.1)

General: parachutes and accessories, tow target reinforcement, safety belts,
bomb hoists and slings, tie-down equipment, overrun barriers.

- Type XXVII — aeronautical safety equipment
- Type XXVIII — cover, water canteen, 2-quart collapsible
- Types VIIIb and VIIIc — load carrying equipment

---

## Referenced documents

MIL-P-43334 packaging · FED-STD-191 test methods · MIL-STD-105 sampling ·
MIL-STD-1480 manufacturer colour codes · AATCC Chromatic Transference Scale ·
Standard Color Card of America
