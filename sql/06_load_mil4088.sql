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

-- 2. Insert master specification entry for MIL-W-4088K
DO $$
DECLARE
  v_spec_id uuid;
  v_var_id uuid;
  r RECORD;
BEGIN
  INSERT INTO specifications (spec_no, title, authority, revision, status)
  VALUES ('MIL-W-4088K', 'Webbing, Textile, Woven Nylon', 'US DoD / Natick RD&E', 'K', 'Active')
  ON CONFLICT (spec_no) DO UPDATE SET title = EXCLUDED.title
  RETURNING id INTO v_spec_id;

  -- Create Type VIII Variant
  INSERT INTO spec_variants (spec_id, variant_code, name, class, description)
  VALUES (v_spec_id, 'Type VIII Class 1', 'MIL-W-4088K Type VIII Class 1', '1', 'Parachute harness and cargo webbing')
  ON CONFLICT (spec_id, variant_code) DO UPDATE SET name = EXCLUDED.name
  RETURNING id INTO v_var_id;

  -- Add Requirements for Type VIII
  INSERT INTO spec_requirements (variant_id, stage, parameter, limit_type, spec_value, tolerance, upper_limit, unit, method, is_critical)
  VALUES
    (v_var_id, 'On-Loom Inspection', 'Width', 'nominal', 43.65625, 1.5875, NULL, 'mm', 'ASTM D3774', false),
    (v_var_id, 'Final Inspection', 'Thickness', 'range', 1.016, NULL, 1.778, 'mm', 'ASTM D1777', false),
    (v_var_id, 'Final Inspection', 'Weight per metre', 'maximum', 49.6, NULL, NULL, 'g/m', 'ASTM D3776', false),
    (v_var_id, 'Final Inspection', 'Breaking Strength', 'minimum', 1814.37, NULL, NULL, 'kgf', 'ASTM D5034 / IS 1969', true),
    (v_var_id, 'On-Loom Inspection', 'Total Ends', 'nominal', 166, 0, NULL, 'ends', 'Visual', false),
    (v_var_id, 'On-Loom Inspection', 'Picks per inch', 'nominal', 18, 1, NULL, 'picks/in', 'ASTM D3775', false)
  ON CONFLICT DO NOTHING;
END $$;
