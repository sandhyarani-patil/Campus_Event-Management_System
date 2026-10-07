import mysql.connector
from config import DB_CONFIG
import bcrypt
from datetime import datetime, timedelta
import sys

def init_database():
    try:
        # Connect to MySQL server
        conn = mysql.connector.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password']
        )
        cursor = conn.cursor()
        
        # Create database
        cursor.execute("CREATE DATABASE IF NOT EXISTS campus_events")
        cursor.execute("USE campus_events")
        
        # Create tables
        tables = [
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                role ENUM('admin', 'student') DEFAULT 'student',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS students (
                student_id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT,
                full_name VARCHAR(255) NOT NULL,
                student_number VARCHAR(50) UNIQUE NOT NULL,
                department VARCHAR(100),
                phone VARCHAR(20),
                year_level VARCHAR(50),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                event_date DATE NOT NULL,
                event_time TIME NOT NULL,
                venue VARCHAR(255) NOT NULL,
                registration_deadline DATETIME NOT NULL,
                capacity INT,
                category VARCHAR(100),
                image_path VARCHAR(500),
                created_by INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS registrations (
                registration_id INT AUTO_INCREMENT PRIMARY KEY,
                event_id INT,
                student_id INT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status ENUM('registered', 'waitlist', 'cancelled') DEFAULT 'registered',
                FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE,
                FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
                UNIQUE KEY unique_registration (event_id, student_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS gallery (
                image_id INT AUTO_INCREMENT PRIMARY KEY,
                event_id INT,
                image_path VARCHAR(500) NOT NULL,
                caption VARCHAR(500),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE,
                UNIQUE KEY unique_image_event (image_path, event_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS testimonials (
                testimonial_id INT AUTO_INCREMENT PRIMARY KEY,
                student_id INT,
                event_id INT,
                content TEXT NOT NULL,
                rating INT CHECK (rating >= 1 AND rating <= 5),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status ENUM('pending', 'approved') DEFAULT 'pending',
                FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
                FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE SET NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS contacts (
                contact_id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                subject VARCHAR(500),
                message TEXT NOT NULL,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status ENUM('unread', 'read', 'replied') DEFAULT 'unread'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS replies (
                reply_id INT AUTO_INCREMENT PRIMARY KEY,
                contact_id INT NOT NULL,
                admin_user_id INT NOT NULL,
                reply_message TEXT NOT NULL,
                replied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (contact_id) REFERENCES contacts(contact_id) ON DELETE CASCADE,
                FOREIGN KEY (admin_user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
            """
        ]
        
        for table in tables:
            cursor.execute(table)
        
        # Create default admin user
        hashed_password = bcrypt.hashpw('admin123'.encode('utf-8'), bcrypt.gensalt())
        cursor.execute(
            "INSERT IGNORE INTO users (email, password, role) VALUES (%s, %s, %s)",
            ('admin@campus.edu', hashed_password.decode('utf-8'), 'admin')
        )
        
        conn.commit()
        print("✅ Database initialized successfully!")
        print("📧 Default admin account: admin@campus.edu / admin123")
        
    except mysql.connector.Error as e:
        # More helpful error messages for common failures
        err_msg = str(e)
        if hasattr(e, 'errno') and e.errno == 1045:
            print("❌ Authentication error connecting to MySQL. Check DB_USER/DB_PASSWORD in your environment or .env file.")
            print(f"Details: {err_msg}")
        else:
            print(f"❌ Error: {err_msg}")
        # Exit with non-zero code so CI/automation knows it failed
        return
    finally:
        # Only attempt to close if conn was created and is connected
        if 'conn' in locals() and conn is not None:
            try:
                if conn.is_connected():
                    try:
                        cursor.close()
                    except Exception:
                        pass
                    conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    init_database()