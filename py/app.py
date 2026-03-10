import time
import threading
import os
from datetime import datetime
from flask import Flask, jsonify, request, session
from flask_cors import CORS
import sqlite3
import hashlib
from collections import deque

app = Flask(__name__)
app.secret_key = 'smart-shopping-system-secret-key-2024'
CORS(app)

# 移除所有序列埠相關變數
# 保留資料庫和條碼掃描器變數
database_initialized = False

# Barcode scanner variables
scanned_barcodes = deque(maxlen=10)  # Store recent barcodes
last_barcode_time = 0
BARCODE_TIMEOUT = 2  # seconds between barcode scans

# Password attempt limits
login_attempts = {}

# 預設使用者（不需要刷卡，直接使用密碼登入）
DEFAULT_USERS = {
    'admin': {
        'name': 'Admin',
        'password': 'admin123',
        'balance': 1000.0,
        'reward_points': 50.0,
        'is_admin': True
    },
    'william': {
        'name': 'William',
        'password': '12345678',
        'balance': 500.0,
        'reward_points': 25.0,
        'is_admin': True
    },
    'user': {
        'name': 'Demo User',
        'password': 'demo123',
        'balance': 200.0,
        'reward_points': 10.0,
        'is_admin': False
    }
}

# 當前登入的使用者
current_user = None

# Initialize database
def init_database():
    """Initialize SQLite database - Fixed to prevent duplicate entries"""
    global database_initialized
    
    if database_initialized:
        return
    
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    # Create customers table with password field and reward points
    c.execute('''CREATE TABLE IF NOT EXISTS customers
                 (username TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  password_hash TEXT NOT NULL,
                  balance REAL DEFAULT 0.0,
                  reward_points REAL DEFAULT 0.0,
                  is_admin BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Create products table WITH BARCODE SUPPORT
    c.execute('''CREATE TABLE IF NOT EXISTS products
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  price REAL NOT NULL,
                  category TEXT,
                  barcode TEXT UNIQUE,
                  stock INTEGER DEFAULT 100,
                  reward_points_cost REAL DEFAULT 0.0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Create purchase history table
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT NOT NULL,
                  product_id INTEGER NOT NULL,
                  product_name TEXT NOT NULL,
                  barcode TEXT,
                  quantity INTEGER NOT NULL,
                  unit_price REAL NOT NULL,
                  total_price REAL NOT NULL,
                  earned_points REAL DEFAULT 0.0,
                  purchase_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (username) REFERENCES customers (username))''')
    
    # Create topup history table
    c.execute('''CREATE TABLE IF NOT EXISTS topup_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT NOT NULL,
                  amount REAL NOT NULL,
                  previous_balance REAL NOT NULL,
                  new_balance REAL NOT NULL,
                  topup_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (username) REFERENCES customers (username))''')
    
    # Create reward redemption history table
    c.execute('''CREATE TABLE IF NOT EXISTS reward_redemptions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT NOT NULL,
                  product_id INTEGER NOT NULL,
                  product_name TEXT NOT NULL,
                  points_used REAL NOT NULL,
                  redemption_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (username) REFERENCES customers (username),
                  FOREIGN KEY (product_id) REFERENCES products (id))''')
    
    # Check if sample data already exists
    c.execute("SELECT COUNT(*) FROM customers")
    customer_count = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM products")
    product_count = c.fetchone()[0]
    
    # Only insert sample data if tables are empty
    if customer_count == 0:
        # Insert default users
        for username, user_data in DEFAULT_USERS.items():
            password_hash = hash_password(user_data['password'])
            c.execute("INSERT INTO customers (username, name, password_hash, balance, reward_points, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
                     (username, user_data['name'], password_hash, user_data['balance'], user_data['reward_points'], user_data['is_admin']))
    
    if product_count == 0:
        # Insert sample product data with barcodes and reward points cost
        products = [
            ('Coca-Cola', 5.00, 'Beverages', '4901777013931', 100, 5.0),
            ('Potato Chips', 8.50, 'Snacks', '4902102118878', 100, 8.5),
            ('Chocolate', 12.00, 'Snacks', '4901777242950', 100, 12.0),
            ('Mineral Water', 2.50, 'Beverages', '4901777242967', 100, 2.5),
            ('Bread', 6.00, 'Food', '4901777242974', 100, 6.0),
            ('Milk', 15.00, 'Beverages', '4901777242981', 100, 15.0),
            ('Apple', 4.50, 'Fruits', '4901777242998', 100, 4.5),
            ('Banana', 3.00, 'Fruits', '4901777243001', 100, 3.0),
            ('Reward Chocolate', 0.0, 'Rewards', 'REWARD001', 50, 10.0),
            ('Reward Drink', 0.0, 'Rewards', 'REWARD002', 30, 5.0),
            ('Reward Snack', 0.0, 'Rewards', 'REWARD003', 20, 3.0)
        ]
        
        for product in products:
            c.execute("INSERT INTO products (name, price, category, barcode, stock, reward_points_cost) VALUES (?, ?, ?, ?, ?, ?)", product)
    
    conn.commit()
    conn.close()
    
    database_initialized = True

def hash_password(password):
    """Hash password using SHA-256 with salt"""
    salt = "smart_shopping_system_salt_2024"
    return hashlib.sha256((password + salt).encode()).hexdigest()

def verify_password(input_password, stored_hash):
    """Verify password against stored hash"""
    if not stored_hash:
        return False
    return hash_password(input_password) == stored_hash

def check_login_attempts(username):
    """Check login attempts to prevent brute force attacks"""
    if username not in login_attempts:
        login_attempts[username] = {'count': 0, 'last_attempt': time.time()}
    
    attempts = login_attempts[username]
    
    # Reset counter if more than 5 minutes have passed
    if time.time() - attempts['last_attempt'] > 300:  # 5 minutes
        attempts['count'] = 0
    
    # Check if exceeded limit
    if attempts['count'] >= 5:
        return False, "Too many failed attempts. Please try again later."
    
    return True, "OK"

def record_login_attempt(username, success):
    """Record login attempt"""
    if username not in login_attempts:
        login_attempts[username] = {'count': 0, 'last_attempt': time.time()}
    
    attempts = login_attempts[username]
    attempts['last_attempt'] = time.time()
    
    if success:
        attempts['count'] = 0  # Reset counter on successful login
    else:
        attempts['count'] += 1

def add_value(username, amount):
    """Add value/balance to customer account"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    try:
        # Check if customer exists
        c.execute("SELECT name, balance FROM customers WHERE username = ?", (username,))
        customer = c.fetchone()
        
        if not customer:
            conn.close()
            return False, "Customer not found"
        
        name, current_balance = customer
        new_balance = current_balance + amount
        
        # Update customer balance
        c.execute("UPDATE customers SET balance = ? WHERE username = ?", (new_balance, username))
        
        # Record topup history
        c.execute('''INSERT INTO topup_history 
                    (username, amount, previous_balance, new_balance)
                    VALUES (?, ?, ?, ?)''',
                 (username, amount, current_balance, new_balance))
        
        conn.commit()
        conn.close()
        
        # Update current user balance
        global current_user
        if current_user and current_user.get('username') == username:
            current_user['balance'] = new_balance
            current_user['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return True, f"✅ Successfully added ¥{amount:.2f} to {name}. New balance: ¥{new_balance:.2f}"
        
    except Exception as e:
        conn.close()
        return False, f"❌ Error adding value: {str(e)}"

def get_topup_history(username=None):
    """Get topup history"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    if username:
        c.execute('''SELECT th.amount, th.previous_balance, th.new_balance, 
                            th.topup_time, c.name
                     FROM topup_history th
                     JOIN customers c ON th.username = c.username
                     WHERE th.username = ?
                     ORDER BY th.topup_time DESC''', (username,))
    else:
        c.execute('''SELECT th.amount, th.previous_balance, th.new_balance, 
                            th.topup_time, c.name, c.username
                     FROM topup_history th
                     JOIN customers c ON th.username = c.username
                     ORDER BY th.topup_time DESC LIMIT 50''')
    
    history = c.fetchall()
    conn.close()
    return history

# Database operations
def get_customer_info(username):
    """Get customer information including password hash, reward points, and admin status"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    try:
        c.execute("SELECT name, balance, reward_points, password_hash, is_admin FROM customers WHERE username = ?", (username,))
        result = c.fetchone()
        return result
    except Exception as e:
        return None
    finally:
        conn.close()

def authenticate_user(username, password):
    """Authenticate user with username and password"""
    customer_info = get_customer_info(username)
    if not customer_info:
        return False, "Username not found"
    
    name, balance, reward_points, password_hash, is_admin = customer_info
    
    # Check login attempts
    can_attempt, message = check_login_attempts(username)
    if not can_attempt:
        return False, message
    
    # Verify password
    if verify_password(password, password_hash):
        record_login_attempt(username, True)
        session['authenticated'] = True
        session['username'] = username
        session['user_name'] = name
        session['is_admin'] = bool(is_admin)
        
        # Update current user
        global current_user
        current_user = {
            'username': username,
            'name': name,
            'balance': balance,
            'reward_points': reward_points,
            'is_admin': bool(is_admin),
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'authenticated': True
        }
        
        return True, "Authentication successful"
    else:
        record_login_attempt(username, False)
        remaining_attempts = 5 - login_attempts[username]['count']
        if remaining_attempts <= 0:
            return False, "Account locked. Too many failed attempts."
        else:
            return False, f"Invalid password. {remaining_attempts} attempts remaining."

# Initialize database
if not database_initialized:
    init_database()

def is_authenticated():
    """Check if user is authenticated"""
    return session.get('authenticated', False)

def is_admin():
    """Check if user is admin"""
    return session.get('is_admin', False)

def get_current_user():
    """Get current authenticated user information"""
    if is_authenticated():
        return {
            'username': session.get('username'),
            'name': session.get('user_name'),
            'is_admin': session.get('is_admin', False)
        }
    return None

def logout_user():
    """User logout"""
    global current_user
    session.clear()
    current_user = None

def get_products():
    """Get all products"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT id, name, price, category, barcode, stock, reward_points_cost FROM products ORDER BY category, name")
    products = c.fetchall()
    conn.close()
    return products

def get_products_by_barcodes(barcodes):
    """Get products by barcode list"""
    if not barcodes:
        return []
    
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    placeholders = ','.join('?' * len(barcodes))
    query = f"SELECT id, name, price, category, barcode, stock, reward_points_cost FROM products WHERE barcode IN ({placeholders}) ORDER BY name"
    
    c.execute(query, barcodes)
    products = c.fetchall()
    conn.close()
    
    return products

def get_product_by_barcode(barcode):
    """Get product by barcode"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT id, name, price, category, barcode, stock, reward_points_cost FROM products WHERE barcode = ?", (barcode,))
    product = c.fetchone()
    conn.close()
    return product

def get_product_categories():
    """Get unique product categories"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT DISTINCT category FROM products ORDER BY category")
    categories = [row[0] for row in c.fetchall()]
    conn.close()
    return categories

def add_product(name, price, category, barcode, stock, reward_points_cost=0):
    """Add new product"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO products (name, price, category, barcode, stock, reward_points_cost) VALUES (?, ?, ?, ?, ?, ?)",
                 (name, price, category, barcode, stock, reward_points_cost))
        conn.commit()
        conn.close()
        return True, "Product added successfully"
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Barcode already exists"
    except Exception as e:
        conn.close()
        return False, f"Error adding product: {str(e)}"

def update_product(product_id, name, price, category, barcode, stock, reward_points_cost=0):
    """Update existing product"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    try:
        # Check if barcode is being changed and if new barcode already exists
        if barcode:
            c.execute("SELECT id FROM products WHERE barcode = ? AND id != ?", (barcode, product_id))
            if c.fetchone():
                conn.close()
                return False, "Barcode already exists for another product"
        
        c.execute("UPDATE products SET name=?, price=?, category=?, barcode=?, stock=?, reward_points_cost=? WHERE id=?",
                 (name, price, category, barcode, stock, reward_points_cost, product_id))
        conn.commit()
        conn.close()
        return True, "Product updated successfully"
    except Exception as e:
        conn.close()
        return False, f"Error updating product: {str(e)}"

def delete_product(product_id):
    """Delete product"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    try:
        c.execute("DELETE FROM products WHERE id=?", (product_id,))
        conn.commit()
        conn.close()
        return True, "Product deleted successfully"
    except Exception as e:
        conn.close()
        return False, f"Error deleting product: {str(e)}"

def record_purchase(username, product_id, quantity, use_points=False):
    """Record purchase with reward points"""
    print(f"🔔 record_purchase called: user={username}, product={product_id}, qty={quantity}, use_points={use_points}")
    
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    try:
        # Get product information including barcode
        c.execute("SELECT name, price, stock, reward_points_cost, barcode FROM products WHERE id = ?", (product_id,))
        product = c.fetchone()
        
        if not product:
            print(f"❌ Product {product_id} not found")
            conn.close()
            return False, "Product not found"
        
        product_name, unit_price, stock, points_cost, barcode = product
        print(f"📦 Product found: {product_name}, Price: {unit_price}, Stock: {stock}")
        
        if use_points:
            # Reward points redemption
            if points_cost <= 0:
                conn.close()
                return False, "This product cannot be purchased with points"
            
            total_points_cost = points_cost * quantity
            
            # Check customer points balance
            c.execute("SELECT reward_points FROM customers WHERE username = ?", (username,))
            result = c.fetchone()
            if not result:
                conn.close()
                return False, "Customer not found"
            
            customer_points = result[0]
            
            if customer_points < total_points_cost:
                conn.close()
                return False, "Insufficient reward points"
            
            # Check stock
            if stock < quantity:
                conn.close()
                return False, "Insufficient stock"
            
            # Record reward redemption
            c.execute('''INSERT INTO reward_redemptions 
                        (username, product_id, product_name, points_used)
                        VALUES (?, ?, ?, ?)''',
                     (username, product_id, product_name, total_points_cost))
            
            # Update customer points
            c.execute("UPDATE customers SET reward_points = reward_points - ? WHERE username = ?",
                     (total_points_cost, username))
            
            # Update product stock
            c.execute("UPDATE products SET stock = stock - ? WHERE id = ?",
                     (quantity, product_id))
            
            conn.commit()
            conn.close()
            print(f"✅ Reward redemption recorded: {product_name} x{quantity}")
            return True, f"Reward redemption successful! Used {total_points_cost} points"
            
        else:
            # Normal purchase with money
            # Check stock
            if stock < quantity:
                conn.close()
                return False, "Insufficient stock"
            
            total_price = unit_price * quantity
            
            # Check customer balance
            c.execute("SELECT balance FROM customers WHERE username = ?", (username,))
            result = c.fetchone()
            if not result:
                conn.close()
                return False, "Customer not found"
            
            customer_balance = result[0]
            
            if customer_balance < total_price:
                conn.close()
                return False, "Insufficient balance"
            
            # Calculate earned points (0.001 points per 1 currency unit)
            earned_points = total_price * 0.001
            
            # Record purchase history with barcode
            c.execute('''INSERT INTO purchase_history 
                        (username, product_id, product_name, barcode, quantity, unit_price, total_price, earned_points)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (username, product_id, product_name, barcode, quantity, unit_price, total_price, earned_points))
            
            print(f"📝 Purchase history recorded: {product_name} x{quantity}, ¥{total_price}")
            
            # Update customer balance
            c.execute("UPDATE customers SET balance = balance - ? WHERE username = ?",
                     (total_price, username))
            
            print(f"💰 Updated customer balance: -¥{total_price}")
            
            # Update customer reward points
            c.execute("UPDATE customers SET reward_points = reward_points + ? WHERE username = ?",
                     (earned_points, username))
            
            print(f"⭐ Updated customer points: +{earned_points}")
            
            # Update product stock
            c.execute("UPDATE products SET stock = stock - ? WHERE id = ?",
                     (quantity, product_id))
            
            print(f"📦 Updated product stock: -{quantity}")
            
            conn.commit()
            conn.close()
            print(f"✅ Purchase recorded successfully")
            return True, f"Purchase successful! Earned {earned_points:.3f} points"
    
    except sqlite3.Error as e:
        print(f"❌ SQLite error in record_purchase: {e}")
        conn.rollback()
        conn.close()
        return False, f"Database error: {str(e)}"
    except Exception as e:
        print(f"❌ General error in record_purchase: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
        conn.close()
        return False, f"Error: {str(e)}"

def get_purchase_history(username=None):
    """Get purchase history - if username provided, only get that user's history"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    if username:
        c.execute('''SELECT ph.product_name, ph.quantity, ph.unit_price, ph.total_price, 
                            ph.earned_points, ph.purchase_time, c.name, ph.barcode
                     FROM purchase_history ph
                     JOIN customers c ON ph.username = c.username
                     WHERE ph.username = ?
                     ORDER BY ph.purchase_time DESC''', (username,))
    else:
        c.execute('''SELECT ph.product_name, ph.quantity, ph.unit_price, ph.total_price, 
                            ph.earned_points, ph.purchase_time, c.name, c.username, ph.barcode
                     FROM purchase_history ph
                     JOIN customers c ON ph.username = c.username
                     ORDER BY ph.purchase_time DESC LIMIT 50''')
    
    history = c.fetchall()
    conn.close()
    return history

def get_reward_redemptions(username=None):
    """Get reward redemption history"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    if username:
        c.execute('''SELECT rr.product_name, rr.points_used, rr.redemption_time, c.name
                     FROM reward_redemptions rr
                     JOIN customers c ON rr.username = c.username
                     WHERE rr.username = ?
                     ORDER BY rr.redemption_time DESC''', (username,))
    else:
        c.execute('''SELECT rr.product_name, rr.points_used, rr.redemption_time, c.name, c.username
                     FROM reward_redemptions rr
                     JOIN customers c ON rr.username = c.username
                     ORDER BY rr.redemption_time DESC LIMIT 50''')
    
    history = c.fetchall()
    conn.close()
    return history

def refresh_current_user_balance():
    """Refresh the current user balance and points from database"""
    global current_user
    if current_user and current_user.get('username'):
        customer_info = get_customer_info(current_user['username'])
        if customer_info:
            name, balance, reward_points, _, is_admin = customer_info
            current_user['balance'] = balance
            current_user['reward_points'] = reward_points
            current_user['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return True
    return False

def add_scanned_barcode(barcode):
    """Add scanned barcode to the queue with timestamp checking"""
    global last_barcode_time, scanned_barcodes
    
    current_time = time.time()
    if current_time - last_barcode_time < BARCODE_TIMEOUT:
        print(f"Ignoring rapid barcode scan: {barcode}")
        return False
    
    # Check if product exists
    product = get_product_by_barcode(barcode)
    if not product:
        print(f"No product found for barcode: {barcode}")
        return False
    
    scanned_barcodes.append(barcode)
    last_barcode_time = current_time
    
    print(f"Barcode scanned: {barcode} -> {product[1]}")
    return True

def clear_scanned_barcodes():
    """Clear all scanned barcodes"""
    global scanned_barcodes
    scanned_barcodes.clear()

def get_scanned_products():
    """Get product information for scanned barcodes"""
    products = []
    barcode_counts = {}
    
    # Count occurrences of each barcode
    for barcode in scanned_barcodes:
        barcode_counts[barcode] = barcode_counts.get(barcode, 0) + 1
    
    # Get product details for each unique barcode
    for barcode, count in barcode_counts.items():
        product = get_product_by_barcode(barcode)
        if product:
            product_id, name, price, category, barcode_val, stock, points_cost = product
            products.append({
                'id': product_id,
                'name': name,
                'price': price,
                'category': category,
                'barcode': barcode_val,
                'stock': stock,
                'points_cost': points_cost,
                'quantity': count
            })
    
    return products

# ============================================================================
# FLASK ROUTES - ALL PAGES IN ONE FILE
# ============================================================================

@app.route('/')
def index():
    """Main login page"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Smart Shopping System - Login</title>
        <meta charset="UTF-8">
        <style>
            body { 
                font-family: Arial; 
                margin: 0; 
                padding: 0; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
            }
            .login-container {
                background: white;
                padding: 2rem;
                border-radius: 10px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                width: 350px;
                max-width: 90%;
            }
            h1 {
                text-align: center;
                color: #333;
                margin-bottom: 1.5rem;
            }
            .form-group {
                margin-bottom: 1rem;
            }
            label {
                display: block;
                margin-bottom: 0.5rem;
                color: #555;
                font-weight: bold;
            }
            input {
                width: 100%;
                padding: 0.75rem;
                border: 2px solid #ddd;
                border-radius: 5px;
                font-size: 1rem;
                box-sizing: border-box;
                transition: border-color 0.3s;
            }
            input:focus {
                outline: none;
                border-color: #667eea;
            }
            .login-btn {
                width: 100%;
                padding: 0.75rem;
                background: #667eea;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 1rem;
                cursor: pointer;
                transition: background 0.3s;
            }
            .login-btn:hover {
                background: #5a67d8;
            }
            .login-btn:disabled {
                background: #ccc;
                cursor: not-allowed;
            }
            .feedback {
                margin-top: 1rem;
                padding: 0.75rem;
                border-radius: 5px;
                text-align: center;
                font-weight: bold;
                display: none;
            }
            .success {
                background: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }
            .error {
                background: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }
            .demo-accounts {
                margin-top: 2rem;
                padding-top: 1rem;
                border-top: 1px solid #eee;
                font-size: 0.9rem;
                color: #666;
            }
            .demo-accounts h3 {
                margin: 0 0 0.5rem 0;
                color: #333;
            }
            .demo-accounts p {
                margin: 0.25rem 0;
            }
        </style>
    </head>
    <body>
        <div class="login-container">
            <h1>🛒 Smart Shopping System</h1>
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" placeholder="Enter username" autocomplete="username">
            </div>
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" placeholder="Enter password" autocomplete="current-password">
            </div>
            <button class="login-btn" id="loginButton" onclick="login()">Login</button>
            <div id="feedback" class="feedback"></div>
            
            <div class="demo-accounts">
                <h3>Demo Accounts:</h3>
                <p><strong>admin</strong> / admin123 (Admin)</p>
                <p><strong>william</strong> / 12345678 (Admin)</p>
                <p><strong>user</strong> / demo123 (Regular User)</p>
            </div>
        </div>

        <script>
            function showFeedback(message, isSuccess) {
                const feedback = document.getElementById('feedback');
                feedback.textContent = message;
                feedback.className = isSuccess ? 'feedback success' : 'feedback error';
                feedback.style.display = 'block';
            }
            
            function login() {
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                
                if (!username || !password) {
                    showFeedback('Please enter username and password', false);
                    return;
                }
                
                const loginBtn = document.getElementById('loginButton');
                const originalText = loginBtn.textContent;
                loginBtn.textContent = 'Logging in...';
                loginBtn.disabled = true;
                
                fetch('/api/login', {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    body: JSON.stringify({
                        username: username,
                        password: password
                    })
                })
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP error! status: ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.success) {
                        showFeedback('✅ ' + data.message, true);
                        setTimeout(() => {
                            if (data.is_admin) {
                                window.location.href = '/admin';
                            } else {
                                window.location.href = '/shopping';
                            }
                        }, 1000);
                    } else {
                        showFeedback('❌ ' + data.message, false);
                        document.getElementById('password').value = '';
                        document.getElementById('password').focus();
                    }
                })
                .catch(error => {
                    console.error('Login error:', error);
                    showFeedback('❌ Network error. Please try again.', false);
                })
                .finally(() => {
                    loginBtn.textContent = originalText;
                    loginBtn.disabled = false;
                });
            }
            
            // Handle Enter key
            document.addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    login();
                }
            });
        </script>
    </body>
    </html>
    '''

@app.route('/shopping')
def shopping_page():
    """Shopping page - Requires authentication"""
    if not is_authenticated():
        return '''
        <script>
            alert("❌ Please login first");
            window.location.href = "/";
        </script>
        '''
    
    # Get scanned products first
    scanned_products = get_scanned_products()
    
    # Create JavaScript for initial cart
    cart_init = '{}'
    if scanned_products:
        cart_items = []
        for product in scanned_products:
            cart_items.append(f'"{product["id"]}": {product["quantity"]}')
        cart_init = '{' + ','.join(cart_items) + '}'
    
    # If there are scanned products, only show those
    if scanned_products:
        products_html = ""
        for product in scanned_products:
            products_html += f'''
            <div class="product-card" data-id="{product['id']}">
                <h3>{product['name']}</h3>
                <p class="category">{product['category']}</p>
                <p class="price">¥{product['price']:.2f}</p>
                <p class="stock">Stock: {product['stock']}</p>
                <div style="font-size: 0.8rem; color: #666; margin: 0.5rem 0;">
                    Barcode: {product['barcode']}
                </div>
                <div class="quantity-controls" data-product-id="{product['id']}">
                    <button class="decrease-btn">-</button>
                    <span id="quantity-{product['id']}" class="quantity-display">{product['quantity']}</span>
                    <button class="increase-btn">+</button>
                </div>
            </div>
            '''
    else:
        # Otherwise show all products with price > 0
        products = get_products()
        shopping_products = [p for p in products if p[2] > 0]
        
        products_html = ""
        for product in shopping_products:
            product_id, name, price, category, barcode, stock, points_cost = product
            products_html += f'''
            <div class="product-card" data-id="{product_id}">
                <h3>{name}</h3>
                <p class="category">{category}</p>
                <p class="price">¥{price:.2f}</p>
                <p class="stock">Stock: {stock}</p>
                <div style="font-size: 0.8rem; color: #666; margin: 0.5rem 0;">
                    Barcode: {barcode}
                </div>
                <div class="quantity-controls" data-product-id="{product_id}">
                    <button class="decrease-btn">-</button>
                    <span id="quantity-{product_id}" class="quantity-display">0</span>
                    <button class="increase-btn">+</button>
                </div>
            </div>
            '''
    
    html_template = f'''<!DOCTYPE html>
<html>
<head>
    <title>Shopping - Smart Shopping System</title>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial; margin: 0; padding: 0; background: #f5f5f5; }}
        .header {{ background: #2c3e50; color: white; padding: 1rem; }}
        .nav {{ display: flex; gap: 1rem; }}
        .nav a {{ color: white; text-decoration: none; padding: 0.5rem 1rem; border-radius: 4px; }}
        .nav a:hover {{ background: #34495e; }}
        .nav a.active {{ background: #3498db; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 1rem; }}
        
        .user-info {{
            background: #3498db;
            color: white;
            padding: 0.5rem 1rem;
            border-radius: 4px;
            margin-bottom: 1rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .points-display {{
            background: #2980b9;
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-weight: bold;
        }}
        .logout-btn {{
            background: #e74c3c;
            color: white;
            border: none;
            padding: 0.25rem 0.5rem;
            border-radius: 3px;
            cursor: pointer;
        }}
        
        .products-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 1rem;
            margin: 2rem 0;
        }}
        
        .product-card {{
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }}
        
        .product-card h3 {{ margin: 0 0 0.5rem 0; color: #2c3e50; }}
        .category {{ color: #7f8c8d; font-size: 0.9rem; margin: 0.25rem 0; }}
        .price {{ font-size: 1.5rem; font-weight: bold; color: #e74c3c; margin: 0.5rem 0; }}
        .stock {{ color: #27ae60; margin: 0.25rem 0; }}
        
        .quantity-controls {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            margin: 1rem 0;
        }}
        
        .quantity-controls button {{
            background: #3498db;
            color: white;
            border: none;
            width: 30px;
            height: 30px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 1rem;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        
        .quantity-controls button:hover {{
            background: #2980b9;
        }}
        
        .quantity-display {{
            font-size: 1.2rem;
            font-weight: bold;
            min-width: 30px;
            text-align: center;
        }}
        
        .cart-summary {{
            background: white;
            padding: 1.5rem;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-top: 2rem;
        }}
        
        .checkout-btn {{
            background: #27ae60;
            color: white;
            border: none;
            padding: 1rem 2rem;
            border-radius: 4px;
            cursor: pointer;
            font-size: 1.1rem;
            margin-top: 1rem;
            width: 100%;
        }}
        .checkout-btn:hover {{
            background: #219a52;
        }}
        .checkout-btn:disabled {{
            background: #95a5a6;
            cursor: not-allowed;
        }}
        
        .barcode-hint {{
            background: #fff3cd;
            color: #856404;
            padding: 0.75rem;
            border-radius: 4px;
            margin: 1rem 0;
            border: 1px solid #ffeaa7;
        }}
        
        .cart-item {{
            display: flex;
            justify-content: space-between;
            padding: 0.5rem;
            border-bottom: 1px solid #eee;
        }}
        
        .empty-cart {{
            text-align: center;
            color: #7f8c8d;
            padding: 2rem;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="container">
            <h1>🛒 Smart Shopping System</h1>
            <div class="nav">
                <a href="/">Home</a>
                <a href="/shopping" class="active">Shopping</a>
                <a href="/rewards">Rewards Shop</a>
                <a href="/history">My Purchase History</a>
                <a href="/topup">My Top-up History</a>
                <a href="/reward_history">My Reward History</a>
                <a href="/admin">Admin Panel</a>
            </div>
        </div>
    </div>
    
    <div class="container">
        <div class="user-info">
            <span>Welcome, {session.get('user_name', 'User')}</span>
            <div class="points-display" id="pointsDisplay">
                ⭐ Loading points...
            </div>
            <button class="logout-btn" onclick="logout()">Logout</button>
        </div>
        
        <h2>🛍️ Product List</h2>
        {f'''
        <div class="barcode-hint">
            📦 <strong>Scanned Products Mode</strong> - Showing only products scanned via barcode scanner.
            <a href="/shopping?all=1" style="color: #3498db; margin-left: 1rem;">Show All Products</a>
        </div>
        ''' if scanned_products else '''
        <div class="barcode-hint">
            📦 <strong>All Products Mode</strong> - Use barcode scanner on the home page or click products to add to cart.
        </div>
        '''}
        
        <div class="products-grid" id="productsGrid">
            {products_html}
        </div>
        
        <div class="cart-summary">
            <h3>🛒 Shopping Cart</h3>
            <div id="cartItems" style="min-height: 100px;">
                <div class="empty-cart">Your cart is empty</div>
            </div>
            <div id="cartTotal" style="font-size: 1.5rem; font-weight: bold; margin: 1rem 0; text-align: right;"></div>
            <div id="pointsEarned" style="color: #8e44ad; font-weight: bold; margin: 0.5rem 0; text-align: right;"></div>
            <button class="checkout-btn" id="checkoutButton" onclick="checkout()" disabled>Checkout & Pay</button>
        </div>
    </div>

    <script>
        // Initialize cart
        let cart = {cart_init};
        let userPoints = 0;
        
        console.log('Initial cart:', cart);
        
        // Event delegation for quantity buttons
        document.addEventListener('DOMContentLoaded', function() {{
            document.addEventListener('click', function(event) {{
                if (event.target.classList.contains('increase-btn')) {{
                    const controls = event.target.closest('.quantity-controls');
                    const productId = controls.getAttribute('data-product-id');
                    changeQuantity(productId, 1);
                }}
                
                if (event.target.classList.contains('decrease-btn')) {{
                    const controls = event.target.closest('.quantity-controls');
                    const productId = controls.getAttribute('data-product-id');
                    changeQuantity(productId, -1);
                }}
            }});
            
            updateAllQuantityDisplays();
            updateCartDisplay();
            loadUserPoints();
        }});
        
        function logout() {{
            fetch('/api/logout', {{ method: 'POST' }})
                .then(() => {{
                    window.location.href = "/";
                }});
        }}
        
        function loadUserPoints() {{
            fetch('/api/user_points')
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        userPoints = data.points;
                        document.getElementById('pointsDisplay').innerHTML = 
                            `⭐ ${{userPoints.toFixed(3)}} Reward Points`;
                    }}
                }})
                .catch(error => {{
                    console.error('Error loading points:', error);
                }});
        }}
        
        function changeQuantity(productId, change) {{
            console.log('Changing quantity:', productId, change);
            
            if (!cart[productId]) {{
                cart[productId] = 0;
            }}
            
            const newQuantity = cart[productId] + change;
            
            if (newQuantity < 0) return;
            
            if (change > 0) {{
                const productCard = document.querySelector(`[data-id="${{productId}}"]`);
                if (productCard) {{
                    const stockText = productCard.querySelector('.stock').textContent;
                    const stockMatch = stockText.match(/Stock: (\\d+)/);
                    if (stockMatch) {{
                        const stock = parseInt(stockMatch[1]);
                        if (newQuantity > stock) {{
                            alert(`❌ Cannot add more. Only ${{stock}} in stock.`);
                            return;
                        }}
                    }}
                }}
            }}
            
            cart[productId] = newQuantity;
            console.log('Updated cart:', cart);
            
            updateQuantityDisplay(productId);
            updateCartDisplay();
        }}
        
        function updateQuantityDisplay(productId) {{
            const displayElement = document.getElementById(`quantity-${{productId}}`);
            if (displayElement) {{
                displayElement.textContent = cart[productId] || 0;
            }}
        }}
        
        function updateAllQuantityDisplays() {{
            for (const productId in cart) {{
                updateQuantityDisplay(productId);
            }}
        }}
        
        function updateCartDisplay() {{
            const cartItems = document.getElementById('cartItems');
            const cartTotal = document.getElementById('cartTotal');
            const pointsEarned = document.getElementById('pointsEarned');
            const checkoutButton = document.getElementById('checkoutButton');
            
            let itemsHtml = '';
            let total = 0;
            let itemCount = 0;
            
            for (const productId in cart) {{
                const quantity = cart[productId];
                if (quantity > 0) {{
                    itemCount++;
                    const productCard = document.querySelector(`[data-id="${{productId}}"]`);
                    if (productCard) {{
                        const productName = productCard.querySelector('h3').textContent;
                        const price = parseFloat(productCard.querySelector('.price').textContent.replace('¥', ''));
                        const itemTotal = price * quantity;
                        total += itemTotal;
                        
                        itemsHtml += `
                            <div class="cart-item">
                                <div>
                                    <strong>${{productName}}</strong>
                                    <div style="font-size: 0.9rem; color: #666;">
                                        ¥${{price.toFixed(2)}} × ${{quantity}}
                                    </div>
                                </div>
                                <div style="font-weight: bold;">
                                    ¥${{itemTotal.toFixed(2)}}
                                </div>
                            </div>
                        `;
                    }}
                }}
            }}
            
            if (itemCount > 0) {{
                cartItems.innerHTML = itemsHtml;
                cartTotal.textContent = `Total: ¥${{total.toFixed(2)}}`;
                
                const pointsToEarn = total * 0.001;
                pointsEarned.textContent = `You will earn: ${{pointsToEarn.toFixed(3)}} points`;
                
                checkoutButton.disabled = false;
            }} else {{
                cartItems.innerHTML = '<div class="empty-cart">Your cart is empty</div>';
                cartTotal.textContent = '';
                pointsEarned.textContent = '';
                checkoutButton.disabled = true;
            }}
        }}
        
        function checkout() {{
            const items = [];
            for (const productId in cart) {{
                const quantity = cart[productId];
                if (quantity > 0) {{
                    items.push({{
                        productId: parseInt(productId),
                        quantity: parseInt(quantity)
                    }});
                }}
            }}
            
            if (items.length === 0) {{
                alert('❌ Please add products to cart first');
                return;
            }}
            
            console.log('Checkout items:', items);
            
            const checkoutButton = document.getElementById('checkoutButton');
            checkoutButton.disabled = true;
            checkoutButton.textContent = 'Processing...';
            
            fetch('/api/purchase', {{
                method: 'POST',
                headers: {{ 
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }},
                body: JSON.stringify({{items: items}})
            }})
            .then(response => {{
                if (!response.ok) {{
                    throw new Error(`HTTP error! status: ${{response.status}}`);
                }}
                return response.json();
            }})
            .then(data => {{
                console.log('Purchase response:', data);
                
                if (data.success) {{
                    alert('✅ ' + data.message);
                    
                    cart = {{}};
                    
                    updateAllQuantityDisplays();
                    updateCartDisplay();
                    loadUserPoints();
                    
                    fetch('/api/clear_barcodes', {{ method: 'POST' }});
                    
                    setTimeout(() => {{
                        window.location.href = '/';
                    }}, 2000);
                }} else {{
                    alert('❌ ' + data.message);
                    checkoutButton.disabled = false;
                    checkoutButton.textContent = 'Checkout & Pay';
                }}
            }})
            .catch(error => {{
                console.error('Checkout error:', error);
                alert('❌ Network error. Please check connection and try again.');
                checkoutButton.disabled = false;
                checkoutButton.textContent = 'Checkout & Pay';
            }});
        }}
        
        setInterval(loadUserPoints, 10000);
    </script>
</body>
</html>'''
    
    return html_template

@app.route('/rewards')
def rewards_page():
    """Rewards shop page"""
    if not is_authenticated():
        return '''
        <script>
            alert("❌ Please login first");
            window.location.href = "/";
        </script>
        '''
    
    products = get_products()
    reward_products = [p for p in products if p[6] > 0]
    
    products_html = ""
    for product in reward_products:
        product_id, name, price, category, barcode, stock, points_cost = product
        products_html += f'''
        <div class="product-card" data-id="{product_id}">
            <h3>{name}</h3>
            <p class="category">{category}</p>
            <p class="points-cost">⭐ {points_cost:.1f} Points</p>
            <p class="stock">Stock: {stock}</p>
            <div style="font-size: 0.8rem; color: #666; margin: 0.5rem 0;">
                Barcode: {barcode}
            </div>
            <div class="quantity-controls">
                <button onclick="changeQuantity({product_id}, -1)">-</button>
                <span id="quantity-{product_id}">0</span>
                <button onclick="changeQuantity({product_id}, 1)">+</button>
            </div>
        </div>
        '''
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Rewards Shop - Smart Shopping System</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial; margin: 0; padding: 0; background: #f5f5f5; }}
            .header {{ background: #2c3e50; color: white; padding: 1rem; }}
            .nav {{ display: flex; gap: 1rem; }}
            .nav a {{ color: white; text-decoration: none; padding: 0.5rem 1rem; border-radius: 4px; }}
            .nav a:hover {{ background: #34495e; }}
            .nav a.active {{ background: #8e44ad; }}
            .container {{ max-width: 1200px; margin: 0 auto; padding: 1rem; }}
            
            .user-info {{
                background: #8e44ad;
                color: white;
                padding: 0.5rem 1rem;
                border-radius: 4px;
                margin-bottom: 1rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .points-display {{
                background: #9b59b6;
                padding: 0.5rem 1rem;
                border-radius: 20px;
                font-weight: bold;
            }}
            .logout-btn {{
                background: #e74c3c;
                color: white;
                border: none;
                padding: 0.25rem 0.5rem;
                border-radius: 3px;
                cursor: pointer;
            }}
            
            .products-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
                gap: 1rem;
                margin: 2rem 0;
            }}
            
            .product-card {{
                background: white;
                padding: 1.5rem;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                text-align: center;
                border: 2px solid #9b59b6;
            }}
            
            .product-card h3 {{ margin: 0 0 0.5rem 0; color: #2c3e50; }}
            .category {{ color: #7f8c8d; font-size: 0.9rem; margin: 0.25rem 0; }}
            .points-cost {{ font-size: 1.5rem; font-weight: bold; color: #8e44ad; margin: 0.5rem 0; }}
            .stock {{ color: #27ae60; margin: 0.25rem 0; }}
            
            .quantity-controls {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.5rem;
                margin: 1rem 0;
            }}
            
            .quantity-controls button {{
                background: #8e44ad;
                color: white;
                border: none;
                width: 30px;
                height: 30px;
                border-radius: 50%;
                cursor: pointer;
            }}
            
            .cart-summary {{
                background: white;
                padding: 1.5rem;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                margin-top: 2rem;
                border: 2px solid #8e44ad;
            }}
            
            .redeem-btn {{
                background: #8e44ad;
                color: white;
                border: none;
                padding: 1rem 2rem;
                border-radius: 4px;
                cursor: pointer;
                font-size: 1.1rem;
                margin-top: 1rem;
            }}
            .redeem-btn:hover {{
                background: #7d3c98;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="container">
                <h1>🛒 Smart Shopping System</h1>
                <div class="nav">
                    <a href="/">Home</a>
                    <a href="/shopping">Shopping</a>
                    <a href="/rewards" class="active">Rewards Shop</a>
                    <a href="/history">My Purchase History</a>
                    <a href="/topup">My Top-up History</a>
                    <a href="/reward_history">My Reward History</a>
                    <a href="/admin">Admin Panel</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <div class="user-info">
                <span>Welcome, {session.get('user_name', 'User')}</span>
                <div class="points-display" id="pointsDisplay">
                    ⭐ Loading points...
                </div>
                <button class="logout-btn" onclick="logout()">Logout</button>
            </div>
            
            <h2>🎁 Rewards Shop</h2>
            <p>Exchange your reward points for free products! Earn 0.001 points for every ¥1 spent.</p>
            
            <div class="products-grid" id="productsGrid">
                {products_html}
            </div>
            
            <div class="cart-summary">
                <h3>Reward Redemption Cart</h3>
                <div id="cartItems"></div>
                <div id="cartTotal" style="font-size: 1.5rem; font-weight: bold; margin: 1rem 0; color: #8e44ad;"></div>
                <button class="redeem-btn" onclick="redeemPoints()">Redeem Points</button>
            </div>
        </div>

        <script>
            let cart = {{}};
            let userPoints = 0;
            
            function logout() {{
                fetch('/api/logout', {{ method: 'POST' }})
                    .then(() => {{
                        window.location.href = "/";
                    }});
            }}
            
            function loadUserPoints() {{
                fetch('/api/user_points')
                    .then(response => response.json())
                    .then(data => {{
                        if (data.success) {{
                            userPoints = data.points;
                            document.getElementById('pointsDisplay').innerHTML = 
                                `⭐ ${{userPoints.toFixed(3)}} Reward Points`;
                        }}
                    }});
            }}
            
            function changeQuantity(productId, change) {{
                if (!cart[productId]) {{
                    cart[productId] = 0;
                }}
                
                cart[productId] += change;
                if (cart[productId] < 0) cart[productId] = 0;
                
                document.getElementById(`quantity-${{productId}}`).textContent = cart[productId];
                updateCartDisplay();
            }}
            
            function updateCartDisplay() {{
                const cartItems = document.getElementById('cartItems');
                const cartTotal = document.getElementById('cartTotal');
                
                let itemsHtml = '';
                let totalPoints = 0;
                let hasItems = false;
                
                for (const [productId, quantity] of Object.entries(cart)) {{
                    if (quantity > 0) {{
                        hasItems = true;
                        const productCard = document.querySelector(`[data-id="${{productId}}"]`);
                        const productName = productCard.querySelector('h3').textContent;
                        const pointsCost = parseFloat(productCard.querySelector('.points-cost').textContent.replace('⭐', '').replace('Points', '').trim());
                        const itemTotal = pointsCost * quantity;
                        totalPoints += itemTotal;
                        
                        itemsHtml += `<p>${{productName}} x ${{quantity}} = ⭐${{itemTotal.toFixed(1)}} Points</p>`;
                    }}
                }}
                
                cartItems.innerHTML = hasItems ? itemsHtml : '<p>Redemption cart is empty</p>';
                if (hasItems) {{
                    cartTotal.textContent = `Total Points: ⭐${{totalPoints.toFixed(1)}}`;
                    if (totalPoints > userPoints) {{
                        cartTotal.innerHTML += ` <span style="color: #e74c3c;">(Insufficient points!)</span>`;
                    }}
                }} else {{
                    cartTotal.textContent = '';
                }}
            }}
            
            function redeemPoints() {{
                const items = [];
                for (const [productId, quantity] of Object.entries(cart)) {{
                    if (quantity > 0) {{
                        items.push({{productId: parseInt(productId), quantity: quantity}});
                    }}
                }}
                
                if (items.length === 0) {{
                    alert('❌ Please select products first');
                    return;
                }}
                
                fetch('/api/redeem_points', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{items: items}})
                }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        alert('✅ ' + data.message);
                        cart = {{}};
                        updateCartDisplay();
                        loadUserPoints();
                        document.querySelectorAll('[id^="quantity-"]').forEach(el => {{
                            el.textContent = '0';
                        }});
                    }} else {{
                        alert('❌ ' + data.message);
                    }}
                }});
            }}
            
            loadUserPoints();
            updateCartDisplay();
            setInterval(loadUserPoints, 5000);
        </script>
    </body>
    </html>
    '''

@app.route('/history')
def history_page():
    """Purchase history page - Shows only user's own history"""
    if not is_authenticated():
        return '''
        <script>
            alert("❌ Please login first");
            window.location.href = "/";
        </script>
        '''
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>My Purchase History - Smart Shopping System</title>
        <meta charset="UTF-8">
        <style>
            body { font-family: Arial; margin: 0; padding: 0; background: #f5f5f5; }
            .header { background: #2c3e50; color: white; padding: 1rem; }
            .nav { display: flex; gap: 1rem; }
            .nav a { color: white; text-decoration: none; padding: 0.5rem 1rem; border-radius: 4px; }
            .nav a:hover { background: #34495e; }
            .nav a.active { background: #3498db; }
            .container { max-width: 1200px; margin: 0 auto; padding: 1rem; }
            
            .user-info {
                background: #3498db;
                color: white;
                padding: 0.5rem 1rem;
                border-radius: 4px;
                margin-bottom: 1rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .logout-btn {
                background: #e74c3c;
                color: white;
                border: none;
                padding: 0.25rem 0.5rem;
                border-radius: 3px;
                cursor: pointer;
            }
            
            .history-table {
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                overflow: hidden;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
            }
            
            th, td {
                padding: 1rem;
                text-align: left;
                border-bottom: 1px solid #ecf0f1;
            }
            
            th {
                background: #34495e;
                color: white;
            }
            
            tr:hover {
                background: #f8f9fa;
            }
            
            .points-earned {
                color: #27ae60;
                font-weight: bold;
            }
            
            .barcode-cell {
                font-family: monospace;
                font-size: 0.9rem;
                color: #666;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="container">
                <h1>🛒 Smart Shopping System</h1>
                <div class="nav">
                    <a href="/">Home</a>
                    <a href="/shopping">Shopping</a>
                    <a href="/rewards">Rewards Shop</a>
                    <a href="/history" class="active">My Purchase History</a>
                    <a href="/topup">My Top-up History</a>
                    <a href="/reward_history">My Reward History</a>
                    <a href="/admin">Admin Panel</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <div class="user-info">
                <span>Welcome, ''' + session.get('user_name', 'User') + '''</span>
                <button class="logout-btn" onclick="logout()">Logout</button>
            </div>
            
            <h2>📊 My Purchase History</h2>
            <div class="history-table">
                <table>
                    <thead>
                        <tr>
                            <th>Product</th>
                            <th>Quantity</th>
                            <th>Unit Price</th>
                            <th>Total Price</th>
                            <th>Points Earned</th>
                            <th>Barcode</th>
                            <th>Purchase Time</th>
                        </tr>
                    </thead>
                    <tbody id="historyTable">
                        <tr><td colspan="7" style="text-align: center;">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            function logout() {
                fetch('/api/logout', { method: 'POST' })
                    .then(() => {
                        window.location.href = "/";
                    });
            }
            
            function loadHistory() {
                fetch('/api/my_purchase_history')
                    .then(response => response.json())
                    .then(data => {
                        const table = document.getElementById('historyTable');
                        
                        if (data.history && data.history.length > 0) {
                            table.innerHTML = data.history.map(item => `
                                <tr>
                                    <td>${item.product_name}</td>
                                    <td>${item.quantity}</td>
                                    <td>¥${item.unit_price.toFixed(2)}</td>
                                    <td>¥${item.total_price.toFixed(2)}</td>
                                    <td class="points-earned">+${item.earned_points.toFixed(3)}</td>
                                    <td class="barcode-cell">${item.barcode || 'N/A'}</td>
                                    <td>${item.purchase_time}</td>
                                </tr>
                            `).join('');
                        } else {
                            table.innerHTML = '<tr><td colspan="7" style="text-align: center;">No purchase records found</td></tr>';
                        }
                    });
            }
            
            loadHistory();
            setInterval(loadHistory, 5000);
        </script>
    </body>
    </html>
    '''

@app.route('/topup')
def topup_page():
    """Top-up history page - Shows only user's own history"""
    if not is_authenticated():
        return '''
        <script>
            alert("❌ Please login first");
            window.location.href = "/";
        </script>
        '''
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>My Top-up History - Smart Shopping System</title>
        <meta charset="UTF-8">
        <style>
            body { font-family: Arial; margin: 0; padding: 0; background: #f5f5f5; }
            .header { background: #2c3e50; color: white; padding: 1rem; }
            .nav { display: flex; gap: 1rem; }
            .nav a { color: white; text-decoration: none; padding: 0.5rem 1rem; border-radius: 4px; }
            .nav a:hover { background: #34495e; }
            .nav a.active { background: #3498db; }
            .container { max-width: 1200px; margin: 0 auto; padding: 1rem; }
            
            .user-info {
                background: #3498db;
                color: white;
                padding: 0.5rem 1rem;
                border-radius: 4px;
                margin-bottom: 1rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .logout-btn {
                background: #e74c3c;
                color: white;
                border: none;
                padding: 0.25rem 0.5rem;
                border-radius: 3px;
                cursor: pointer;
            }
            
            .history-table {
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                overflow: hidden;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
            }
            
            th, td {
                padding: 1rem;
                text-align: left;
                border-bottom: 1px solid #ecf0f1;
            }
            
            th {
                background: #34495e;
                color: white;
            }
            
            tr:hover {
                background: #f8f9fa;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="container">
                <h1>🛒 Smart Shopping System</h1>
                <div class="nav">
                    <a href="/">Home</a>
                    <a href="/shopping">Shopping</a>
                    <a href="/rewards">Rewards Shop</a>
                    <a href="/history">My Purchase History</a>
                    <a href="/topup" class="active">My Top-up History</a>
                    <a href="/reward_history">My Reward History</a>
                    <a href="/admin">Admin Panel</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <div class="user-info">
                <span>Welcome, ''' + session.get('user_name', 'User') + '''</span>
                <button class="logout-btn" onclick="logout()">Logout</button>
            </div>
            
            <h2>💰 My Top-up History</h2>
            <div class="history-table">
                <table>
                    <thead>
                        <tr>
                            <th>Amount</th>
                            <th>Previous Balance</th>
                            <th>New Balance</th>
                            <th>Top-up Time</th>
                        </tr>
                    </thead>
                    <tbody id="topupTable">
                        <tr><td colspan="4" style="text-align: center;">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            function logout() {
                fetch('/api/logout', { method: 'POST' })
                    .then(() => {
                        window.location.href = "/";
                    });
            }
            
            function loadTopupHistory() {
                fetch('/api/my_topup_history')
                    .then(response => response.json())
                    .then(data => {
                        const table = document.getElementById('topupTable');
                        
                        if (data.history && data.history.length > 0) {
                            table.innerHTML = data.history.map(item => `
                                <tr>
                                    <td style="color: #27ae60; font-weight: bold;">+¥${item.amount.toFixed(2)}</td>
                                    <td>¥${item.previous_balance.toFixed(2)}</td>
                                    <td style="font-weight: bold;">¥${item.new_balance.toFixed(2)}</td>
                                    <td>${item.topup_time}</td>
                                </tr>
                            `).join('');
                        } else {
                            table.innerHTML = '<tr><td colspan="4" style="text-align: center;">No top-up records found</td></tr>';
                        }
                    });
            }
            
            loadTopupHistory();
            setInterval(loadTopupHistory, 5000);
        </script>
    </body>
    </html>
    '''

@app.route('/reward_history')
def reward_history_page():
    """Reward redemption history page"""
    if not is_authenticated():
        return '''
        <script>
            alert("❌ Please login first");
            window.location.href = "/";
        </script>
        '''
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>My Reward History - Smart Shopping System</title>
        <meta charset="UTF-8">
        <style>
            body { font-family: Arial; margin: 0; padding: 0; background: #f5f5f5; }
            .header { background: #2c3e50; color: white; padding: 1rem; }
            .nav { display: flex; gap: 1rem; }
            .nav a { color: white; text-decoration: none; padding: 0.5rem 1rem; border-radius: 4px; }
            .nav a:hover { background: #34495e; }
            .nav a.active { background: #8e44ad; }
            .container { max-width: 1200px; margin: 0 auto; padding: 1rem; }
            
            .user-info {
                background: #8e44ad;
                color: white;
                padding: 0.5rem 1rem;
                border-radius: 4px;
                margin-bottom: 1rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .logout-btn {
                background: #e74c3c;
                color: white;
                border: none;
                padding: 0.25rem 0.5rem;
                border-radius: 3px;
                cursor: pointer;
            }
            
            .history-table {
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                overflow: hidden;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
            }
            
            th, td {
                padding: 1rem;
                text-align: left;
                border-bottom: 1px solid #ecf0f1;
            }
            
            th {
                background: #34495e;
                color: white;
            }
            
            tr:hover {
                background: #f8f9fa;
            }
            
            .points-used {
                color: #8e44ad;
                font-weight: bold;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="container">
                <h1>🛒 Smart Shopping System</h1>
                <div class="nav">
                    <a href="/">Home</a>
                    <a href="/shopping">Shopping</a>
                    <a href="/rewards">Rewards Shop</a>
                    <a href="/history">My Purchase History</a>
                    <a href="/topup">My Top-up History</a>
                    <a href="/reward_history" class="active">My Reward History</a>
                    <a href="/admin">Admin Panel</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <div class="user-info">
                <span>Welcome, ''' + session.get('user_name', 'User') + '''</span>
                <button class="logout-btn" onclick="logout()">Logout</button>
            </div>
            
            <h2>🎁 My Reward Redemption History</h2>
            <div class="history-table">
                <table>
                    <thead>
                        <tr>
                            <th>Product</th>
                            <th>Points Used</th>
                            <th>Redemption Time</th>
                        </tr>
                    </thead>
                    <tbody id="rewardHistoryTable">
                        <tr><td colspan="3" style="text-align: center;">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            function logout() {
                fetch('/api/logout', { method: 'POST' })
                    .then(() => {
                        window.location.href = "/";
                    });
            }
            
            function loadRewardHistory() {
                fetch('/api/my_reward_history')
                    .then(response => response.json())
                    .then(data => {
                        const table = document.getElementById('rewardHistoryTable');
                        
                        if (data.history && data.history.length > 0) {
                            table.innerHTML = data.history.map(item => `
                                <tr>
                                    <td>${item.product_name}</td>
                                    <td class="points-used">⭐${item.points_used.toFixed(1)}</td>
                                    <td>${item.redemption_time}</td>
                                </tr>
                            `).join('');
                        } else {
                            table.innerHTML = '<tr><td colspan="3" style="text-align: center;">No reward redemption records found</td></tr>';
                        }
                    });
            }
            
            loadRewardHistory();
            setInterval(loadRewardHistory, 5000);
        </script>
    </body>
    </html>
    '''

@app.route('/admin')
def admin_page():
    """Admin panel for product management with barcode support"""
    if not is_authenticated() or not is_admin():
        return '''
        <script>
            alert("❌ Admin access required");
            window.location.href = "/";
        </script>
        '''
    
    products = get_products()
    categories = get_product_categories()
    
    products_html = ""
    for product in products:
        product_id, name, price, category, barcode, stock, points_cost = product
        products_html += f'''
        <tr>
            <td>{product_id}</td>
            <td><input type="text" id="name_{product_id}" value="{name}" class="form-input"></td>
            <td><input type="number" id="price_{product_id}" value="{price:.2f}" step="0.01" class="form-input"></td>
            <td>
                <select id="category_{product_id}" class="form-input">
                    <option value="">Select Category</option>
        '''
        
        for cat in categories:
            selected = "selected" if cat == category else ""
            products_html += f'<option value="{cat}" {selected}>{cat}</option>'
        
        products_html += f'''
                </select>
            </td>
            <td><input type="text" id="barcode_{product_id}" value="{barcode if barcode else ''}" class="form-input" placeholder="Enter barcode"></td>
            <td><input type="number" id="stock_{product_id}" value="{stock}" class="form-input"></td>
            <td><input type="number" id="points_{product_id}" value="{points_cost:.1f}" step="0.1" class="form-input"></td>
            <td>
                <button class="btn-update" onclick="updateProduct({product_id})">Update</button>
                <button class="btn-delete" onclick="deleteProduct({product_id})">Delete</button>
            </td>
        </tr>
        '''
    
    categories_options = "".join([f'<option value="{cat}">{cat}</option>' for cat in categories])
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin Panel - Smart Shopping System</title>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial; margin: 0; padding: 0; background: #f5f5f5; }}
            .header {{ background: #2c3e50; color: white; padding: 1rem; }}
            .nav {{ display: flex; gap: 1rem; }}
            .nav a {{ color: white; text-decoration: none; padding: 0.5rem 1rem; border-radius: 4px; }}
            .nav a:hover {{ background: #34495e; }}
            .nav a.active {{ background: #e67e22; }}
            .container {{ max-width: 1400px; margin: 0 auto; padding: 1rem; }}
            
            .user-info {{
                background: #e67e22;
                color: white;
                padding: 0.5rem 1rem;
                border-radius: 4px;
                margin-bottom: 1rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .logout-btn {{
                background: #e74c3c;
                color: white;
                border: none;
                padding: 0.25rem 0.5rem;
                border-radius: 3px;
                cursor: pointer;
            }}
            
            .admin-section {{
                background: white;
                padding: 1.5rem;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                margin-bottom: 2rem;
            }}
            
            .form-group {{
                margin-bottom: 1rem;
            }}
            
            .form-input {{
                width: 100%;
                padding: 0.5rem;
                border: 1px solid #ddd;
                border-radius: 4px;
                box-sizing: border-box;
            }}
            
            .btn-primary {{
                background: #3498db;
                color: white;
                border: none;
                padding: 0.75rem 1.5rem;
                border-radius: 4px;
                cursor: pointer;
            }}
            
            .btn-update {{
                background: #27ae60;
                color: white;
                border: none;
                padding: 0.25rem 0.5rem;
                border-radius: 3px;
                cursor: pointer;
                margin-right: 0.25rem;
            }}
            
            .btn-delete {{
                background: #e74c3c;
                color: white;
                border: none;
                padding: 0.25rem 0.5rem;
                border-radius: 3px;
                cursor: pointer;
            }}
            
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 1rem;
            }}
            
            th, td {{
                padding: 0.75rem;
                text-align: left;
                border-bottom: 1px solid #ecf0f1;
            }}
            
            th {{
                background: #34495e;
                color: white;
            }}
            
            tr:hover {{
                background: #f8f9fa;
            }}
            
            .feedback {{
                padding: 0.75rem;
                border-radius: 4px;
                margin: 1rem 0;
                font-weight: bold;
            }}
            
            .success {{
                background: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }}
            
            .error {{
                background: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }}
            
            .barcode-info {{
                background: #e3f2fd;
                color: #1565c0;
                padding: 0.5rem;
                border-radius: 4px;
                margin: 0.5rem 0;
                font-size: 0.9rem;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="container">
                <h1>🛒 Smart Shopping System - Admin Panel</h1>
                <div class="nav">
                    <a href="/">Home</a>
                    <a href="/shopping">Shopping</a>
                    <a href="/rewards">Rewards Shop</a>
                    <a href="/history">Purchase History</a>
                    <a href="/topup">Top-up History</a>
                    <a href="/reward_history">Reward History</a>
                    <a href="/admin" class="active">Admin Panel</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <div class="user-info">
                <span>👑 Admin Panel - Welcome, {session.get('user_name', 'Admin')}</span>
                <button class="logout-btn" onclick="logout()">Logout</button>
            </div>
            
            <div class="admin-section">
                <h2>➕ Add New Product</h2>
                <div class="barcode-info">
                    📦 <strong>Barcode Information:</strong> Enter EAN-13 barcode (13 digits) or custom barcode for products.
                    Scanned products will appear automatically in the shopping page.
                </div>
                <div class="form-group">
                    <label>Product Name:</label>
                    <input type="text" id="newProductName" class="form-input" placeholder="Enter product name">
                </div>
                <div class="form-group">
                    <label>Price (¥):</label>
                    <input type="number" id="newProductPrice" class="form-input" step="0.01" placeholder="0.00">
                </div>
                <div class="form-group">
                    <label>Category:</label>
                    <select id="newProductCategory" class="form-input">
                        <option value="">Select Category</option>
                        {categories_options}
                        <option value="new">+ Create New Category</option>
                    </select>
                    <input type="text" id="newCategoryInput" class="form-input" placeholder="Enter new category" style="display: none; margin-top: 0.5rem;">
                </div>
                <div class="form-group">
                    <label>Barcode (EAN-13):</label>
                    <input type="text" id="newProductBarcode" class="form-input" placeholder="Enter barcode (13 digits)">
                </div>
                <div class="form-group">
                    <label>Stock Quantity:</label>
                    <input type="number" id="newProductStock" class="form-input" value="100">
                </div>
                <div class="form-group">
                    <label>Reward Points Cost (for rewards shop):</label>
                    <input type="number" id="newProductPoints" class="form-input" step="0.1" value="0.0">
                </div>
                <button class="btn-primary" onclick="addNewProduct()">Add Product</button>
                <div id="addProductFeedback" class="feedback" style="display: none;"></div>
            </div>
            
            <div class="admin-section">
                <h2>📦 Product Management</h2>
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Name</th>
                            <th>Price (¥)</th>
                            <th>Category</th>
                            <th>Barcode</th>
                            <th>Stock</th>
                            <th>Points Cost</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {products_html}
                    </tbody>
                </table>
                <div id="updateFeedback" class="feedback" style="display: none;"></div>
            </div>
        </div>

        <script>
            function logout() {{
                fetch('/api/logout', {{ method: 'POST' }})
                    .then(() => {{
                        window.location.href = "/";
                    }});
            }}
            
            document.getElementById('newProductCategory').addEventListener('change', function() {{
                const newCategoryInput = document.getElementById('newCategoryInput');
                if (this.value === 'new') {{
                    newCategoryInput.style.display = 'block';
                }} else {{
                    newCategoryInput.style.display = 'none';
                }}
            }});
            
            function addNewProduct() {{
                const name = document.getElementById('newProductName').value;
                const price = parseFloat(document.getElementById('newProductPrice').value);
                let category = document.getElementById('newProductCategory').value;
                const barcode = document.getElementById('newProductBarcode').value;
                const stock = parseInt(document.getElementById('newProductStock').value);
                const points = parseFloat(document.getElementById('newProductPoints').value);
                
                if (category === 'new') {{
                    category = document.getElementById('newCategoryInput').value;
                }}
                
                if (!name || !price || !category || !barcode || !stock) {{
                    showFeedback('addProductFeedback', 'Please fill all fields', false);
                    return;
                }}
                
                fetch('/api/admin/add_product', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        name: name,
                        price: price,
                        category: category,
                        barcode: barcode,
                        stock: stock,
                        reward_points_cost: points
                    }})
                }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        showFeedback('addProductFeedback', '✅ ' + data.message, true);
                        document.getElementById('newProductName').value = '';
                        document.getElementById('newProductPrice').value = '';
                        document.getElementById('newProductBarcode').value = '';
                        document.getElementById('newProductStock').value = '100';
                        document.getElementById('newProductPoints').value = '0.0';
                        setTimeout(() => {{ window.location.reload(); }}, 2000);
                    }} else {{
                        showFeedback('addProductFeedback', '❌ ' + data.message, false);
                    }}
                }});
            }}
            
            function updateProduct(productId) {{
                const name = document.getElementById('name_' + productId).value;
                const price = parseFloat(document.getElementById('price_' + productId).value);
                const category = document.getElementById('category_' + productId).value;
                const barcode = document.getElementById('barcode_' + productId).value;
                const stock = parseInt(document.getElementById('stock_' + productId).value);
                const points = parseFloat(document.getElementById('points_' + productId).value);
                
                if (!name || !price || !category || !stock) {{
                    showFeedback('updateFeedback', 'Please fill all fields', false);
                    return;
                }}
                
                fetch('/api/admin/update_product', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        product_id: productId,
                        name: name,
                        price: price,
                        category: category,
                        barcode: barcode,
                        stock: stock,
                        reward_points_cost: points
                    }})
                }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        showFeedback('updateFeedback', '✅ Product updated successfully', true);
                        setTimeout(() => {{ 
                            document.getElementById('updateFeedback').style.display = 'none'; 
                        }}, 3000);
                    }} else {{
                        showFeedback('updateFeedback', '❌ ' + data.message, false);
                    }}
                }});
            }}
            
            function deleteProduct(productId) {{
                if (!confirm('Are you sure you want to delete this product?')) {{
                    return;
                }}
                
                fetch('/api/admin/delete_product', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ product_id: productId }})
                }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        showFeedback('updateFeedback', '✅ Product deleted successfully', true);
                        setTimeout(() => {{ window.location.reload(); }}, 2000);
                    }} else {{
                        showFeedback('updateFeedback', '❌ ' + data.message, false);
                    }}
                }});
            }}
            
            function showFeedback(elementId, message, isSuccess) {{
                const element = document.getElementById(elementId);
                element.textContent = message;
                element.className = isSuccess ? 'feedback success' : 'feedback error';
                element.style.display = 'block';
            }}
        </script>
    </body>
    </html>
    '''

# ============================================================================
# API ROUTES
# ============================================================================

@app.route('/api/login', methods=['POST'])
def api_login():
    """Handle user login with username and password"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data received'})
        
        username = data.get('username', '').lower().strip()
        password = data.get('password', '')
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password required'})
        
        # Authenticate with username and password
        success, message = authenticate_user(username, password)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Welcome {session.get("user_name")}! Login successful.',
                'user_name': session.get('user_name'),
                'is_admin': session.get('is_admin', False)
            })
        else:
            return jsonify({'success': False, 'message': message})
            
    except Exception as e:
        return jsonify({'success': False, 'message': 'Server error. Please try again.'})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    """User logout"""
    logout_user()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/auth_status')
def api_auth_status():
    """Get authentication status"""
    auth_info = get_current_user()
    return jsonify({
        'authenticated': is_authenticated(),
        'current_user': auth_info,
        'current_user_data': current_user
    })

@app.route('/api/user_points')
def api_user_points():
    """Get current user's reward points"""
    if not is_authenticated():
        return jsonify({'success': False, 'points': 0})
    
    username = session.get('username')
    customer_info = get_customer_info(username)
    
    if customer_info:
        return jsonify({'success': True, 'points': customer_info[2]})
    else:
        return jsonify({'success': False, 'points': 0})

@app.route('/api/purchase', methods=['POST'])
def api_purchase():
    """Handle purchase request - Requires authentication"""
    try:
        print("🔔 Purchase API called")
        
        if not is_authenticated():
            print("❌ Not authenticated")
            return jsonify({'success': False, 'message': 'Please login first'})
        
        data = request.get_json()
        print(f"📦 Received data: {data}")
        
        if not data:
            print("❌ No data received")
            return jsonify({'success': False, 'message': 'No data received'})
        
        items = data.get('items', [])
        print(f"🛍️ Items to purchase: {items}")
        
        if not items:
            print("❌ No items in cart")
            return jsonify({'success': False, 'message': 'No items in cart'})
        
        username = session.get('username')
        print(f"👤 Username: {username}")
        
        customer_info = get_customer_info(username)
        
        if not customer_info:
            print("❌ Customer not found")
            return jsonify({'success': False, 'message': 'Customer information not found'})
        
        name, balance, reward_points, _, _ = customer_info
        print(f"👤 Customer: {name}, Balance: {balance}, Points: {reward_points}")
        
        total_price = 0
        total_earned_points = 0
        
        for item in items:
            product_id = item.get('productId')
            quantity = item.get('quantity')
            
            print(f"📦 Processing item: product_id={product_id}, quantity={quantity}")
            
            if not product_id or not quantity:
                print(f"❌ Invalid item data: {item}")
                return jsonify({'success': False, 'message': f'Invalid item data: {item}'})
            
            # Get product details
            conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
            c = conn.cursor()
            c.execute("SELECT name, price, stock FROM products WHERE id = ?", (product_id,))
            product = c.fetchone()
            conn.close()
            
            if not product:
                print(f"❌ Product not found: {product_id}")
                return jsonify({'success': False, 'message': f'Product {product_id} not found'})
            
            product_name, product_price, stock = product
            
            if stock < quantity:
                print(f"❌ Insufficient stock: {product_name} (stock: {stock}, requested: {quantity})")
                return jsonify({'success': False, 'message': f'Insufficient stock for {product_name}'})
            
            item_total = product_price * quantity
            if balance < item_total:
                print(f"❌ Insufficient balance: needed {item_total}, have {balance}")
                return jsonify({'success': False, 'message': f'Insufficient balance for {product_name}'})
            
            success, message = record_purchase(username, product_id, quantity, use_points=False)
            if not success:
                print(f"❌ Purchase failed: {message}")
                return jsonify({'success': False, 'message': message})
            
            total_price += item_total
            total_earned_points += item_total * 0.001
        
        print(f"✅ Purchase successful! Total: ¥{total_price:.2f}, Earned points: {total_earned_points:.3f}")
        
        updated_info = get_customer_info(username)
        if updated_info:
            _, new_balance, new_points, _, _ = updated_info
            if current_user:
                current_user['balance'] = new_balance
                current_user['reward_points'] = new_points
        
        return jsonify({
            'success': True, 
            'message': f'✅ Purchase successful! Amount: ¥{total_price:.2f}, Earned: {total_earned_points:.3f} points', 
            'new_balance': updated_info[1] if updated_info else 0,
            'earned_points': total_earned_points
        })
        
    except Exception as e:
        print(f"🔥 ERROR in api_purchase: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Server error: {str(e)}'}), 500

@app.route('/api/redeem_points', methods=['POST'])
def api_redeem_points():
    """Handle reward points redemption"""
    if not is_authenticated():
        return jsonify({'success': False, 'message': 'Please login first'})
    
    data = request.get_json()
    items = data.get('items', [])
    
    username = session.get('username')
    
    total_points_used = 0
    
    for item in items:
        product_id = item['productId']
        quantity = item['quantity']
        success, message = record_purchase(username, product_id, quantity, use_points=True)
        if not success:
            return jsonify({'success': False, 'message': message})
        
        products = get_products()
        product = next((p for p in products if p[0] == product_id), None)
        if product:
            total_points_used += product[6] * quantity
    
    updated_info = get_customer_info(username)
    if updated_info and current_user:
        current_user['reward_points'] = updated_info[2]
    
    return jsonify({
        'success': True, 
        'message': f'✅ Redemption successful! Used {total_points_used:.1f} points', 
        'points_used': total_points_used
    })

@app.route('/api/my_purchase_history')
def api_my_purchase_history():
    """Get current user's purchase history only"""
    if not is_authenticated():
        return jsonify({'history': []})
    
    username = session.get('username')
    history_data = get_purchase_history(username)
    history = []
    
    for item in history_data:
        if len(item) == 8:
            product_name, quantity, unit_price, total_price, earned_points, purchase_time, customer_name, barcode = item
            history.append({
                'customer_name': customer_name,
                'product_name': product_name,
                'quantity': quantity,
                'unit_price': unit_price,
                'total_price': total_price,
                'earned_points': earned_points,
                'barcode': barcode,
                'purchase_time': purchase_time
            })
    
    return jsonify({'history': history})

@app.route('/api/my_topup_history')
def api_my_topup_history():
    """Get current user's top-up history only"""
    if not is_authenticated():
        return jsonify({'history': []})
    
    username = session.get('username')
    history_data = get_topup_history(username)
    history = []
    
    for item in history_data:
        if len(item) == 5:
            amount, previous_balance, new_balance, topup_time, customer_name = item
            history.append({
                'customer_name': customer_name,
                'amount': amount,
                'previous_balance': previous_balance,
                'new_balance': new_balance,
                'topup_time': topup_time
            })
    
    return jsonify({'history': history})

@app.route('/api/my_reward_history')
def api_my_reward_history():
    """Get current user's reward redemption history"""
    if not is_authenticated():
        return jsonify({'history': []})
    
    username = session.get('username')
    history_data = get_reward_redemptions(username)
    history = []
    
    for item in history_data:
        if len(item) == 4:
            product_name, points_used, redemption_time, customer_name = item
            history.append({
                'customer_name': customer_name,
                'product_name': product_name,
                'points_used': points_used,
                'redemption_time': redemption_time
            })
    
    return jsonify({'history': history})

# Barcode Scanner API routes
@app.route('/api/scan_barcode', methods=['POST'])
def api_scan_barcode():
    """Handle barcode scanning"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data received'})
        
        barcode = data.get('barcode', '').strip()
        
        if not barcode:
            return jsonify({'success': False, 'message': 'No barcode provided'})
        
        success = add_scanned_barcode(barcode)
        
        if success:
            return jsonify({'success': True, 'message': 'Barcode scanned successfully'})
        else:
            return jsonify({'success': False, 'message': 'Invalid barcode or product not found'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

@app.route('/api/scanned_products')
def api_scanned_products():
    """Get scanned products information"""
    products = get_scanned_products()
    return jsonify({'products': products})

@app.route('/api/clear_barcodes', methods=['POST'])
def api_clear_barcodes():
    """Clear all scanned barcodes"""
    clear_scanned_barcodes()
    return jsonify({'success': True, 'message': 'Scanned barcodes cleared'})

@app.route('/api/remove_scanned_product', methods=['POST'])
def api_remove_scanned_product():
    """Remove a specific barcode from scanned list"""
    try:
        data = request.get_json()
        barcode_to_remove = data.get('barcode', '')
        
        if barcode_to_remove:
            global scanned_barcodes
            scanned_barcodes = deque([b for b in scanned_barcodes if b != barcode_to_remove], maxlen=10)
        
        return jsonify({'success': True, 'message': 'Product removed'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})

# Admin API routes
@app.route('/api/admin/add_product', methods=['POST'])
def api_admin_add_product():
    """Add new product with barcode (Admin only)"""
    if not is_authenticated() or not is_admin():
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    data = request.get_json()
    name = data.get('name')
    price = data.get('price')
    category = data.get('category')
    barcode = data.get('barcode')
    stock = data.get('stock')
    reward_points_cost = data.get('reward_points_cost', 0)
    
    if not all([name, price, category, barcode, stock]):
        return jsonify({'success': False, 'message': 'All fields are required'})
    
    success, message = add_product(name, price, category, barcode, stock, reward_points_cost)
    return jsonify({'success': success, 'message': message})

@app.route('/api/admin/update_product', methods=['POST'])
def api_admin_update_product():
    """Update product with barcode (Admin only)"""
    if not is_authenticated() or not is_admin():
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    data = request.get_json()
    product_id = data.get('product_id')
    name = data.get('name')
    price = data.get('price')
    category = data.get('category')
    barcode = data.get('barcode')
    stock = data.get('stock')
    reward_points_cost = data.get('reward_points_cost', 0)
    
    if not all([product_id, name, price, category, stock]):
        return jsonify({'success': False, 'message': 'All fields are required'})
    
    success, message = update_product(product_id, name, price, category, barcode, stock, reward_points_cost)
    return jsonify({'success': success, 'message': message})

@app.route('/api/admin/delete_product', methods=['POST'])
def api_admin_delete_product():
    """Delete product (Admin only)"""
    if not is_authenticated() or not is_admin():
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    data = request.get_json()
    product_id = data.get('product_id')
    
    if not product_id:
        return jsonify({'success': False, 'message': 'Product ID is required'})
    
    success, message = delete_product(product_id)
    return jsonify({'success': success, 'message': message})

if __name__ == '__main__':
    print("=" * 60)
    print("🛒 Smart Shopping System with Barcode Scanner")
    print("=" * 60)
    print("\n🌐 Access: http://localhost:5000")
    print("\n📱 Features:")
    print("   🏠 Login Page - Username/password authentication")
    print("   🛒 Shopping - Auto-populates with scanned products")
    print("   🎁 Rewards Shop - Exchange points for products")
    print("   📊 My Purchase History - With barcode information")
    print("   💰 My Top-up History - User's top-up records")
    print("   🎁 My Reward History - Reward redemption records")
    print("   👑 Admin Panel - Product management with barcode support")
    print("\n🔐 Security Features:")
    print("   ✅ Username/password authentication")
    print("   ✅ Admin role detection and access control")
    print("   ✅ Brute force protection (5 attempts limit)")
    print("\n📦 Barcode Scanner Features:")
    print("   ✅ Scan products on home page")
    print("   ✅ Auto-add to shopping cart")
    print("   ✅ EAN-13 barcode support")
    print("   ✅ Real-time product lookup")
    print("\n⭐ Reward System:")
    print("   ✅ Earn 0.001 points for every ¥1 spent")
    print("   ✅ Exchange points for special reward products")
    print("   ✅ Separate rewards shop interface")
    print("\n👤 Demo Accounts:")
    print("   👑 admin / admin123 (Admin)")
    print("   👑 william / 12345678 (Admin)")
    print("   👤 user / demo123 (Regular User)")
    print("\n📦 Test Barcodes (EAN-13):")
    print("   4901777013931 - Coca-Cola")
    print("   4902102118878 - Potato Chips")
    print("   4901777242950 - Chocolate")
    print("   4901777242967 - Mineral Water")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
