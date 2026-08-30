-- ============================================================================
-- SNM WORKS — REFERENCE DATA & MASTERS
-- ============================================================================

-- Masters Dropdown Lists
INSERT INTO masters (list_name, value, sort_order) VALUES
  ('products', 'Narrow Woven Webbing', 1),
  ('products', 'Technical Broad Fabric', 2),
  ('products', 'Braided Cordage', 3),
  ('products', 'Twisted Rope', 4),
  ('machines', 'Loom 01 (Needle Loom 4-Space)', 1),
  ('machines', 'Loom 02 (Needle Loom 2-Space Heavy)', 2),
  ('machines', 'Loom 03 (Shuttle Loom)', 3),
  ('machines', 'Braider 01 (16-Carrier)', 4),
  ('machines', 'Braider 02 (32-Carrier)', 5),
  ('machines', 'Dyeing Jigger 01', 6),
  ('defects', 'Broken End', 1),
  ('defects', 'Missing Pick', 2),
  ('defects', 'Uneven Width', 3),
  ('defects', 'Oil Stain', 4),
  ('defects', 'Shade Variation', 5),
  ('defects', 'Slack Selvedge', 6)
ON CONFLICT (list_name, value) DO NOTHING;

-- Parameter Library
INSERT INTO param_library (family, name, unit, method) VALUES
  ('Narrow woven', 'Width', 'mm', 'ASTM D3774'),
  ('Narrow woven', 'Thickness', 'mm', 'ASTM D1777'),
  ('Narrow woven', 'Weight per metre', 'g/m', 'ASTM D3776'),
  ('Narrow woven', 'Breaking Strength', 'kgf', 'ASTM D5034 / IS 1969'),
  ('Narrow woven', 'Elongation at Break', '%', 'ASTM D5034'),
  ('Narrow woven', 'Picks per cm', 'picks/cm', 'ASTM D3775'),
  ('Fabric', 'Width', 'cm', 'ISO 22198'),
  ('Fabric', 'Weight (GSM)', 'g/m²', 'ISO 3801'),
  ('Fabric', 'Tensile Strength Warp', 'kgf', 'ISO 13934-1'),
  ('Fabric', 'Tensile Strength Weft', 'kgf', 'ISO 13934-1'),
  ('Fabric', 'Tear Strength Warp', 'kgf', 'ISO 13937-2'),
  ('Fabric', 'Tear Strength Weft', 'kgf', 'ISO 13937-2'),
  ('Fabric', 'Ends per inch (EPI)', 'ends/in', 'ISO 7211-2'),
  ('Fabric', 'Picks per inch (PPI)', 'picks/in', 'ISO 7211-2'),
  ('Cordage', 'Diameter', 'mm', 'ISO 1968'),
  ('Cordage', 'Breaking Force', 'daN', 'ISO 2307'),
  ('Cordage', 'Linear Density', 'ktex', 'ISO 1968')
ON CONFLICT DO NOTHING;
