-- ============================================
-- Smart Sales Analytics & Forecasting System
-- Database Schema — PostgreSQL (8 Tables)
-- Based on Supervisor's ERD
-- ============================================

-- 1. Users
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'manager' CHECK (role IN ('admin', 'manager')),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 2. Uploads
CREATE TABLE uploads (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    rows_imported INT NOT NULL,
    rows_skipped INT NOT NULL DEFAULT 0,
    uploaded_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 3. Categories
CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    name_ar VARCHAR(100) NOT NULL,
    name_en VARCHAR(100) NOT NULL
);

-- 4. Products
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    sku VARCHAR(50) UNIQUE NOT NULL,
    name_ar VARCHAR(100) NOT NULL,
    name_en VARCHAR(100) NOT NULL,
    category_id INT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 5. Orders
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    upload_id INT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    order_reference VARCHAR(50) UNIQUE NOT NULL,
    order_datetime TIMESTAMP NOT NULL,
    customer_name VARCHAR(100)
);

-- 6. Order Items
CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity INT NOT NULL CHECK (quantity > 0),
    unit_price DECIMAL(10,2) NOT NULL,
    unit_cost DECIMAL(10,2) NOT NULL
);

-- 7. Forecasts
CREATE TABLE forecasts (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_type VARCHAR(30) NOT NULL,
    target_id INT,
    train_start DATE NOT NULL,
    train_end DATE NOT NULL,
    horizon_days INT NOT NULL,
    model_used VARCHAR(20) NOT NULL,
    metrics_json JSONB NOT NULL,
    result_json JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 8. Menu Product Metrics
CREATE TABLE menu_product_metrics (
    id SERIAL PRIMARY KEY,
    product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    total_qty INT NOT NULL,
    total_revenue DECIMAL(10,2) NOT NULL,
    total_cost DECIMAL(10,2) NOT NULL,
    contribution_margin DECIMAL(10,2) NOT NULL,
    margin_pct DECIMAL(5,2) NOT NULL,
    popularity_score DECIMAL(5,2) NOT NULL,
    classification VARCHAR(20) NOT NULL CHECK (classification IN ('Star', 'Plowhorse', 'Puzzle', 'Dog')),
    created_by INT NOT NULL REFERENCES users(id) ON DELETE CASCADE
);

-- ============================================
-- Indexes for performance
-- ============================================
CREATE INDEX idx_uploads_user_id ON uploads(user_id);
CREATE INDEX idx_products_category_id ON products(category_id);
CREATE INDEX idx_orders_upload_id ON orders(upload_id);
CREATE INDEX idx_orders_datetime ON orders(order_datetime);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_order_items_product_id ON order_items(product_id);
CREATE INDEX idx_forecasts_user_id ON forecasts(user_id);
CREATE INDEX idx_menu_metrics_product_id ON menu_product_metrics(product_id);
