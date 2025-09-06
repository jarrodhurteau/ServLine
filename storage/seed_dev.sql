INSERT INTO restaurants (name, phone, address) VALUES
  ('Trailblazer Pizza Co.', '413-555-0199', '123 River Rd, Chicopee, MA');

INSERT INTO menus (restaurant_id, name) VALUES
  (1, 'Default Menu');

INSERT INTO menu_items (menu_id, name, description, price_cents) VALUES
  (1, 'Large Pepperoni Pizza', 'Classic large pie with pepperoni', 1699),
  (1, 'Garlic Knots', 'Six knots with garlic butter + marinara', 599);
