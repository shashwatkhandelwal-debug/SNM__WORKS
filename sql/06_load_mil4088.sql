-- ============================================================================
-- SNM WORKS — LOAD MIL-W-4088K REFERENCE DATA & SPECIFICATIONS
-- ============================================================================

-- 1. Populate mil_w_4088_types reference table
INSERT INTO mil_w_4088_types (
  type, width_in, width_tol_in, thick_min_in, thick_max_in, weight_max_oz_yd,
  break_min_lb, ends_face_back, ends_binder, picks_class1, filling_1a2,
  ply_warp, ply_binder, ply_filling, yarn_class1, yarn_class1a2, weave, id_yarns
) VALUES
  ('I', 0.5625, 0.03125, 0.025, 0.040, 0.28, 500, 92, 0, 34, 68, 1, 0, 1, '420/68 W, 840/140 F', '420', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('Ia', 0.75, 0.03125, 0.025, 0.035, 0.32, 600, 108, 0, 34, 68, 1, 0, 1, '420/68 W, 840/140 F', '420', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('II', 1.0, 0.03125, 0.025, 0.040, 0.42, 600, 134, 0, 34, 68, 1, 0, 1, '420/68 W, 840/140 F', '420', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('III', 1.25, 0.03125, 0.025, 0.040, 0.52, 800, 168, 0, 34, 68, 1, 0, 1, '420/68 W, 840/140 F', '420', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('IV', 3.0, 0.125, 0.025, 0.040, 1.20, 1800, 400, 0, 34, 68, 1, 0, 1, '420/68 W, 840/140 F', '420', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('VI', 1.71875, 0.0625, 0.030, 0.050, 1.15, 2500, 114, 0, 21, 42, 2, 0, 2, '840/140 W F', '840', '2 up 2 down herringbone twill, 1 reversal', 'Red 70081, 2 at centre'),
  ('VII', 1.71875, 0.0625, 0.060, 0.100, 2.35, 6000, 229, 27, 26, 52, 2, 1, 2, '840/140 W B F', '840', 'Double plain weave; binder 2 up 2 down', 'Yellow 70068, 2 at each selvage'),
  ('VIII', 1.71875, 0.0625, 0.040, 0.070, 1.60, 4000, 166, 0, 18, 36, 2, 0, 2, '840/140 W F', '840', '2 up 2 down herringbone twill, 1 reversal', 'Black, 2 at centre'),
  ('VIIIa', 3.0, 0.09375, 0.040, 0.070, 2.80, 6300, 280, 0, 18, 36, 2, 0, 2, '840/140 W F', '840', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('VIIIb', 2.0, 0.0625, 0.040, 0.070, 1.80, 4500, 192, 0, 18, 36, 2, 0, 2, '840/140 W F', '840', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('VIIIc', 2.25, 0.0625, 0.040, 0.070, 2.10, 5300, 222, 0, 18, 36, 2, 0, 2, '840/140 W F', '840', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('IX', 3.0, 0.09375, 0.065, 0.100, 4.00, 9000, 257, 31, 28, 56, 3, 2, 2, '840/140 W B F', '840', 'Double plain weave; binder 2 up 2 down', 'None'),
  ('X', 1.71875, 0.09375, 0.105, 0.140, 3.70, 9500, 257, 31, 22, 44, 3, 1, 2, '840/140 W B F', '840', 'Double plain weave; binder 2 up 2 down', 'None'),
  ('XII', 1.71875, 0.0625, 0.025, 0.040, 0.85, 1200, 266, 0, 34, 68, 1, 0, 1, '420/68 W, 840/140 F', '420', '2 up 2 down herringbone twill, 1 reversal', 'Red 70081, 1 at each selvage'),
  ('XIII', 1.71875, 0.09375, 0.080, 0.120, 2.90, 7000, 281, 34, 24, 48, 2, 1, 2, '840/140 W B F', '840', 'Double plain weave; binder 2 up 2 down', 'Black, 2 at each selvage'),
  ('XIV', 0.5, 0.03125, 0.070, 0.100, 0.80, 1200, 91, 0, 36, 70, 7, 0, 7, '210/34 W F', '210', '5 up 1 down 1 up 5 down herringbone twill', 'None'),
  ('XV', 2.0, 0.0625, 0.035, 0.050, 1.25, 1500, 88, 0, 15, 30, 2, 0, 2, '840/140 W F', '840', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('XVI', 1.71875, 0.0625, 0.045, 0.080, 2.00, 4500, 198, 0, 17, 34, 2, 0, 2, '840/140 W F', '840', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('XVII', 1.0, 0.0625, 0.045, 0.070, 1.15, 2500, 114, 0, 15, 30, 2, 0, 2, '840/140 W F', '840', '2 up 2 down herringbone twill, 1 reversal', 'None'),
  ('XVIII', 1.0, 0.0625, 0.100, 0.160, 2.05, 6000, 260, 0, 18, 36, 2, 0, 2, '840/140 W F', '840', '5 up 1 down 1 up 5 down herringbone twill', 'None'),
  ('XIX', 1.75, 0.09375, 0.100, 0.130, 4.10, 10000, 280, 0, 18, 36, 3, 0, 2, '840/140 W F', '840', '5 up 1 down 1 up 5 down herringbone twill', 'Green 70063, 2 at centre'),
  ('XX', 1.0, 0.09375, 0.170, 0.210, 3.25, 9000, 162, 26, 19, 38, 5, 1, 3, '840/140 W B, 420 F', '840/420', 'Cross-section filling', 'None'),
  ('XXI', 1.25, 0.0625, 0.065, 0.085, 1.70, 3600, 260, 0, 25, 50, 5, 0, 10, '210/34 W F', '210', '5 up 1 down 1 up 5 down herringbone twill', 'None'),
  ('XXII', 1.71875, 0.09375, 0.090, 0.120, 3.50, 9500, 259, 0, 18, 36, 3, 0, 2, '840/140 W F', '840', '1/3 twill body, double plain selvage', 'Black, 2 at selvages, 2 at centre'),
  ('XXIII', 1.125, 0.09375, 0.200, 0.300, 3.70, 12000, 324, 27, 15, 30, 3, 2, 3, '840/140 W B, 420 F', '840/420', 'Cross-section filling', 'None'),
  ('XXIV', 1.9375, 0.09375, 0.055, 0.075, 2.25, 5500, 244, 0, 17, 34, 2, 0, 3, '840/140 W F', '840/420', '2 up 2 down herringbone twill, 3 reversals', 'None'),
  ('XXV', 1.0, 0.0625, 0.080, 0.125, 1.50, 4500, 169, 20, 22, 44, 2, 1, 2, '840/140 W B F', '840', 'Double plain weave; binder 2 up 2 down', 'None'),
  ('XXVI', 1.75, 0.0625, 0.150, 0.180, 4.90, 15000, 236, 0, 16, 32, 5, 0, 3, '840/140 W F', '840/420', '5/1/1/5/1/1 twill, 11 harness', 'Yellow 70068, 2 at centre'),
  ('XXVII', 1.71875, 0.0625, 0.085, 0.110, 2.90, 6500, 215, 0, 24, 48, 3, 0, 2, '840/140 W F', '840', 'Two ends weaving as one', 'Black, 1 at each selvage'),
  ('XXVIII', 2.25, 0.09375, 0.080, 0.110, 3.80, 8700, 257, 31, 22, 44, 3, 1, 2, '840/140 W B F', '840', 'Double plain weave; binder 2 up 2 down', 'None')
ON CONFLICT (type) DO UPDATE SET
  width_in = EXCLUDED.width_in,
  break_min_lb = EXCLUDED.break_min_lb,
  ends_face_back = EXCLUDED.ends_face_back;


-- 2. Insert master specification entry for MIL-W-4088K (Rebuilt to match real production data)
DO $$
DECLARE
  v_spec_id uuid;
BEGIN
  INSERT INTO specifications (spec_no, revision, title, issuing_body, issued_on, supersedes, distribution, scope, active)
  VALUES ('MIL-W-4088', 'K', 'Webbing, Textile, Woven Nylon', 'US Army Natick RD&E Center', '1988-11-21', 'MIL-W-4088J', 'Statement A — approved for public release', 'Untreated nylon webbing. FSC 8305.', true)
  ON CONFLICT (spec_no, revision) DO UPDATE SET title = EXCLUDED.title
  RETURNING id INTO v_spec_id;

  -- 3. Spec-wide generic requirements (variant_id is NULL)
  INSERT INTO spec_requirements (spec_id, variant_id, parameter, unit, limit_type, spec_value, tolerance, upper_limit, text_value, test_method, clause_ref, is_critical, sort_order) VALUES (v_spec_id, NULL, 'pH of water extract', 'pH', 'range', 5.0, NULL, 8.5, NULL, 'FED-STD-191 2811', '3.8', false, 20);
  INSERT INTO spec_requirements (spec_id, variant_id, parameter, unit, limit_type, spec_value, tolerance, upper_limit, text_value, test_method, clause_ref, is_critical, sort_order) VALUES (v_spec_id, NULL, 'Curvature', 'in per yard', 'maximum', 0.25, NULL, NULL, NULL, 'MIL-W-4088K 4.5.1', '3.6.2', false, 21);
  INSERT INTO spec_requirements (spec_id, variant_id, parameter, unit, limit_type, spec_value, tolerance, upper_limit, text_value, test_method, clause_ref, is_critical, sort_order) VALUES (v_spec_id, NULL, 'Yarn twist, final', 'TPI', 'minimum', 2.5, NULL, NULL, NULL, 'FED-STD-191 4054', '3.3.1.2', false, 22);
  INSERT INTO spec_requirements (spec_id, variant_id, parameter, unit, limit_type, spec_value, tolerance, upper_limit, text_value, test_method, clause_ref, is_critical, sort_order) VALUES (v_spec_id, NULL, 'Colorfastness — crocking', 'AATCC CTS', 'minimum', 3.5, NULL, NULL, NULL, 'FED-STD-191 5651', '3.4.3', false, 23);

  -- 4. Spec defects classification matrix (30 Table VI items)
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Abrasion marks', 'Rupture of yarns, or nap obscuring any yarn over 10% of width or 1 inch', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Yarns (filling)', 'Two yarns per shed (class 1 only)', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Broken or missing end', 'Two or more, or a single end over 6 inches', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Broken or missing end', 'Single end under 6 inches but over 1/4 inch', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Broken or missing pick', 'Two or more regardless of extent', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Coarse or light filling bar', 'Visible difference over 1/4 inch lengthwise', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Coarse or light filling bar', 'Visible difference 1/4 inch or less lengthwise', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Twist or distortion', 'Will not lay flat under manual pressure', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Cut, hole or tear', 'Any cut, hole or tear', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Drop-ply', 'More than 2 ends over 9 linear inches', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Drop-ply', '1 or 2 ends over 9 linear inches', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Edges', 'Frayed, slack or poorly constructed over 1/4 inch', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Floats or skips', 'Three or more of 1/2 inch, or single over 1 inch', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Floats or skips', 'Three or more under 1/2 inch', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Hitchback crack', 'Visible opening between picks', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Jerked-in filling', 'Visible loop of filling pulled in at edges', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Kinks', 'More than 3 in any linear inches', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Knots', 'More than 1 knot in any 9 linear inches', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Mispick, double pick', 'Two or more across the full width', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Mispick, double pick', 'Single across the full width', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Slack end', 'Two or more, or clearly visible loops', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Slack end', 'Single jerked in between picks', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Slub, slug, gout', 'More than twice the thickness of the yarn', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Smash', 'Any smash', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Spot, stain or streak', 'Any clearly visible', 'Minor', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Tight end', 'Clearly visible up to 12 inches', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Wrong draw', 'Extending more than 9 inches', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Dropped knitted stitch on edge', 'Shuttleless looms, classes 1A and 2', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Catch-cord missing', 'Shuttleless looms, classes 1A and 2', 'Major', 'Table VI');
  INSERT INTO spec_defects (spec_id, examine, defect, classification, clause_ref) VALUES (v_spec_id, 'Width', 'Beyond specified tolerances', 'Minor', 'Table VI');

  -- 5. Spec sampling plan (9 ANSI/ASQC Z1.4 items)
  INSERT INTO spec_sampling (spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number) VALUES (v_spec_id, 'yards', 'end item testing', 0, 800, 2, NULL);
  INSERT INTO spec_sampling (spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number) VALUES (v_spec_id, 'yards', 'visual', 0, 1200, 3, 0);
  INSERT INTO spec_sampling (spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number) VALUES (v_spec_id, 'yards', 'end item testing', 801, 22000, 3, NULL);
  INSERT INTO spec_sampling (spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number) VALUES (v_spec_id, 'yards', 'visual', 1201, 3200, 5, 0);
  INSERT INTO spec_sampling (spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number) VALUES (v_spec_id, 'yards', 'visual', 3201, 1E+4, 8, 0);
  INSERT INTO spec_sampling (spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number) VALUES (v_spec_id, 'yards', 'visual', 10001, 35000, 13, 0);
  INSERT INTO spec_sampling (spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number) VALUES (v_spec_id, 'yards', 'end item testing', 22001, NULL, 5, NULL);
  INSERT INTO spec_sampling (spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number) VALUES (v_spec_id, 'yards', 'visual', 35001, 1.5E+5, 20, 1);
  INSERT INTO spec_sampling (spec_id, basis, purpose, lot_from, lot_to, sample_size, accept_number) VALUES (v_spec_id, 'yards', 'visual', 150001, NULL, 32, 2);
END $$;
