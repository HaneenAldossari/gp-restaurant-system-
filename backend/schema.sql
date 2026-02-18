-- ============================================
-- Smart Sales Analytics & Forecasting System
-- Database Schema — PostgreSQL
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

-- 2. Categories
CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL
);

-- 3. Products
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category_id INT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    selling_price DECIMAL(10,2) NOT NULL,
    cost DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (name, category_id)
);

-- 4. Uploads
CREATE TABLE uploads (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    rows_imported INT NOT NULL DEFAULT 0,
    rows_skipped INT NOT NULL DEFAULT 0,
    uploaded_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 5. Sales (core data)
CREATE TABLE sales (
    id SERIAL PRIMARY KEY,
    upload_id INT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    order_date DATE NOT NULL,
    order_time TIME,
    product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity INT NOT NULL CHECK (quantity > 0),
    unit_price DECIMAL(10,2) NOT NULL CHECK (unit_price > 0),
    unit_cost DECIMAL(10,2) NOT NULL CHECK (unit_cost >= 0),
    total_price DECIMAL(10,2) NOT NULL,
    total_cost DECIMAL(10,2) NOT NULL
);

-- 6. Forecasts (saved results)
CREATE TABLE forecasts (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope VARCHAR(20) NOT NULL CHECK (scope IN ('total', 'category', 'item')),
    target_name VARCHAR(100),
    period_days INT NOT NULL CHECK (period_days IN (7, 14, 30)),
    model_used VARCHAR(20) NOT NULL CHECK (model_used IN ('prophet', 'lstm')),
    result_json JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================
-- Indexes for performance
-- ============================================
CREATE INDEX idx_sales_order_date ON sales(order_date);
CREATE INDEX idx_sales_product_id ON sales(product_id);
CREATE INDEX idx_sales_upload_id ON sales(upload_id);
CREATE INDEX idx_products_category_id ON products(category_id);
CREATE INDEX idx_forecasts_user_id ON forecasts(user_id);
