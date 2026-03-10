import serial
import time
import threading
import os
import subprocess
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request, session
from flask_cors import CORS
import sqlite3
import hashlib
from collections import deque

app = Flask(__name__)
app.secret_key = 'smart-shopping-system-secret-key-2024'
CORS(app)

# Force using COM6
SERIAL_PORT = 'COM6'
BAUD_RATE = 9600

# Global variables
ser = None
current_card = None
serial_connected = False
database_initialized = False

# Barcode scanner variables
scanned_barcodes = deque(maxlen=10)  # Store recent barcodes
last_barcode_time = 0
BARCODE_TIMEOUT = 2  # seconds between barcode scans

# Password attempt limits
login_attempts = {}

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
                 (card_uid TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  balance REAL DEFAULT 0.0,
                  reward_points REAL DEFAULT 0.0,
                  password_hash TEXT,
                  is_admin BOOLEAN DEFAULT FALSE,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Create products table WITH BARCODE SUPPORT
    c.execute('''CREATE TABLE IF NOT EXISTS products
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  price REAL NOT NULL,
                  category TEXT,
                  barcode TEXT UNIQUE,  -- Added barcode field
                  stock INTEGER DEFAULT 100,
                  reward_points_cost REAL DEFAULT 0.0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Create purchase history table
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  card_uid TEXT NOT NULL,
                  product_id INTEGER NOT NULL,
                  product_name TEXT NOT NULL,
                  barcode TEXT,  -- Added barcode field
                  quantity INTEGER NOT NULL,
                  unit_price REAL NOT NULL,
                  total_price REAL NOT NULL,
                  earned_points REAL DEFAULT 0.0,
                  purchase_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (card_uid) REFERENCES customers (card_uid),
                  FOREIGN KEY (product_id) REFERENCES products (id))''')
    
    # Create topup history table
    c.execute('''CREATE TABLE IF NOT EXISTS topup_history
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  card_uid TEXT NOT NULL,
                  amount REAL NOT NULL,
                  previous_balance REAL NOT NULL,
                  new_balance REAL NOT NULL,
                  topup_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (card_uid) REFERENCES customers (card_uid))''')
    
    # Create reward redemption history table
    c.execute('''CREATE TABLE IF NOT EXISTS reward_redemptions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  card_uid TEXT NOT NULL,
                  product_id INTEGER NOT NULL,
                  product_name TEXT NOT NULL,
                  points_used REAL NOT NULL,
                  redemption_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (card_uid) REFERENCES customers (card_uid),
                  FOREIGN KEY (product_id) REFERENCES products (id))''')
    
    # Check if reward_points column exists, if not add it
    try:
        c.execute("SELECT reward_points FROM customers LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE customers ADD COLUMN reward_points REAL DEFAULT 0.0")
    
    # Check if reward_points_cost column exists in products, if not add it
    try:
        c.execute("SELECT reward_points_cost FROM products LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE products ADD COLUMN reward_points_cost REAL DEFAULT 0.0")
    
    # Check if earned_points column exists in purchase_history, if not add it
    try:
        c.execute("SELECT earned_points FROM purchase_history LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE purchase_history ADD COLUMN earned_points REAL DEFAULT 0.0")
    
    # Check if password_hash column exists, if not add it
    try:
        c.execute("SELECT password_hash FROM customers LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE customers ADD COLUMN password_hash TEXT")
    
    # Check if is_admin column exists, if not add it
    try:
        c.execute("SELECT is_admin FROM customers LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE customers ADD COLUMN is_admin BOOLEAN DEFAULT FALSE")
    
    # Check if barcode column exists in products, if not add it
    try:
        c.execute("SELECT barcode FROM products LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE products ADD COLUMN barcode TEXT UNIQUE")
        print("✅ Added barcode column to products table")
    
    # Check if barcode column exists in purchase_history, if not add it
    try:
        c.execute("SELECT barcode FROM purchase_history LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE purchase_history ADD COLUMN barcode TEXT")
        print("✅ Added barcode column to purchase_history table")
    
    # Check if sample data already exists
    c.execute("SELECT COUNT(*) FROM customers")
    customer_count = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM products")
    product_count = c.fetchone()[0]
    
    # Only insert sample data if tables are empty
    if customer_count == 0:
        # Use secure password hashing
        admin_password_hash = hash_password('admin123')
        william_password_hash = hash_password('12345678')
        
        # Insert sample customer data with password hashes - BOTH AS ADMINS
        c.execute("INSERT INTO customers (card_uid, name, balance, reward_points, password_hash, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
                  ('9073ACED', 'Admin', 1000.00, 50.0, admin_password_hash, True))
        c.execute("INSERT INTO customers (card_uid, name, balance, reward_points, password_hash, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
                  ('300757DB', 'William', 500.00, 25.0, william_password_hash, True))
    
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

def check_login_attempts(card_uid):
    """Check login attempts to prevent brute force attacks"""
    if card_uid not in login_attempts:
        login_attempts[card_uid] = {'count': 0, 'last_attempt': time.time()}
    
    attempts = login_attempts[card_uid]
    
    # Reset counter if more than 5 minutes have passed
    if time.time() - attempts['last_attempt'] > 300:  # 5 minutes
        attempts['count'] = 0
    
    # Check if exceeded limit
    if attempts['count'] >= 5:
        return False, "Too many failed attempts. Please try again later."
    
    return True, "OK"

def record_login_attempt(card_uid, success):
    """Record login attempt"""
    if card_uid not in login_attempts:
        login_attempts[card_uid] = {'count': 0, 'last_attempt': time.time()}
    
    attempts = login_attempts[card_uid]
    attempts['last_attempt'] = time.time()
    
    if success:
        attempts['count'] = 0  # Reset counter on successful login
    else:
        attempts['count'] += 1

def add_value(card_uid, amount):
    """Add value/balance to customer account"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    try:
        # Check if customer exists
        c.execute("SELECT name, balance FROM customers WHERE card_uid = ?", (card_uid,))
        customer = c.fetchone()
        
        if not customer:
            conn.close()
            return False, "Customer not found"
        
        name, current_balance = customer
        new_balance = current_balance + amount
        
        # Update customer balance
        c.execute("UPDATE customers SET balance = ? WHERE card_uid = ?", (new_balance, card_uid))
        
        # Record topup history
        c.execute('''INSERT INTO topup_history 
                    (card_uid, amount, previous_balance, new_balance)
                    VALUES (?, ?, ?, ?)''',
                 (card_uid, amount, current_balance, new_balance))
        
        conn.commit()
        conn.close()
        
        # Update current card balance if it's the same card
        global current_card
        if current_card and current_card.get('card_uid') == card_uid:
            current_card['balance'] = new_balance
            current_card['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Send updated balance to Arduino
        send_balance_to_arduino(card_uid)
        
        return True, f"✅ Successfully added ¥{amount:.2f} to {name}. New balance: ¥{new_balance:.2f}"
        
    except Exception as e:
        conn.close()
        return False, f"❌ Error adding value: {str(e)}"

def get_topup_history(card_uid=None):
    """Get topup history"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    if card_uid:
        c.execute('''SELECT th.amount, th.previous_balance, th.new_balance, 
                            th.topup_time, c.name
                     FROM topup_history th
                     JOIN customers c ON th.card_uid = c.card_uid
                     WHERE th.card_uid = ?
                     ORDER BY th.topup_time DESC''', (card_uid,))
    else:
        c.execute('''SELECT th.amount, th.previous_balance, th.new_balance, 
                            th.topup_time, c.name, c.card_uid
                     FROM topup_history th
                     JOIN customers c ON th.card_uid = c.card_uid
                     ORDER BY th.topup_time DESC LIMIT 50''')
    
    history = c.fetchall()
    conn.close()
    return history

def connect_serial():
    """Connect to COM6 - Enhanced version"""
    global ser, serial_connected
    
    max_retries = 5
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            # Connect to serial port
            ser = serial.Serial(
                port=SERIAL_PORT,
                baudrate=BAUD_RATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1,
                write_timeout=1,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )
            
            serial_connected = True
            
            # Clear buffers
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            
            return True
            
        except serial.SerialException as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
    
    return False

def parse_card_data(line):
    """Parse card data - Modified to require password authentication"""
    try:
        line = line.strip()
        
        if line.startswith("CARD:"):
            if '|NAME:' in line and '|BALANCE:' in line:
                parts = line.split('|')
                card_uid = parts[0].replace("CARD:", "").strip().upper()
                
                name = balance = reward_points = None
                for part in parts[1:]:
                    if part.startswith("NAME:"):
                        name = part.replace("NAME:", "").strip()
                    elif part.startswith("BALANCE:"):
                        try:
                            balance = float(part.replace("BALANCE:", "").strip())
                        except ValueError:
                            balance = 0.0
                    elif part.startswith("POINTS:"):
                        try:
                            reward_points = float(part.replace("POINTS:", "").strip())
                        except ValueError:
                            reward_points = 0.0
                
                if name and balance is not None:
                    # Set state requiring password authentication
                    return {
                        'card_uid': card_uid,
                        'name': name,
                        'balance': balance,
                        'reward_points': reward_points or 0.0,
                        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'needs_password': True,
                        'authenticated': False
                    }
        
        elif line.startswith("UNKNOWN_CARD:"):
            card_uid = line.replace("UNKNOWN_CARD:", "").strip().upper()
            return {
                'card_uid': card_uid,
                'name': 'Unauthorized User',
                'balance': 0.0,
                'reward_points': 0.0,
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'type': 'unknown'
            }
    
    except Exception as e:
        pass
    
    return None

def serial_listener():
    """Serial listener thread"""
    global current_card, serial_connected
    
    buffer = ""
    
    while True:
        try:
            if ser and ser.is_open:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                    buffer += data
                    
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        
                        if line:
                            card_data = parse_card_data(line)
                            if card_data:
                                # Check if authentication status already exists
                                if current_card and current_card.get('card_uid') == card_data.get('card_uid'):
                                    # Maintain existing authentication status
                                    card_data['authenticated'] = current_card.get('authenticated', False)
                                    card_data['needs_password'] = current_card.get('needs_password', True)
                                
                                current_card = card_data
            
            time.sleep(0.1)
            
        except Exception as e:
            serial_connected = False
            time.sleep(2)
            if connect_serial():
                buffer = ""

# Database operations
def get_customer_info(card_uid):
    """Get customer information including password hash, reward points, and admin status"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    try:
        c.execute("SELECT name, balance, reward_points, password_hash, is_admin FROM customers WHERE card_uid = ?", (card_uid,))
        result = c.fetchone()
        return result
    except sqlite3.OperationalError as e:
        # If is_admin column doesn't exist yet, return default values
        try:
            c.execute("SELECT name, balance, reward_points, password_hash FROM customers WHERE card_uid = ?", (card_uid,))
            result = c.fetchone()
            if result:
                # Add default admin status (False) for existing records
                return result + (False,)  # Add is_admin as False
            return None
        except:
            return None
    finally:
        conn.close()

def authenticate_user(card_uid, password):
    """Authenticate user with password"""
    customer_info = get_customer_info(card_uid)
    if not customer_info:
        return False, "Card not found"
    
    # Handle both old format (4 items) and new format (5 items with is_admin)
    if len(customer_info) == 4:
        name, balance, reward_points, password_hash = customer_info
        is_admin = False  # Default to non-admin for old format
    else:
        name, balance, reward_points, password_hash, is_admin = customer_info
    
    # Check login attempts
    can_attempt, message = check_login_attempts(card_uid)
    if not can_attempt:
        return False, message
    
    # Verify password
    if verify_password(password, password_hash):
        record_login_attempt(card_uid, True)
        session['authenticated'] = True
        session['card_uid'] = card_uid
        session['user_name'] = name
        session['is_admin'] = bool(is_admin)  # Ensure boolean value
        return True, "Authentication successful"
    else:
        record_login_attempt(card_uid, False)
        remaining_attempts = 5 - login_attempts[card_uid]['count']
        if remaining_attempts <= 0:
            return False, "Account locked. Too many failed attempts."
        else:
            return False, f"Invalid password. {remaining_attempts} attempts remaining."

def migrate_database():
    """Ensure database has latest schema and William is admin"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    try:
        # Check if is_admin column exists
        c.execute("SELECT is_admin FROM customers LIMIT 1")
    except sqlite3.OperationalError:
        # Add is_admin column if it doesn't exist
        c.execute("ALTER TABLE customers ADD COLUMN is_admin BOOLEAN DEFAULT FALSE")
        print("✅ Added is_admin column to customers table")
    
    # Ensure William is set as admin
    c.execute("UPDATE customers SET is_admin = TRUE WHERE card_uid = '300757DB' AND name = 'William'")
    
    # Check if update was successful
    c.execute("SELECT is_admin FROM customers WHERE card_uid = '300757DB'")
    result = c.fetchone()
    if result and result[0]:
        print("✅ William is now set as admin")
    else:
        print("⚠️  Could not set William as admin - card might not exist")
    
    conn.commit()
    conn.close()

# Add this after database initialization
if not database_initialized:
    init_database()

# Run migration after initialization
migrate_database()

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
            'card_uid': session.get('card_uid'),
            'name': session.get('user_name'),
            'is_admin': session.get('is_admin', False)
        }
    return None

def logout_user():
    """User logout"""
    session.clear()

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

def record_purchase(card_uid, product_id, quantity, use_points=False):
    """Record purchase with reward points - FIXED VERSION"""
    print(f"🔔 record_purchase called: card={card_uid}, product={product_id}, qty={quantity}, use_points={use_points}")
    
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
            c.execute("SELECT reward_points FROM customers WHERE card_uid = ?", (card_uid,))
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
                        (card_uid, product_id, product_name, points_used)
                        VALUES (?, ?, ?, ?)''',
                     (card_uid, product_id, product_name, total_points_cost))
            
            # Update customer points
            c.execute("UPDATE customers SET reward_points = reward_points - ? WHERE card_uid = ?",
                     (total_points_cost, card_uid))
            
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
            c.execute("SELECT balance FROM customers WHERE card_uid = ?", (card_uid,))
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
                        (card_uid, product_id, product_name, barcode, quantity, unit_price, total_price, earned_points)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (card_uid, product_id, product_name, barcode, quantity, unit_price, total_price, earned_points))
            
            print(f"📝 Purchase history recorded: {product_name} x{quantity}, ¥{total_price}")
            
            # Update customer balance
            c.execute("UPDATE customers SET balance = balance - ? WHERE card_uid = ?",
                     (total_price, card_uid))
            
            print(f"💰 Updated customer balance: -¥{total_price}")
            
            # Update customer reward points
            c.execute("UPDATE customers SET reward_points = reward_points + ? WHERE card_uid = ?",
                     (earned_points, card_uid))
            
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

def get_purchase_history(card_uid=None):
    """Get purchase history - if card_uid provided, only get that user's history"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    if card_uid:
        c.execute('''SELECT ph.product_name, ph.quantity, ph.unit_price, ph.total_price, 
                            ph.earned_points, ph.purchase_time, c.name, ph.barcode
                     FROM purchase_history ph
                     JOIN customers c ON ph.card_uid = c.card_uid
                     WHERE ph.card_uid = ?
                     ORDER BY ph.purchase_time DESC''', (card_uid,))
    else:
        c.execute('''SELECT ph.product_name, ph.quantity, ph.unit_price, ph.total_price, 
                            ph.earned_points, ph.purchase_time, c.name, c.card_uid, ph.barcode
                     FROM purchase_history ph
                     JOIN customers c ON ph.card_uid = c.card_uid
                     ORDER BY ph.purchase_time DESC LIMIT 50''')
    
    history = c.fetchall()
    conn.close()
    return history

def get_reward_redemptions(card_uid=None):
    """Get reward redemption history"""
    conn = sqlite3.connect('shopping_system.db', check_same_thread=False)
    c = conn.cursor()
    
    if card_uid:
        c.execute('''SELECT rr.product_name, rr.points_used, rr.redemption_time, c.name
                     FROM reward_redemptions rr
                     JOIN customers c ON rr.card_uid = c.card_uid
                     WHERE rr.card_uid = ?
                     ORDER BY rr.redemption_time DESC''', (card_uid,))
    else:
        c.execute('''SELECT rr.product_name, rr.points_used, rr.redemption_time, c.name, c.card_uid
                     FROM reward_redemptions rr
                     JOIN customers c ON rr.card_uid = c.card_uid
                     ORDER BY rr.redemption_time DESC LIMIT 50''')
    
    history = c.fetchall()
    conn.close()
    return history

def send_balance_to_arduino(card_uid):
    """Send balance and points information to Arduino"""
    global ser
    
    if not ser or not ser.is_open:
        return False
    
    customer_info = get_customer_info(card_uid)
    if customer_info:
        name, balance, reward_points, _, _ = customer_info
        command = f"UPDATE_BALANCE:{card_uid}:{name}:{balance:.2f}:{reward_points:.3f}\n"
        try:
            ser.write(command.encode('utf-8'))
            ser.flush()
            return True
        except Exception as e:
            return False
    else:
        return False

def refresh_current_card_balance():
    """Refresh the current card balance and points from database"""
    global current_card
    if current_card and current_card.get('card_uid'):
        customer_info = get_customer_info(current_card['card_uid'])
        if customer_info:
            name, balance, reward_points, _, _ = customer_info
            current_card['balance'] = balance
            current_card['reward_points'] = reward_points
            current_card['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

# Initialize database - Only run once
if not database_initialized:
    init_database()

# ============================================================================
# FLASK ROUTES - ALL PAGES IN ONE FILE
# ============================================================================

@app.route('/')
def index():
    """Main dashboard page"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Smart Shopping System</title>
        <meta charset="UTF-8">
        <style>
            body { font-family: Arial; margin: 0; padding: 0; background: #f5f5f5; }
            .header { background: #2c3e50; color: white; padding: 1rem; }
            .nav { display: flex; gap: 1rem; }
            .nav a { color: white; text-decoration: none; padding: 0.5rem 1rem; border-radius: 4px; }
            .nav a:hover { background: #34495e; }
            .nav a.active { background: #3498db; }
            .container { max-width: 1200px; margin: 0 auto; padding: 1rem; }
            .status { padding: 1rem; margin: 1rem 0; border-radius: 5px; }
            .connected { background: #d4edda; color: #155724; }
            .disconnected { background: #f8d7da; color: #721c24; }
            .card-actions { display: flex; gap: 0.5rem; margin-top: 1rem; justify-content: center; flex-wrap: wrap; }
            .card-actions button { padding: 0.5rem 1rem; border: none; border-radius: 4px; cursor: pointer; color: white; }
            .check-balance { background: #9b59b6; }
            .refresh-balance { background: #f39c12; }
            .logout-btn { background: #e74c3c; }
            .add-value-btn { background: #27ae60; }
            .admin-btn { background: #e67e22; }
            .rewards-btn { background: #8e44ad; }
            
            .password-modal, .add-value-modal, .confirm-password-modal {
                display: none;
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0,0,0,0.5);
                z-index: 1000;
                justify-content: center;
                align-items: center;
            }
            .password-content, .add-value-content, .confirm-password-content {
                background: white;
                padding: 2rem;
                border-radius: 8px;
                width: 350px;
                text-align: center;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }
            .password-input, .amount-input, .confirm-password-input {
                width: 100%;
                padding: 0.75rem;
                margin: 1rem 0;
                border: 2px solid #ddd;
                border-radius: 4px;
                font-size: 1rem;
                box-sizing: border-box;
            }
            .password-input:focus, .amount-input:focus, .confirm-password-input:focus {
                border-color: #3498db;
                outline: none;
            }
            .login-btn, .confirm-btn, .confirm-password-btn {
                background: #27ae60;
                color: white;
                border: none;
                padding: 0.75rem 1.5rem;
                border-radius: 4px;
                cursor: pointer;
                margin-right: 0.5rem;
                font-size: 1rem;
            }
            .login-btn:hover, .confirm-btn:hover, .confirm-password-btn:hover {
                background: #219a52;
            }
            .login-btn:disabled, .confirm-btn:disabled, .confirm-password-btn:disabled {
                background: #95a5a6;
                cursor: not-allowed;
            }
            .cancel-btn {
                background: #95a5a6;
                color: white;
                border: none;
                padding: 0.75rem 1.5rem;
                border-radius: 4px;
                cursor: pointer;
                font-size: 1rem;
            }
            .cancel-btn:hover {
                background: #7f8c8d;
            }
            .password-feedback, .add-value-feedback, .confirm-password-feedback {
                margin-top: 1rem;
                padding: 0.75rem;
                border-radius: 4px;
                font-weight: bold;
            }
            .success-feedback {
                background: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }
            .error-feedback {
                background: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }
            .amount-buttons {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 0.5rem;
                margin: 1rem 0;
            }
            .amount-btn {
                padding: 0.75rem;
                border: 2px solid #3498db;
                background: white;
                color: #3498db;
                border-radius: 4px;
                cursor: pointer;
                font-size: 1rem;
            }
            .amount-btn:hover {
                background: #3498db;
                color: white;
            }
            .amount-btn.active {
                background: #3498db;
                color: white;
            }
            .points-display {
                background: linear-gradient(135deg, #8e44ad, #9b59b6);
                color: white;
                padding: 0.5rem 1rem;
                border-radius: 20px;
                font-weight: bold;
                margin: 0.5rem 0;
                display: inline-block;
            }
            .barcode-info {
                background: #3498db;
                color: white;
                padding: 0.5rem 1rem;
                border-radius: 4px;
                margin: 0.5rem 0;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 0.5rem;
            }
            .clear-barcode-btn {
                background: #e74c3c;
                color: white;
                border: none;
                padding: 0.25rem 0.5rem;
                border-radius: 3px;
                cursor: pointer;
                margin-left: 0.5rem;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <div class="container">
                <h1>🛒 Smart Shopping System</h1>
                <div class="nav">
                    <a href="/" class="active">Home</a>
                    <a href="/shopping">Shopping</a>
                    <a href="/rewards">Rewards Shop</a>
                    <a href="/history">My Purchase History</a>
                    <a href="/topup">My Top-up History</a>
                    <a href="/reward_history">My Reward History</a>
                    <a href="/admin">Admin Panel</a>
                </div>
            </div>
        </div>
        
        <div class="container">
            <div class="status" id="status">Checking COM6 connection...</div>
            
            <div>
                <h2>🎯 Card Information</h2>
                <div id="cardDisplay" style="background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <h3>Waiting for card swipe...</h3>
                    <p>Port: COM6 | Baud Rate: 9600</p>
                </div>
            </div>
            
            <div id="barcodeDisplay" style="display: none; margin-top: 2rem;">
                <h2>📦 Scanned Products</h2>
                <div id="scannedProducts" style="background: white; padding: 1.5rem; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <p>No products scanned yet. Use barcode scanner to add products.</p>
                </div>
            </div>
        </div>

        <!-- Password Input Modal -->
        <div class="password-modal" id="passwordModal">
            <div class="password-content">
                <h3>Enter Password</h3>
                <p id="passwordPrompt">Please enter your password</p>
                <input type="password" id="passwordInput" class="password-input" placeholder="Enter password" autocomplete="current-password">
                <div id="passwordFeedback" class="password-feedback" style="display: none;"></div>
                <div style="margin-top: 1.5rem;">
                    <button class="login-btn" id="loginButton" onclick="submitPassword()">Login</button>
                    <button class="cancel-btn" onclick="hidePasswordModal()">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Add Value Modal -->
        <div class="add-value-modal" id="addValueModal">
            <div class="add-value-content">
                <h3>Add Value to Account</h3>
                <p id="addValuePrompt">Select amount to add</p>
                
                <div class="amount-buttons">
                    <button class="amount-btn" onclick="selectAmount(10)">¥10</button>
                    <button class="amount-btn" onclick="selectAmount(50)">¥50</button>
                    <button class="amount-btn" onclick="selectAmount(100)">¥100</button>
                    <button class="amount-btn" onclick="selectAmount(200)">¥200</button>
                    <button class="amount-btn" onclick="selectAmount(500)">¥500</button>
                    <button class="amount-btn" onclick="selectAmount(1000)">¥1000</button>
                </div>
                
                <input type="number" id="customAmount" class="amount-input" placeholder="Or enter custom amount" min="1" step="1">
                
                <div id="addValueFeedback" class="add-value-feedback" style="display: none;"></div>
                <div style="margin-top: 1.5rem;">
                    <button class="confirm-btn" id="confirmButton" onclick="confirmAddValue()">Add Value</button>
                    <button class="cancel-btn" onclick="hideAddValueModal()">Cancel</button>
                </div>
            </div>
        </div>

        <!-- Confirm Password Modal -->
        <div class="confirm-password-modal" id="confirmPasswordModal">
            <div class="confirm-password-content">
                <h3>Confirm Add Value</h3>
                <p id="confirmPasswordPrompt">Please enter your password to confirm adding <span id="confirmAmount" style="font-weight: bold; color: #e74c3c;"></span></p>
                <input type="password" id="confirmPasswordInput" class="confirm-password-input" placeholder="Enter password" autocomplete="current-password">
                <div id="confirmPasswordFeedback" class="confirm-password-feedback" style="display: none;"></div>
                <div style="margin-top: 1.5rem;">
                    <button class="confirm-password-btn" id="confirmPasswordButton" onclick="submitConfirmPassword()">Confirm Add Value</button>
                    <button class="cancel-btn" onclick="hideConfirmPasswordModal()">Cancel</button>
                </div>
            </div>
        </div>

        <script>
            let currentCardData = null;
            let selectedAmount = 0;
            let scannedProducts = [];
            
            function updateDisplay() {
                fetch('/api/status')
                    .then(r => {
                        if (!r.ok) throw new Error('Network response was not ok');
                        return r.json();
                    })
                    .then(data => {
                        updateStatus(data);
                        updateCard(data);
                        updateScannedProducts();
                    })
                    .catch(error => {
                        console.error('Status update error:', error);
                    });
            }
            
            function updateStatus(data) {
                const status = document.getElementById('status');
                if (data.serial_connected) {
                    status.textContent = '✅ COM6 Connected - System Ready';
                    status.className = 'status connected';
                } else {
                    status.textContent = '❌ COM6 Connection Failed';
                    status.className = 'status disconnected';
                }
            }
            
            function updateCard(data) {
                const display = document.getElementById('cardDisplay');
                if (data.current_card) {
                    const card = data.current_card;
                    currentCardData = card;
                    
                    if (card.needs_password && !card.authenticated) {
                        // Show password required interface
                        display.innerHTML = `
                            <div style="text-align: center;">
                                <h2 style="color: #f39c12; margin-bottom: 1rem;">🔒 Authentication Required</h2>
                                <h3>${card.name}</h3>
                                <p><strong>Card ID:</strong> ${card.card_uid}</p>
                                <p style="font-size: 1.2rem; margin: 1rem 0;">Please enter password to continue</p>
                                <button onclick="showPasswordModal()" 
                                        style="padding: 0.75rem 1.5rem; background: #3498db; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 1.1rem;">
                                    Enter Password
                                </button>
                            </div>
                        `;
                    } else if (card.authenticated) {
                        // Check if user is admin by making an API call
                        fetch('/api/auth_status')
                            .then(response => response.json())
                            .then(authData => {
                                const isAdmin = authData.current_user && authData.current_user.is_admin;
                                
                                const adminButton = isAdmin ? 
                                    `<button class="admin-btn" onclick="goToAdmin()">Admin Panel</button>` : '';
                                
                                display.innerHTML = `
                                    <div style="text-align: center;">
                                        <h2 style="color: #27ae60; margin-bottom: 1rem;">✅ ${card.name}</h2>
                                        <p><strong>Card ID:</strong> ${card.card_uid}</p>
                                        <p style="font-size: 2rem; font-weight: bold; color: #e74c3c; margin: 1rem 0;">¥${card.balance.toFixed(2)}</p>
                                        <div class="points-display">
                                            ⭐ ${card.reward_points ? card.reward_points.toFixed(3) : '0.000'} Reward Points
                                        </div>
                                        <small style="color: #666;">${card.timestamp}</small>
                                        <div class="card-actions">
                                            <button class="check-balance" onclick="checkBalance()">Check Balance</button>
                                            <button class="refresh-balance" onclick="refreshBalance()">Refresh</button>
                                            <button class="add-value-btn" onclick="showAddValueModal()">Add Value</button>
                                            <button class="rewards-btn" onclick="goToRewards()">Rewards Shop</button>
                                            ${adminButton}
                                            <button class="logout-btn" onclick="logout()">Logout</button>
                                        </div>
                                    </div>
                                `;
                            })
                            .catch(error => {
                                console.error('Error checking admin status:', error);
                                // Fallback: show without admin button
                                display.innerHTML = `
                                    <div style="text-align: center;">
                                        <h2 style="color: #27ae60; margin-bottom: 1rem;">✅ ${card.name}</h2>
                                        <p><strong>Card ID:</strong> ${card.card_uid}</p>
                                        <p style="font-size: 2rem; font-weight: bold; color: #e74c3c; margin: 1rem 0;">¥${card.balance.toFixed(2)}</p>
                                        <div class="points-display">
                                            ⭐ ${card.reward_points ? card.reward_points.toFixed(3) : '0.000'} Reward Points
                                        </div>
                                        <small style="color: #666;">${card.timestamp}</small>
                                        <div class="card-actions">
                                            <button class="check-balance" onclick="checkBalance()">Check Balance</button>
                                            <button class="refresh-balance" onclick="refreshBalance()">Refresh</button>
                                            <button class="add-value-btn" onclick="showAddValueModal()">Add Value</button>
                                            <button class="rewards-btn" onclick="goToRewards()">Rewards Shop</button>
                                            <button class="logout-btn" onclick="logout()">Logout</button>
                                        </div>
                                    </div>
                                `;
                            });
                    }
                } else {
                    display.innerHTML = `
                        <div style="text-align: center;">
                            <h3>Waiting for card swipe...</h3>
                            <p>Please place RFID card near the reader</p>
                            <p style="color: #666;">Port: COM6 | Baud Rate: 9600</p>
                        </div>
                    `;
                }
            }
            
            function updateScannedProducts() {
                fetch('/api/scanned_products')
                    .then(response => response.json())
                    .then(data => {
                        const barcodeDisplay = document.getElementById('barcodeDisplay');
                        const scannedProductsDiv = document.getElementById('scannedProducts');
                        
                        if (data.products && data.products.length > 0) {
                            scannedProducts = data.products;
                            barcodeDisplay.style.display = 'block';
                            
                            let html = '<div style="text-align: center;">';
                            html += '<div class="barcode-info">';
                            html += '<span>📦 ' + data.products.length + ' product(s) scanned</span>';
                            html += '<button class="clear-barcode-btn" onclick="clearScannedBarcodes()">Clear All</button>';
                            html += '</div>';
                            
                            html += '<div style="margin-top: 1rem;">';
                            data.products.forEach((product, index) => {
                                html += `
                                    <div style="background: #f8f9fa; padding: 0.75rem; margin: 0.5rem 0; border-radius: 4px; border-left: 4px solid #3498db;">
                                        <div style="display: flex; justify-content: space-between; align-items: center;">
                                            <div style="text-align: left;">
                                                <strong>${product.name}</strong>
                                                <div style="font-size: 0.9rem; color: #666;">
                                                    Price: ¥${product.price.toFixed(2)} | Quantity: ${product.quantity}
                                                </div>
                                                <div style="font-size: 0.8rem; color: #999;">
                                                    Barcode: ${product.barcode}
                                                </div>
                                            </div>
                                            <div style="text-align: right;">
                                                <strong>¥${(product.price * product.quantity).toFixed(2)}</strong>
                                                <div>
                                                    <button onclick="removeScannedProduct('${product.barcode}')" style="background: #e74c3c; color: white; border: none; padding: 0.25rem 0.5rem; border-radius: 3px; cursor: pointer; font-size: 0.8rem; margin-top: 0.25rem;">
                                                        Remove
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                `;
                            });
                            
                            // Calculate total
                            const total = data.products.reduce((sum, product) => sum + (product.price * product.quantity), 0);
                            const pointsEarned = total * 0.001;
                            
                            html += `
                                <div style="margin-top: 1rem; padding-top: 1rem; border-top: 2px solid #ddd;">
                                    <div style="display: flex; justify-content: space-between; font-weight: bold;">
                                        <span>Total:</span>
                                        <span>¥${total.toFixed(2)}</span>
                                    </div>
                                    <div style="color: #8e44ad; font-size: 0.9rem; margin-top: 0.25rem;">
                                        Will earn: ${pointsEarned.toFixed(3)} points
                                    </div>
                                </div>
                            `;
                            
                            html += '<button onclick="goToShoppingWithScanned()" style="background: #27ae60; color: white; border: none; padding: 0.75rem 1.5rem; border-radius: 4px; cursor: pointer; margin-top: 1rem; font-size: 1rem;">Proceed to Checkout</button>';
                            html += '</div></div>';
                            
                            scannedProductsDiv.innerHTML = html;
                        } else {
                            barcodeDisplay.style.display = 'none';
                            scannedProducts = [];
                        }
                    });
            }
            
            function showPasswordModal() {
                const modal = document.getElementById('passwordModal');
                const prompt = document.getElementById('passwordPrompt');
                if (currentCardData) {
                    prompt.textContent = `Please enter password for ${currentCardData.name}`;
                }
                document.getElementById('passwordInput').value = '';
                document.getElementById('passwordFeedback').style.display = 'none';
                modal.style.display = 'flex';
                document.getElementById('passwordInput').focus();
            }
            
            function hidePasswordModal() {
                document.getElementById('passwordModal').style.display = 'none';
                document.getElementById('passwordFeedback').style.display = 'none';
            }
            
            function showAddValueModal() {
                const modal = document.getElementById('addValueModal');
                const prompt = document.getElementById('addValuePrompt');
                if (currentCardData) {
                    prompt.textContent = `Add value to ${currentCardData.name}'s account (Current: ¥${currentCardData.balance.toFixed(2)})`;
                }
                selectedAmount = 0;
                document.getElementById('customAmount').value = '';
                document.getElementById('addValueFeedback').style.display = 'none';
                
                // Reset amount buttons
                document.querySelectorAll('.amount-btn').forEach(btn => {
                    btn.classList.remove('active');
                });
                
                modal.style.display = 'flex';
            }
            
            function hideAddValueModal() {
                document.getElementById('addValueModal').style.display = 'none';
                document.getElementById('addValueFeedback').style.display = 'none';
            }
            
            function showConfirmPasswordModal(amount) {
                const modal = document.getElementById('confirmPasswordModal');
                const amountDisplay = document.getElementById('confirmAmount');
                amountDisplay.textContent = '¥' + amount.toFixed(2);
                document.getElementById('confirmPasswordInput').value = '';
                document.getElementById('confirmPasswordFeedback').style.display = 'none';
                modal.style.display = 'flex';
                document.getElementById('confirmPasswordInput').focus();
            }
            
            function hideConfirmPasswordModal() {
                document.getElementById('confirmPasswordModal').style.display = 'none';
                document.getElementById('confirmPasswordFeedback').style.display = 'none';
            }
            
            function selectAmount(amount) {
                selectedAmount = amount;
                document.getElementById('customAmount').value = '';
                
                // Update button states
                document.querySelectorAll('.amount-btn').forEach(btn => {
                    btn.classList.remove('active');
                });
                event.target.classList.add('active');
            }
            
            function showPasswordFeedback(message, isSuccess) {
                const feedback = document.getElementById('passwordFeedback');
                feedback.textContent = message;
                feedback.className = isSuccess ? 'password-feedback success-feedback' : 'password-feedback error-feedback';
                feedback.style.display = 'block';
            }
            
            function showAddValueFeedback(message, isSuccess) {
                const feedback = document.getElementById('addValueFeedback');
                feedback.textContent = message;
                feedback.className = isSuccess ? 'add-value-feedback success-feedback' : 'add-value-feedback error-feedback';
                feedback.style.display = 'block';
            }
            
            function showConfirmPasswordFeedback(message, isSuccess) {
                const feedback = document.getElementById('confirmPasswordFeedback');
                feedback.textContent = message;
                feedback.className = isSuccess ? 'confirm-password-feedback success-feedback' : 'confirm-password-feedback error-feedback';
                feedback.style.display = 'block';
            }
            
            function submitPassword() {
                const password = document.getElementById('passwordInput').value;
                
                if (!password) {
                    showPasswordFeedback('Please enter password', false);
                    return;
                }
                
                // Show loading state
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
                    body: JSON.stringify({password: password})
                })
                .then(response => {
                    if (!response.ok) {
                        throw new Error(`HTTP error! status: ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.success) {
                        showPasswordFeedback('✅ ' + data.message, true);
                        setTimeout(() => {
                            hidePasswordModal();
                            updateDisplay();
                            // Redirect based on user type
                            if (data.is_admin) {
                                window.location.href = '/admin';
                            } else {
                                window.location.href = '/shopping';
                            }
                        }, 1000);
                    } else {
                        showPasswordFeedback('❌ ' + data.message, false);
                        document.getElementById('passwordInput').value = '';
                        document.getElementById('passwordInput').focus();
                    }
                })
                .catch(error => {
                    console.error('Login error:', error);
                    showPasswordFeedback('❌ Network error. Please check connection and try again.', false);
                })
                .finally(() => {
                    // Restore button state
                    loginBtn.textContent = originalText;
                    loginBtn.disabled = false;
                });
            }
            
            function submitConfirmPassword() {
                const password = document.getElementById('confirmPasswordInput').value;
                
                if (!password) {
                    showConfirmPasswordFeedback('Please enter password', false);
                    return;
                }
                
                // Show loading state
                const confirmBtn = document.getElementById('confirmPasswordButton');
                const originalText = confirmBtn.textContent;
                confirmBtn.textContent = 'Processing...';
                confirmBtn.disabled = true;
                
                fetch('/api/confirm_add_value', {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    body: JSON.stringify({
                        password: password,
                        amount: selectedAmount
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
                        showConfirmPasswordFeedback('✅ ' + data.message, true);
                        setTimeout(() => {
                            hideConfirmPasswordModal();
                            updateDisplay();
                        }, 2000);
                    } else {
                        showConfirmPasswordFeedback('❌ ' + data.message, false);
                        document.getElementById('confirmPasswordInput').value = '';
                        document.getElementById('confirmPasswordInput').focus();
                    }
                })
                .catch(error => {
                    console.error('Confirm password error:', error);
                    showConfirmPasswordFeedback('❌ Network error. Please check connection and try again.', false);
                })
                .finally(() => {
                    // Restore button state
                    confirmBtn.textContent = originalText;
                    confirmBtn.disabled = false;
                });
            }
            
            function confirmAddValue() {
                let amount = selectedAmount;
                
                // Check if custom amount is entered
                const customAmount = document.getElementById('customAmount').value;
                if (customAmount) {
                    amount = parseFloat(customAmount);
                }
                
                if (!amount || amount <= 0) {
                    showAddValueFeedback('Please select or enter a valid amount', false);
                    return;
                }
                
                selectedAmount = amount;
                hideAddValueModal();
                showConfirmPasswordModal(amount);
            }
            
            function checkBalance() {
                fetch('/api/check_balance', { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            alert('✅ ' + data.message);
                            updateDisplay();
                        } else {
                            alert('❌ ' + data.message);
                        }
                    });
            }
            
            function refreshBalance() {
                fetch('/api/refresh_balance', { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            updateDisplay();
                        } else {
                            alert('❌ ' + data.message);
                        }
                    });
            }
            
            function logout() {
                fetch('/api/logout', { method: 'POST' })
                    .then(() => {
                        updateDisplay();
                    });
            }
            
            function goToAdmin() {
                window.location.href = '/admin';
            }
            
            function goToRewards() {
                window.location.href = '/rewards';
            }
            
            function goToShoppingWithScanned() {
                window.location.href = '/shopping';
            }
            
            function clearScannedBarcodes() {
                fetch('/api/clear_barcodes', { method: 'POST' })
                    .then(() => {
                        updateScannedProducts();
                    });
            }
            
            function removeScannedProduct(barcode) {
                fetch('/api/remove_scanned_product', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ barcode: barcode })
                })
                .then(() => {
                    updateScannedProducts();
                });
            }
            
            // Barcode scanner input handling
            let barcodeBuffer = '';
            let lastKeyTime = Date.now();
            
            document.addEventListener('keydown', function(event) {
                const currentTime = Date.now();
                
                // Reset buffer if too much time has passed between keys
                if (currentTime - lastKeyTime > 100) {
                    barcodeBuffer = '';
                }
                
                lastKeyTime = currentTime;
                
                // Only process digit keys and Enter
                if (event.key >= '0' && event.key <= '9') {
                    barcodeBuffer += event.key;
                } else if (event.key === 'Enter') {
                    if (barcodeBuffer.length >= 8) {  // Accept barcodes of length 8 or more
                        processBarcode(barcodeBuffer);
                    }
                    barcodeBuffer = '';
                    event.preventDefault(); // Prevent form submission
                }
            });
            
            function processBarcode(barcode) {
                fetch('/api/scan_barcode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ barcode: barcode })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        updateScannedProducts();
                    } else {
                        alert('❌ ' + data.message);
                    }
                });
            }
            
            // Handle Enter key in password inputs
            document.addEventListener('DOMContentLoaded', function() {
                const passwordInput = document.getElementById('passwordInput');
                if (passwordInput) {
                    passwordInput.addEventListener('keypress', function(e) {
                        if (e.key === 'Enter') {
                            submitPassword();
                        }
                    });
                }
                
                const confirmPasswordInput = document.getElementById('confirmPasswordInput');
                if (confirmPasswordInput) {
                    confirmPasswordInput.addEventListener('keypress', function(e) {
                        if (e.key === 'Enter') {
                            submitConfirmPassword();
                        }
                    });
                }
                
                const customAmount = document.getElementById('customAmount');
                if (customAmount) {
                    customAmount.addEventListener('input', function() {
                        if (this.value) {
                            selectedAmount = parseFloat(this.value) || 0;
                            // Clear button selections
                            document.querySelectorAll('.amount-btn').forEach(btn => {
                                btn.classList.remove('active');
                            });
                        }
                    });
                    
                    customAmount.addEventListener('keypress', function(e) {
                        if (e.key === 'Enter') {
                            confirmAddValue();
                        }
                    });
                }
            });
            
            setInterval(updateDisplay, 1000);
            setInterval(updateScannedProducts, 2000);
            updateDisplay();
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
    
    # Create JavaScript for initial cart - SIMPLIFIED
    cart_init = '{}'
    if scanned_products:
        cart_items = []
        for product in scanned_products:
            cart_items.append(f'"{product["id"]}": {product["quantity"]}')
        cart_init = '{' + ','.join(cart_items) + '}'
    
    # If there are scanned products, only show those
    if scanned_products:
        # Convert scanned products to HTML
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
    
    # SIMPLIFIED HTML TEMPLATE - FIX THE JAVASCRIPT
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
        // Initialize cart - SIMPLE VERSION
        let cart = {cart_init};
        let userPoints = 0;
        
        console.log('Initial cart:', cart);
        console.log('Cart init string:', '{cart_init}');
        
        // Event delegation for quantity buttons
        document.addEventListener('DOMContentLoaded', function() {{
            // Handle increase button clicks
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
            
            // Initialize display
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
            
            // Ensure quantity doesn't go below 0
            if (newQuantity < 0) return;
            
            // Check stock if increasing
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
            
            // Update the display
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
            // Update all quantity displays based on cart
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
                
                // Calculate points that will be earned (0.001 points per ¥1)
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
            
            // Disable checkout button during processing
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
                    
                    // Reset cart
                    cart = {{}};
                    
                    // Update UI
                    updateAllQuantityDisplays();
                    updateCartDisplay();
                    loadUserPoints();
                    
                    // Clear scanned barcodes
                    fetch('/api/clear_barcodes', {{ method: 'POST' }});
                    
                    // Show success message and redirect
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
        
        // Auto-refresh points every 10 seconds
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
    # Filter products that can be purchased with points (reward_points_cost > 0)
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
            
            // Handle new category selection
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
                        // Clear form
                        document.getElementById('newProductName').value = '';
                        document.getElementById('newProductPrice').value = '';
                        document.getElementById('newProductBarcode').value = '';
                        document.getElementById('newProductStock').value = '100';
                        document.getElementById('newProductPoints').value = '0.0';
                        // Reload page after 2 seconds
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

@app.route('/api/status')
def api_status():
    """Get system status"""
    return jsonify({
        'serial_connected': serial_connected,
        'current_card': current_card
    })

@app.route('/api/check_balance', methods=['POST'])
def api_check_balance():
    """Check and update balance from database"""
    global current_card
    
    if not current_card:
        return jsonify({'success': False, 'message': 'No card detected. Please swipe a card first.'})
    
    card_uid = current_card['card_uid']
    customer_info = get_customer_info(card_uid)
    
    if not customer_info:
        return jsonify({'success': False, 'message': 'Customer information not found'})
    
    name, balance, reward_points, _, _ = customer_info
    
    # Update current card balance
    current_card['balance'] = balance
    current_card['reward_points'] = reward_points
    current_card['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Send updated balance to Arduino
    send_balance_to_arduino(card_uid)
    
    return jsonify({
        'success': True, 
        'message': f'Balance updated: {name} - ¥{balance:.2f}, Points: {reward_points:.3f}',
        'balance': balance,
        'reward_points': reward_points
    })

@app.route('/api/refresh_balance', methods=['POST'])
def api_refresh_balance():
    """Refresh the current card balance display"""
    if refresh_current_card_balance():
        return jsonify({'success': True, 'message': 'Balance display refreshed'})
    else:
        return jsonify({'success': False, 'message': 'No card to refresh'})

@app.route('/api/login', methods=['POST'])
def api_login():
    """Handle user login"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data received'})
        
        password = data.get('password', '')
        
        if not current_card:
            return jsonify({'success': False, 'message': 'No card detected'})
        
        if current_card.get('type') == 'unknown':
            return jsonify({'success': False, 'message': 'Unauthorized card'})
        
        card_uid = current_card['card_uid']
        
        # Authenticate with password
        success, message = authenticate_user(card_uid, password)
        
        if success:
            # Update current_card status
            current_card['authenticated'] = True
            current_card['needs_password'] = False
            
            # Send balance information to Arduino
            send_balance_to_arduino(card_uid)
            
            return jsonify({
                'success': True, 
                'message': f'Welcome {current_card["name"]}! Authentication successful.',
                'user_name': current_card['name'],
                'is_admin': session.get('is_admin', False)
            })
        else:
            return jsonify({'success': False, 'message': message})
            
    except Exception as e:
        return jsonify({'success': False, 'message': 'Server error. Please try again.'})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    """User logout"""
    global current_card
    
    if current_card:
        current_card['authenticated'] = False
        current_card['needs_password'] = True
    
    logout_user()
    
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/confirm_add_value', methods=['POST'])
def api_confirm_add_value():
    """Confirm add value with password verification and process immediately"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data received'})
        
        password = data.get('password', '')
        amount = data.get('amount', 0)
        
        if not current_card:
            return jsonify({'success': False, 'message': 'No card detected'})
        
        if not is_authenticated() and not (current_card and current_card.get('authenticated')):
            return jsonify({'success': False, 'message': 'Please login first'})
        
        if amount <= 0:
            return jsonify({'success': False, 'message': 'Invalid amount'})
        
        # Use the authenticated card UID
        if is_authenticated():
            card_uid = session.get('card_uid')
        else:
            card_uid = current_card['card_uid']
        
        # Verify password again for security
        customer_info = get_customer_info(card_uid)
        if not customer_info:
            return jsonify({'success': False, 'message': 'Customer not found'})
        
        name, balance, reward_points, password_hash, _ = customer_info
        
        if not verify_password(password, password_hash):
            return jsonify({'success': False, 'message': '❌ Invalid password. Please try again.'})
        
        # Process the add value immediately
        success, message = add_value(card_uid, amount)
        
        if success:
            return jsonify({
                'success': True,
                'message': message
            })
        else:
            return jsonify({
                'success': False,
                'message': message
            })
        
    except Exception as e:
        return jsonify({'success': False, 'message': '❌ Server error. Please try again.'})

@app.route('/api/auth_status')
def api_auth_status():
    """Get authentication status"""
    auth_info = get_current_user()
    return jsonify({
        'authenticated': is_authenticated(),
        'current_user': auth_info,
        'current_card': current_card
    })

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
        
        card_uid = session.get('card_uid')
        print(f"💳 Card UID: {card_uid}")
        
        customer_info = get_customer_info(card_uid)
        
        if not customer_info:
            print("❌ Customer not found")
            return jsonify({'success': False, 'message': 'Customer information not found'})
        
        name, balance, reward_points, _, _ = customer_info
        print(f"👤 Customer: {name}, Balance: {balance}, Points: {reward_points}")
        
        # Process each purchase
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
            
            # Check stock
            if stock < quantity:
                print(f"❌ Insufficient stock: {product_name} (stock: {stock}, requested: {quantity})")
                return jsonify({'success': False, 'message': f'Insufficient stock for {product_name}'})
            
            # Check balance
            item_total = product_price * quantity
            if balance < item_total:
                print(f"❌ Insufficient balance: needed {item_total}, have {balance}")
                return jsonify({'success': False, 'message': f'Insufficient balance for {product_name}'})
            
            # Record purchase
            success, message = record_purchase(card_uid, product_id, quantity, use_points=False)
            if not success:
                print(f"❌ Purchase failed: {message}")
                return jsonify({'success': False, 'message': message})
            
            total_price += item_total
            total_earned_points += item_total * 0.001
        
        print(f"✅ Purchase successful! Total: ¥{total_price:.2f}, Earned points: {total_earned_points:.3f}")
        
        # Update current card balance from database
        updated_info = get_customer_info(card_uid)
        if updated_info:
            _, new_balance, new_points, _, _ = updated_info
            if current_card:
                current_card['balance'] = new_balance
                current_card['reward_points'] = new_points
            # Send updated balance to Arduino
            send_balance_to_arduino(card_uid)
        
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
    
    card_uid = session.get('card_uid')
    
    # Process each redemption
    total_points_used = 0
    
    for item in items:
        product_id = item['productId']
        quantity = item['quantity']
        success, message = record_purchase(card_uid, product_id, quantity, use_points=True)
        if not success:
            return jsonify({'success': False, 'message': message})
        
        # Calculate points used for this item
        products = get_products()
        product = next((p for p in products if p[0] == product_id), None)
        if product:
            total_points_used += product[6] * quantity
    
    # Update current card points from database
    updated_info = get_customer_info(card_uid)
    if updated_info:
        current_card['reward_points'] = updated_info[2]
        # Send updated balance to Arduino
        send_balance_to_arduino(card_uid)
    
    return jsonify({
        'success': True, 
        'message': f'✅ Redemption successful! Used {total_points_used:.1f} points', 
        'points_used': total_points_used
    })

@app.route('/api/user_points')
def api_user_points():
    """Get current user's reward points"""
    if not is_authenticated():
        return jsonify({'success': False, 'points': 0})
    
    card_uid = session.get('card_uid')
    customer_info = get_customer_info(card_uid)
    
    if customer_info:
        return jsonify({'success': True, 'points': customer_info[2]})
    else:
        return jsonify({'success': False, 'points': 0})

@app.route('/api/my_purchase_history')
def api_my_purchase_history():
    """Get current user's purchase history only"""
    if not is_authenticated():
        return jsonify({'history': []})
    
    card_uid = session.get('card_uid')
    history_data = get_purchase_history(card_uid)
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
    
    card_uid = session.get('card_uid')
    history_data = get_topup_history(card_uid)
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
    
    card_uid = session.get('card_uid')
    history_data = get_reward_redemptions(card_uid)
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
        
        # Add barcode to scanned list
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
            # Remove all instances of this barcode
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
    
    if connect_serial():
        threading.Thread(target=serial_listener, daemon=True).start()
        print(f"\n🌐 Access: http://localhost:5000")
        print("📱 Features:")
        print("   🏠 Home - Card recognition + Barcode scanner")
        print("   🛒 Shopping - Auto-populates with scanned products")
        print("   🎁 Rewards Shop - Exchange points for products")
        print("   📊 My Purchase History - With barcode information")
        print("   💰 My Top-up History - User's top-up records")
        print("   🎁 My Reward History - Reward redemption records")
        print("   👑 Admin Panel - Product management with barcode support")
        print("\n🔐 Security Features:")
        print("   ✅ Password authentication required")
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
        print("\n💳 Test Cards & Passwords:")
        print("   👑 Admin - 9073ACED - Password: admin123")
        print("   👑 William (Admin) - 300757DB - Password: 12345678")
        print("\n📦 Test Barcodes (EAN-13):")
        print("   4901777013931 - Coca-Cola")
        print("   4902102118878 - Potato Chips")
        print("   4901777242950 - Chocolate")
        print("   4901777242967 - Mineral Water")
        print("=" * 60)
        
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    else:
        print("\n❌ Unable to connect to COM6")
        print("💡 Please check:")
        print("   1. Arduino is connected to COM6")
        print("   2. Serial monitor is closed")
        print("   3. Correct drivers are installed")