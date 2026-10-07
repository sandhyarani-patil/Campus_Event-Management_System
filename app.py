from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, make_response
from flask_wtf.csrf import CSRFProtect
import mysql.connector
from types import SimpleNamespace
import bcrypt
from datetime import datetime, date, time, timedelta
import os
import uuid
from config import Config, DB_CONFIG
from functools import wraps
import io

# ReportLab imports for PDF generation
try:
    import reportlab  # type: ignore
    from reportlab.lib.pagesizes import letter, A4  # type: ignore
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore
    from reportlab.lib.units import inch  # type: ignore
    from reportlab.lib.colors import HexColor, black, white  # type: ignore
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image  # type: ignore
    from reportlab.lib import colors  # type: ignore
    from reportlab.graphics.shapes import Drawing, Rect  # type: ignore
    from reportlab.graphics import renderPDF  # type: ignore
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT  # type: ignore
    REPORTLAB_AVAILABLE = True
except ImportError:
    # ReportLab not available - PDF generation will be disabled
    REPORTLAB_AVAILABLE = False
    # Define dummy classes to prevent errors
    class SimpleDocTemplate:  # type: ignore
        def __init__(self, *args, **kwargs):
            raise RuntimeError("ReportLab not available")
    class A4: pass  # type: ignore
    class getSampleStyleSheet:  # type: ignore
        def __getitem__(self, key): return None
    class ParagraphStyle: pass  # type: ignore
    class inch: pass  # type: ignore
    class HexColor: pass  # type: ignore
    class black: pass  # type: ignore
    class white: pass  # type: ignore
    class Paragraph: pass  # type: ignore
    class Spacer: pass  # type: ignore
    class Table: pass  # type: ignore
    class TableStyle: pass  # type: ignore
    class Image: pass  # type: ignore
    class colors: pass  # type: ignore
    class Drawing: pass  # type: ignore
    class Rect: pass  # type: ignore
    class renderPDF: pass  # type: ignore
    class TA_CENTER: pass  # type: ignore
    class TA_LEFT: pass  # type: ignore
    class TA_RIGHT: pass  # type: ignore

app = Flask(__name__)
app.config.from_object(Config)

# Ensure Flask has a secret key
app.secret_key = app.config.get('SECRET_KEY')

# Configure file upload limits
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Initialize CSRF protection
csrf = CSRFProtect(app)

# Handle file size errors
@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({'success': False, 'message': 'File too large. Maximum file size is 16MB.'}), 413


class DBWrapper:
    """Lightweight DB wrapper that connects lazily and provides cursor with dictionary results."""
    
    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = None

    def connect(self):
        if self.conn:
            try:
                if self.conn.is_connected():
                    return
            except Exception:
                pass  # proceed to reconnect

        try:
            print(f"Connecting to MySQL: host={self.cfg['host']}, user={self.cfg['user']}, db={self.cfg['database']}")
            self.conn = mysql.connector.connect(
                host=self.cfg['host'],
                user=self.cfg['user'],
                password=self.cfg['password'],
                database=self.cfg['database']
            )
            print("✓ Database connection successful!")
        except Exception as e:
            print(f"× Database connection failed: {str(e)}")
            self.conn = None
            raise RuntimeError(f"Database connection failed: {str(e)}")

    def cursor(self, dictionary=True):
        self.connect()
        if not self.conn:
            raise RuntimeError('Database not available. Check DB credentials in environment or config.')
        return self.conn.cursor(dictionary=dictionary)

    def commit(self):
        if not self.conn:
            raise RuntimeError('Database not available. Cannot commit.')
        return self.conn.commit()

    def rollback(self):
        if not self.conn:
            raise RuntimeError('Database not available. Cannot rollback.')
        return self.conn.rollback()


# Create a single DBWrapper instance for the application
db = DBWrapper(DB_CONFIG)
db_api = SimpleNamespace(connection=db)

# Helper functions
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(hashed_password, user_password):
    return bcrypt.checkpw(user_password.encode('utf-8'), hashed_password.encode('utf-8'))

# Jinja filters for robust date/time formatting across varying DB return types
@app.template_filter('format_date')
def jinja_format_date(value, fmt='%b %d, %Y'):
    try:
        if isinstance(value, datetime):
            return value.strftime(fmt)
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day).strftime(fmt)
        if isinstance(value, str):
            for pattern in ('%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y'):
                try:
                    return datetime.strptime(value, pattern).strftime(fmt)
                except Exception:
                    pass
        return str(value)
    except Exception:
        return str(value)

@app.context_processor
def inject_now():
    return { 'now': datetime.now(), 'timedelta': timedelta }

@app.template_filter('format_time')
def jinja_format_time(value, fmt='%I:%M %p'):
    try:
        if isinstance(value, datetime):
            return value.strftime(fmt)
        if isinstance(value, time):
            dt = datetime(2000, 1, 1, value.hour, value.minute, value.second)
            return dt.strftime(fmt)
        if isinstance(value, timedelta):
            total_seconds = int(value.total_seconds())
            hours = (total_seconds // 3600) % 24
            minutes = (total_seconds % 3600) // 60
            dt = datetime(2000, 1, 1, hours, minutes)
            return dt.strftime(fmt)
        if isinstance(value, str):
            for pattern in ('%H:%M:%S', '%H:%M'):
                try:
                    dt = datetime.strptime(value, pattern)
                    return dt.strftime(fmt)
                except Exception:
                    pass
        return str(value)
    except Exception:
        return str(value)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
def home():
    cursor = db_api.connection.cursor()
    
    # Get upcoming events
    cursor.execute("""
        SELECT * FROM events 
        WHERE registration_deadline > NOW() 
        ORDER BY event_date ASC 
        LIMIT 6
    """)
    events = cursor.fetchall()
    
    # Get statistics
    cursor.execute("SELECT COUNT(*) as total FROM events")
    total_events = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(DISTINCT student_id) as total FROM registrations")
    total_students = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM registrations WHERE status = 'registered'")
    total_registrations = cursor.fetchone()['total']
    
    # Get all approved testimonials
    cursor.execute("""
        SELECT t.*, t.content as testimonial, s.full_name, s.department, s.year_level, e.title as event_title
        FROM testimonials t 
        JOIN students s ON t.student_id = s.student_id 
        LEFT JOIN events e ON t.event_id = e.event_id
        WHERE t.status = 'approved' 
        ORDER BY t.created_at DESC
    """)
    testimonials = cursor.fetchall()
    
    # Calculate testimonials statistics
    total_testimonials = len(testimonials)
    average_rating = sum(t['rating'] for t in testimonials) / len(testimonials) if testimonials else 0
    unique_events = len(set(t['event_id'] for t in testimonials if t['event_id']))
    
    cursor.close()
    
    return render_template('index.html', 
                         events=events,
                         total_events=total_events,
                         total_students=total_students,
                         total_registrations=total_registrations,
                         testimonials=testimonials,
                         total_testimonials=total_testimonials,
                         average_rating=average_rating,
                         unique_events=unique_events)

@app.route('/events')
def events():
    category = request.args.get('category', '')
    search = request.args.get('search', '')
    
    cursor = db_api.connection.cursor()
    
    query = """
        SELECT e.*, 
               COUNT(r.registration_id) as registered_count,
               (e.capacity - COUNT(r.registration_id)) as available_slots
        FROM events e
        LEFT JOIN registrations r ON e.event_id = r.event_id AND r.status = 'registered'
        WHERE 1=1
    """
    params = []
    
    if category:
        query += " AND e.category = %s"
        params.append(category)
    
    if search:
        query += " AND (e.title LIKE %s OR e.description LIKE %s OR e.venue LIKE %s)"
        params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
    
    query += " GROUP BY e.event_id ORDER BY e.event_date ASC"
    
    cursor.execute(query, params)
    events = cursor.fetchall()
    
    # Get categories for filter
    cursor.execute("SELECT DISTINCT category FROM events WHERE category IS NOT NULL")
    categories = cursor.fetchall()
    
    cursor.close()
    
    return render_template('events.html', events=events, categories=categories, 
                         selected_category=category, search_query=search)

@app.route('/gallery')
def gallery():
    cursor = db_api.connection.cursor()
    
    cursor.execute("""
        SELECT g.*, e.title as event_title 
        FROM gallery g 
        LEFT JOIN events e ON g.event_id = e.event_id 
        ORDER BY g.uploaded_at DESC
    """)
    images = cursor.fetchall()
    
    cursor.close()
    return render_template('gallery.html', images=images)

@app.route('/about')
def about():
    cursor = db_api.connection.cursor()
    
    # Get statistics for about page
    cursor.execute("SELECT COUNT(*) as total FROM events")
    total_events = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(DISTINCT student_id) as total FROM registrations")
    total_students = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM registrations")
    total_registrations = cursor.fetchone()['total']
    
    cursor.close()
    
    return render_template('about.html', 
                         total_events=total_events,
                         total_students=total_students,
                         total_registrations=total_registrations)

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        subject = request.form['subject']
        message = request.form['message']
        
        cursor = db_api.connection.cursor()
        cursor.execute(
            "INSERT INTO contacts (name, email, subject, message) VALUES (%s, %s, %s, %s)",
            (name, email, subject, message)
        )
        db_api.connection.commit()
        cursor.close()
        
        flash('Your message has been sent successfully! We will get back to you soon.', 'success')
        return redirect(url_for('contact'))
    
    return render_template('contact.html')

# Authentication routes
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        full_name = request.form['full_name']
        student_number = request.form['student_number']
        department = request.form['department']
        phone = request.form['phone']
        year_level = request.form['year_level']
        
        if password != confirm_password:
            flash('Passwords do not match!', 'error')
            return render_template('register.html')
        
        cursor = db_api.connection.cursor()
        
        try:
            # Check if email already exists
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            if cursor.fetchone():
                flash('Email already registered!', 'error')
                return render_template('register.html')
            
            # Check if student number exists
            cursor.execute("SELECT * FROM students WHERE student_number = %s", (student_number,))
            if cursor.fetchone():
                flash('Student number already registered!', 'error')
                return render_template('register.html')
            
            # Create user
            hashed_password = hash_password(password)
            cursor.execute(
                "INSERT INTO users (email, password, role) VALUES (%s, %s, 'student')",
                (email, hashed_password)
            )
            user_id = cursor.lastrowid
            
            # Create student profile
            cursor.execute(
                """INSERT INTO students (user_id, full_name, student_number, department, phone, year_level) 
                VALUES (%s, %s, %s, %s, %s, %s)""",
                (user_id, full_name, student_number, department, phone, year_level)
            )
            
            db_api.connection.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
            
        except Exception as e:
            db_api.connection.rollback()
            flash('Registration failed. Please try again.', 'error')
        finally:
            cursor.close()
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        cursor = db_api.connection.cursor()
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        
        if user and check_password(user['password'], password):
            session['user_id'] = user['user_id']
            session['email'] = user['email']
            session['role'] = user['role']
            
            # Get student info if student
            if user['role'] == 'student':
                cursor.execute("SELECT * FROM students WHERE user_id = %s", (user['user_id'],))
                student = cursor.fetchone()
                if student:
                    session['student_id'] = student['student_id']
                    session['full_name'] = student['full_name']
            
            cursor.close()
            flash(f'Welcome back, {session.get("full_name", session["email"])}!', 'success')
            return redirect(url_for('home'))
        else:
            cursor.close()
            flash('Invalid email or password!', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('home'))

# Event registration
@app.route('/api/events/register', methods=['POST'])
@csrf.exempt
def register_event():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please login to register for events'})
    
    if session.get('role') != 'student':
        return jsonify({'success': False, 'message': 'Only students can register for events'})
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'message': 'Invalid JSON data'})
    event_id = data.get('event_id')
    if not event_id:
        return jsonify({'success': False, 'message': 'Event ID is required'})
    
    cursor = db_api.connection.cursor()
    
    try:
        # Check if event exists and registration is open
        cursor.execute("SELECT * FROM events WHERE event_id = %s AND registration_deadline > NOW()", (event_id,))
        event = cursor.fetchone()
        
        if not event:
            return jsonify({'success': False, 'message': 'Event not found or registration closed'})
        
        # Check if already registered
        cursor.execute(
            "SELECT * FROM registrations WHERE event_id = %s AND student_id = %s AND status = 'registered'",
            (event_id, session['student_id'])
        )
        if cursor.fetchone():
            return jsonify({'success': False, 'message': 'Already registered for this event'})
        
        # Check capacity
        cursor.execute(
            "SELECT COUNT(*) as count FROM registrations WHERE event_id = %s AND status = 'registered'",
            (event_id,)
        )
        registration_count = cursor.fetchone()['count']
        
        if event['capacity'] and registration_count >= event['capacity']:
            return jsonify({'success': False, 'message': 'Event is at full capacity'})
        
        # Register for event
        cursor.execute(
            "INSERT INTO registrations (event_id, student_id) VALUES (%s, %s)",
            (event_id, session['student_id'])
        )
        
        db_api.connection.commit()
        return jsonify({'success': True, 'message': 'Successfully registered for the event!'})
        
    except Exception as e:
        db_api.connection.rollback()
        return jsonify({'success': False, 'message': 'Registration failed'})
    finally:
        cursor.close()

# Entry Pass Generation
@app.route('/api/events/<int:event_id>/entry-pass')
@app.route('/entry-pass/<int:event_id>')
@login_required
def generate_entry_pass(event_id):
    if session.get('role') != 'student':
        flash('Student access only', 'error')
        return redirect(url_for('home'))
    
    if not REPORTLAB_AVAILABLE:
        flash('PDF generation is not available. Please install reportlab package.', 'error')
        return redirect(url_for('events'))
    
    cursor = db_api.connection.cursor()
    
    try:
        # Get event details
        cursor.execute("SELECT * FROM events WHERE event_id = %s", (event_id,))
        event = cursor.fetchone()
        
        if not event:
            flash('Event not found!', 'error')
            return redirect(url_for('events'))
        
        # Check if student is registered for this event
        cursor.execute("""
            SELECT r.*, s.full_name, s.student_number, s.department, s.year_level, u.email
            FROM registrations r
            JOIN students s ON r.student_id = s.student_id
            JOIN users u ON s.user_id = u.user_id
            WHERE r.event_id = %s AND r.student_id = %s AND r.status = 'registered'
        """, (event_id, session['student_id']))
        
        registration = cursor.fetchone()
        
        if not registration:
            flash('You are not registered for this event!', 'error')
            return redirect(url_for('events'))
        
        # Generate PDF with compact, attractive design
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
        
        # Create styles
        styles = getSampleStyleSheet()
        
        # Custom styles for compact design
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=20,
            spaceAfter=15,
            alignment=TA_CENTER,
            textColor=HexColor('#667eea'),
            fontName='Helvetica-Bold'
        )
        
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=10,
            alignment=TA_CENTER,
            textColor=HexColor('#764ba2'),
            fontName='Helvetica'
        )
        
        header_style = ParagraphStyle(
            'Header',
            parent=styles['Heading3'],
            fontSize=12,
            spaceAfter=8,
            textColor=HexColor('#333333'),
            fontName='Helvetica-Bold'
        )
        
        normal_style = ParagraphStyle(
            'Normal',
            parent=styles['Normal'],
            fontSize=10,
            spaceAfter=4,
            textColor=HexColor('#666666'),
            fontName='Helvetica'
        )
        
        # Build the PDF content
        story = []
        
        # Header section with gradient effect
        story.append(Spacer(1, 10))
        
        # Title
        story.append(Paragraph("🎫 EVENT TICKET", title_style))
        story.append(Paragraph("YOUR ENTRY PASS", subtitle_style))
        story.append(Spacer(1, 15))
        
        # Main content in two columns
        # Left column - Event details
        event_data = [
            ['📅 Date:', event['event_date'].strftime('%B %d, %Y') if isinstance(event['event_date'], date) else str(event['event_date'])],
            ['🕐 Time:', event['event_time'].strftime('%I:%M %p') if isinstance(event['event_time'], time) else str(event['event_time'])],
            ['📍 Venue:', event['venue']],
            ['🏷️ Category:', event['category']]
        ]
        
        event_table = Table(event_data, colWidths=[1.2*inch, 2.8*inch])
        event_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), HexColor('#f8f9fa')),
            ('TEXTCOLOR', (0, 0), (0, -1), HexColor('#333333')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, HexColor('#e9ecef'))
        ]))
        
        # Right column - Student details
        student_data = [
            ['👤 Name:', registration['full_name']],
            ['🎓 Student #:', registration['student_number']],
            ['🏫 Department:', registration['department']],
            ['📚 Year:', registration['year_level']]
        ]
        
        student_table = Table(student_data, colWidths=[1.2*inch, 2.8*inch])
        student_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), HexColor('#e3f2fd')),
            ('TEXTCOLOR', (0, 0), (0, -1), HexColor('#333333')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, HexColor('#bbdefb'))
        ]))
        
        # Create two-column layout
        two_column_data = [
            [event_table, student_table]
        ]
        
        two_column_table = Table(two_column_data, colWidths=[4*inch, 4*inch])
        two_column_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))
        
        story.append(two_column_table)
        story.append(Spacer(1, 15))
        
        # Event title (larger)
        story.append(Paragraph(f"<b>{event['title']}</b>", header_style))
        story.append(Spacer(1, 10))
        
        # Important notes (compact)
        story.append(Paragraph("📋 IMPORTANT NOTES", header_style))
        notes_text = "• Bring this pass for event verification • Arrive 15 minutes early • Keep this pass safe • Contact organizer for questions"
        story.append(Paragraph(notes_text, normal_style))
        story.append(Spacer(1, 15))
        
        # Footer with registration info and QR code area
        footer_data = [
            [f"🎫 Registration ID: REG-{registration['registration_id']:06d}", f"📧 {registration['email']}"],
            [f"📅 Registered: {registration['registered_at'].strftime('%b %d, %Y')}", f"🕐 Generated: {datetime.now().strftime('%b %d, %Y at %I:%M %p')}"]
        ]
        
        footer_table = Table(footer_data, colWidths=[4*inch, 4*inch])
        footer_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, -1), white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
        ]))
        
        story.append(footer_table)
        story.append(Spacer(1, 10))
        
        # Add a decorative border and QR code placeholder
        qr_data = [
            ["QR CODE", "VALIDATION"],
            ["", f"REG-{registration['registration_id']:06d}"]
        ]
        
        qr_table = Table(qr_data, colWidths=[2*inch, 6*inch])
        qr_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), HexColor('#f8f9fa')),
            ('BACKGROUND', (1, 0), (1, -1), HexColor('#e9ecef')),
            ('TEXTCOLOR', (0, 0), (-1, -1), HexColor('#333333')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 1, HexColor('#dee2e6'))
        ]))
        
        story.append(qr_table)
        
        # Build PDF
        doc.build(story)
        
        # Get PDF content
        pdf_content = buffer.getvalue()
        buffer.close()
        
        # Create response
        response = make_response(pdf_content)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=entry-pass-{event_id}-{registration["student_number"]}.pdf'
        
        return response
        
    except Exception as e:
        flash('Error generating entry pass!', 'error')
        return redirect(url_for('events'))
    finally:
        cursor.close()

# User profile
@app.route('/profile')
@login_required
def profile():
    if session.get('role') != 'student':
        flash('Student profile access only', 'error')
        return redirect(url_for('home'))
    
    cursor = db_api.connection.cursor()
    
    # Get student details
    cursor.execute("SELECT * FROM students WHERE student_id = %s", (session['student_id'],))
    student = cursor.fetchone()
    
    # Get registered events
    cursor.execute("""
        SELECT e.*, r.registered_at 
        FROM events e 
        JOIN registrations r ON e.event_id = r.event_id 
        WHERE r.student_id = %s AND r.status = 'registered'
        ORDER BY e.event_date DESC
    """, (session['student_id'],))
    registered_events = cursor.fetchall()
    
    # Get user testimonials
    cursor.execute("""
        SELECT t.*, t.content as testimonial, e.title as event_title
        FROM testimonials t
        JOIN events e ON t.event_id = e.event_id
        WHERE t.student_id = %s
        ORDER BY t.created_at DESC
    """, (session['student_id'],))
    user_testimonials = cursor.fetchall()
    
    # Get student's messages and replies (based on email)
    cursor.execute("""
        SELECT c.*, 
               r.reply_message, 
               r.replied_at,
               u.email as admin_email
        FROM contacts c
        LEFT JOIN replies r ON c.contact_id = r.contact_id
        LEFT JOIN users u ON r.admin_user_id = u.user_id
        WHERE c.email = %s
        ORDER BY c.submitted_at DESC
    """, (session['email'],))
    messages_with_replies = cursor.fetchall()
    
    cursor.close()
    
    return render_template('user/profile.html', 
                         student=student, 
                         registered_events=registered_events, 
                         user_testimonials=user_testimonials,
                         messages_with_replies=messages_with_replies)

# Student messages page
@app.route('/my-messages')
@login_required
def student_messages():
    if session.get('role') != 'student':
        flash('Student access only', 'error')
        return redirect(url_for('home'))
    
    cursor = db_api.connection.cursor()
    
    # Get all messages from this student with any replies
    cursor.execute("""
        SELECT c.*, 
               r.reply_message, 
               r.replied_at,
               u.email as admin_email
        FROM contacts c
        LEFT JOIN replies r ON c.contact_id = r.contact_id
        LEFT JOIN users u ON r.admin_user_id = u.user_id
        WHERE c.email = %s
        ORDER BY c.submitted_at DESC
    """, (session['email'],))
    messages_with_replies = cursor.fetchall()
    
    cursor.close()
    
    return render_template('user/my_messages.html', messages_with_replies=messages_with_replies)

# Testimonial submission
@app.route('/submit_testimonial', methods=['POST'])
@login_required
def submit_testimonial():
    if session.get('role') != 'student':
        flash('Student access only', 'error')
        return redirect(url_for('home'))
    
    event_id = request.form.get('event_id')
    rating = request.form.get('rating')
    testimonial_text = request.form.get('testimonial')
    
    if not all([event_id, rating, testimonial_text]):
        flash('Please fill in all fields', 'error')
        return redirect(url_for('profile'))
    
    cursor = db_api.connection.cursor()
    
    try:
        # Check if student is registered for this event
        cursor.execute("""
            SELECT r.* FROM registrations r
            WHERE r.event_id = %s AND r.student_id = %s AND r.status = 'registered'
        """, (event_id, session['student_id']))
        
        if not cursor.fetchone():
            flash('You can only submit testimonials for events you have registered for', 'error')
            return redirect(url_for('profile'))
        
        # Check if testimonial already exists for this event
        cursor.execute("""
            SELECT * FROM testimonials 
            WHERE event_id = %s AND student_id = %s
        """, (event_id, session['student_id']))
        
        if cursor.fetchone():
            flash('You have already submitted a testimonial for this event', 'error')
            return redirect(url_for('profile'))
        
        # Insert testimonial
        cursor.execute("""
            INSERT INTO testimonials (event_id, student_id, rating, content, status)
            VALUES (%s, %s, %s, %s, 'pending')
        """, (event_id, session['student_id'], int(rating), testimonial_text))
        
        db_api.connection.commit()
        flash('Thank you for your testimonial! It will be reviewed before being published.', 'success')
        
    except Exception as e:
        db_api.connection.rollback()
        print(f"Error submitting testimonial: {e}")
        flash('Error submitting testimonial. Please try again.', 'error')
    finally:
        cursor.close()
    
    return redirect(url_for('profile'))

# Testimonials page
@app.route('/testimonials')
def testimonials():
    cursor = db_api.connection.cursor()
    
    # Get approved testimonials with student and event info
    cursor.execute("""
        SELECT t.*, t.content as testimonial, s.full_name, s.department, s.year_level, e.title as event_title
        FROM testimonials t
        JOIN students s ON t.student_id = s.student_id
        LEFT JOIN events e ON t.event_id = e.event_id
        WHERE t.status = 'approved'
        ORDER BY t.created_at DESC
    """)
    testimonials = cursor.fetchall()
    
    # Calculate statistics
    total_testimonials = len(testimonials)
    if total_testimonials > 0:
        average_rating = sum(t['rating'] for t in testimonials) / total_testimonials
    else:
        average_rating = 0
    
    # Get unique events count
    cursor.execute("""
        SELECT COUNT(DISTINCT event_id) as unique_events
        FROM testimonials 
        WHERE status = 'approved'
    """)
    unique_events = cursor.fetchone()['unique_events']
    
    cursor.close()
    
    return render_template('testimonials.html', 
                         testimonials=testimonials,
                         average_rating=average_rating,
                         unique_events=unique_events)

# Admin routes
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    cursor = db_api.connection.cursor()
    
    # Dashboard statistics
    cursor.execute("SELECT COUNT(*) as total FROM events")
    total_events = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM students")
    total_students = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM registrations WHERE status = 'registered'")
    total_registrations = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM contacts WHERE status = 'unread'")
    unread_messages = cursor.fetchone()['total']
    
    # Recent events
    cursor.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT 5")
    recent_events = cursor.fetchall()
    
    # Recent registrations
    cursor.execute("""
        SELECT r.*, e.title, s.full_name 
        FROM registrations r
        JOIN events e ON r.event_id = e.event_id
        JOIN students s ON r.student_id = s.student_id
        ORDER BY r.registered_at DESC LIMIT 5
    """)
    recent_registrations = cursor.fetchall()
    
    cursor.close()
    
    return render_template('admin/dashboard.html',
                         total_events=total_events,
                         total_students=total_students,
                         total_registrations=total_registrations,
                         unread_messages=unread_messages,
                         recent_events=recent_events,
                         recent_registrations=recent_registrations)

@app.route('/admin/events')
@admin_required
def admin_events():
    cursor = db_api.connection.cursor()
    cursor.execute("""
        SELECT e.*, COUNT(r.registration_id) as registrations_count 
        FROM events e 
        LEFT JOIN registrations r ON e.event_id = r.event_id AND r.status = 'registered'
        GROUP BY e.event_id 
        ORDER BY e.event_date DESC
    """)
    events = cursor.fetchall()
    cursor.close()
    
    # Get current datetime for comparison
    now = datetime.now()
    
    return render_template('admin/events.html', events=events, now=now)

@app.route('/admin/events/create', methods=['GET', 'POST'])
@admin_required
def create_event():
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        event_date = request.form['event_date']
        event_time = request.form['event_time']
        venue = request.form['venue']
        registration_deadline = request.form['registration_deadline']
        capacity = request.form['capacity'] or None
        category = request.form['category']
        
        cursor = db_api.connection.cursor()
        cursor.execute(
            """INSERT INTO events (title, description, event_date, event_time, venue, 
            registration_deadline, capacity, category, created_by) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (title, description, event_date, event_time, venue, registration_deadline, 
             capacity, category, session['user_id'])
        )
        db_api.connection.commit()
        cursor.close()
        
        flash('Event created successfully!', 'success')
        return redirect(url_for('admin_events'))
    
    # Get stats for the template
    cursor = db_api.connection.cursor()
    cursor.execute("SELECT COUNT(*) as total FROM events")
    total_events = cursor.fetchone()['total']
    
    cursor.execute("""
        SELECT e.*, COUNT(r.registration_id) as registrations_count
        FROM events e
        LEFT JOIN registrations r ON e.event_id = r.event_id AND r.status = 'registered'
        GROUP BY e.event_id
        ORDER BY e.created_at DESC 
        LIMIT 5
    """)
    events = cursor.fetchall()
    
    cursor.close()
    
    return render_template('admin/create_event.html', total_events=total_events, events=events)

# Students Management
@app.route('/admin/students')
@admin_required
def admin_students():
    cursor = db_api.connection.cursor()
    
    # Get all students with registration counts
    cursor.execute("""
        SELECT s.*, u.email, u.role, u.created_at,
               COUNT(r.registration_id) as registration_count,
               'active' as status
        FROM students s
        JOIN users u ON s.user_id = u.user_id
        LEFT JOIN registrations r ON s.student_id = r.student_id AND r.status = 'registered'
        GROUP BY s.student_id
        ORDER BY u.created_at DESC
    """)
    students = cursor.fetchall()
    
    cursor.close()
    return render_template('admin/students.html', students=students)

# Gallery Management
@app.route('/admin/gallery')
@admin_required
def admin_gallery():
    cursor = db_api.connection.cursor()
    
    # Get all gallery images with event info
    cursor.execute("""
        SELECT g.*, e.title as event_title, e.event_date
        FROM gallery g
        LEFT JOIN events e ON g.event_id = e.event_id
        ORDER BY g.uploaded_at DESC
    """)
    images = cursor.fetchall()
    
    # Get events for filter
    cursor.execute("SELECT event_id, title FROM events ORDER BY title")
    events = cursor.fetchall()
    
    cursor.close()
    return render_template('admin/gallery.html', images=images, events=events)

# Gallery Upload Routes
@app.route('/admin/gallery/upload', methods=['POST'])
@admin_required
def upload_gallery_image():
    try:
        print(f"Upload request received. Files: {list(request.files.keys())}")
        print(f"Form data: {dict(request.form)}")
        
        if 'image' not in request.files:
            print("No image file in request")
            return jsonify({'success': False, 'message': 'No image file provided'}), 400
        
        file = request.files['image']
        event_id = request.form.get('event_id')
        caption = request.form.get('caption', '')
        
        print(f"File: {file.filename}, Event ID: {event_id}, Caption: {caption}")
        
        if file.filename == '':
            print("Empty filename")
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        if not event_id:
            print("No event ID provided")
            return jsonify({'success': False, 'message': 'Event selection is required'}), 400
        
        # Validate file type
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        if not ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions):
            print(f"Invalid file type: {file.filename}")
            return jsonify({'success': False, 'message': 'Invalid file type. Only PNG, JPG, JPEG, GIF, and WebP are allowed.'}), 400
        
        # Check file size (5MB limit)
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)  # Reset file pointer
        
        print(f"File size: {file_size} bytes")
        if file_size > 5 * 1024 * 1024:  # 5MB
            print("File too large")
            return jsonify({'success': False, 'message': 'File size too large. Maximum 5MB allowed.'}), 400
        
        # Generate unique filename
        file_ext = file.filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4().hex}_{int(datetime.now().timestamp())}.{file_ext}"
        
        # Save file
        upload_path = os.path.join('static', 'images', 'gallery', unique_filename)
        os.makedirs(os.path.dirname(upload_path), exist_ok=True)
        file.save(upload_path)
        
        # Save to database
        cursor = db_api.connection.cursor()
        cursor.execute("""
            INSERT INTO gallery (event_id, image_path, caption)
            VALUES (%s, %s, %s)
        """, (event_id, unique_filename, caption))
        db_api.connection.commit()
        cursor.close()
        
        print("Image uploaded successfully")
        return jsonify({'success': True, 'message': 'Image uploaded successfully'}), 200
        
    except Exception as e:
        print(f"Error uploading image: {e}")
        return jsonify({'success': False, 'message': 'Upload failed. Please try again.'}), 500

@app.route('/admin/gallery/bulk-upload', methods=['POST'])
@admin_required
def bulk_upload_gallery_images():
    try:
        print(f"Bulk upload request received. Files: {list(request.files.keys())}")
        print(f"Form data: {dict(request.form)}")
        
        if 'images' not in request.files:
            print("No images in request")
            return jsonify({'success': False, 'message': 'No image files provided'}), 400
        
        files = request.files.getlist('images')
        event_id = request.form.get('event_id')
        
        print(f"Number of files: {len(files)}, Event ID: {event_id}")
        
        if not files or len(files) == 0:
            print("No files selected")
            return jsonify({'success': False, 'message': 'No files selected'}), 400
        
        if not event_id:
            print("No event ID provided")
            return jsonify({'success': False, 'message': 'Event selection is required'}), 400
        
        if len(files) > 10:
            print("Too many files")
            return jsonify({'success': False, 'message': 'Maximum 10 images allowed at once'}), 400
        
        uploaded_count = 0
        failed_files = []
        
        # Validate all files first
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
        for file in files:
            if file.filename == '':
                continue
                
            if not ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in allowed_extensions):
                failed_files.append(f"{file.filename}: Invalid file type")
                continue
            
            # Check file size
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)  # Reset file pointer
            
            if file_size > 5 * 1024 * 1024:  # 5MB
                failed_files.append(f"{file.filename}: File too large (max 5MB)")
                continue
        
        if failed_files and len(failed_files) == len(files):
            print("All files failed validation")
            return jsonify({'success': False, 'message': 'All files failed validation: ' + '; '.join(failed_files)}), 400
        
        # Process valid files
        cursor = db_api.connection.cursor()
        
        for file in files:
            if file.filename == '':
                continue
                
            # Skip files that failed validation
            file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
            if file_ext not in allowed_extensions:
                continue
                
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
            
            if file_size > 5 * 1024 * 1024:
                continue
            
            try:
                # Generate unique filename
                unique_filename = f"{uuid.uuid4().hex}_{int(datetime.now().timestamp())}.{file_ext}"
                
                # Save file
                upload_path = os.path.join('static', 'images', 'gallery', unique_filename)
                os.makedirs(os.path.dirname(upload_path), exist_ok=True)
                file.save(upload_path)
                
                # Save to database with original filename as caption
                original_name = os.path.splitext(file.filename)[0].replace('_', ' ').replace('-', ' ').title()
                cursor.execute("""
                    INSERT INTO gallery (event_id, image_path, caption)
                    VALUES (%s, %s, %s)
                """, (event_id, unique_filename, original_name))
                
                uploaded_count += 1
                
            except Exception as e:
                failed_files.append(f"{file.filename}: Upload error")
                print(f"Error uploading {file.filename}: {e}")
        
        db_api.connection.commit()
        cursor.close()
        
        if uploaded_count > 0:
            message = f"Successfully uploaded {uploaded_count} image(s)"
            if failed_files:
                message += f". {len(failed_files)} file(s) failed: {'; '.join(failed_files[:3])}"
                if len(failed_files) > 3:
                    message += "..."
            print(f"Bulk upload completed: {message}")
            return jsonify({'success': True, 'message': message}), 200
        else:
            print("No images were uploaded")
            return jsonify({'success': False, 'message': 'No images were uploaded. ' + '; '.join(failed_files)}), 400
        
    except Exception as e:
        print(f"Error in bulk upload: {e}")
        return jsonify({'success': False, 'message': 'Bulk upload failed. Please try again.'}), 500

@app.route('/admin/gallery/edit', methods=['POST'])
@admin_required
def edit_gallery_image():
    try:
        print(f"Edit request received. Form data: {dict(request.form)}")
        
        image_id = request.form.get('image_id')
        event_id = request.form.get('event_id')
        caption = request.form.get('caption', '')
        
        if not image_id:
            print("No image ID provided")
            return jsonify({'success': False, 'message': 'Image ID is required'}), 400
        
        if not event_id:
            print("No event ID provided")
            return jsonify({'success': False, 'message': 'Event selection is required'}), 400
        
        # Verify image exists
        cursor = db_api.connection.cursor()
        cursor.execute("SELECT image_id FROM gallery WHERE image_id = %s", (image_id,))
        if not cursor.fetchone():
            cursor.close()
            print(f"Image {image_id} not found")
            return jsonify({'success': False, 'message': 'Image not found'}), 404
        
        # Verify event exists
        cursor.execute("SELECT event_id FROM events WHERE event_id = %s", (event_id,))
        if not cursor.fetchone():
            cursor.close()
            print(f"Event {event_id} not found")
            return jsonify({'success': False, 'message': 'Selected event not found'}), 404
        
        # Update the image
        cursor.execute("""
            UPDATE gallery 
            SET event_id = %s, caption = %s 
            WHERE image_id = %s
        """, (event_id, caption, image_id))
        
        db_api.connection.commit()
        cursor.close()
        
        print(f"Image {image_id} updated successfully")
        return jsonify({'success': True, 'message': 'Image updated successfully'}), 200
        
    except Exception as e:
        print(f"Error editing image: {e}")
        return jsonify({'success': False, 'message': 'Failed to update image. Please try again.'}), 500

@app.route('/admin/gallery/delete/<int:image_id>', methods=['POST'])
@admin_required
def delete_gallery_image(image_id):
    try:
        print(f"Delete request received for image ID: {image_id}")
        
        # Get image info before deletion
        cursor = db_api.connection.cursor()
        cursor.execute("SELECT image_path FROM gallery WHERE image_id = %s", (image_id,))
        image_data = cursor.fetchone()
        
        if not image_data:
            cursor.close()
            print(f"Image {image_id} not found")
            return jsonify({'success': False, 'message': 'Image not found'}), 404
        
        image_path = image_data['image_path']
        
        # Delete from database
        cursor.execute("DELETE FROM gallery WHERE image_id = %s", (image_id,))
        db_api.connection.commit()
        cursor.close()
        
        # Delete physical file
        try:
            file_path = os.path.join('static', 'images', 'gallery', image_path)
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"Deleted file: {file_path}")
            else:
                print(f"File not found: {file_path}")
        except Exception as file_error:
            print(f"Error deleting file {file_path}: {file_error}")
            # Continue anyway - database deletion succeeded
        
        print(f"Image {image_id} deleted successfully")
        return jsonify({'success': True, 'message': 'Image deleted successfully'}), 200
        
    except Exception as e:
        print(f"Error deleting image: {e}")
        return jsonify({'success': False, 'message': 'Failed to delete image. Please try again.'}), 500

# Testimonials Management
@app.route('/admin/testimonials')
@admin_required
def admin_testimonials():
    cursor = db_api.connection.cursor()
    
    # Get all testimonials with student info
    cursor.execute("""
        SELECT t.*, t.content as testimonial, s.full_name, s.department
        FROM testimonials t
        JOIN students s ON t.student_id = s.student_id
        ORDER BY t.created_at DESC
    """)
    testimonials = cursor.fetchall()
    
    cursor.close()
    return render_template('admin/testimonials.html', testimonials=testimonials)

# Admin testimonial management routes
@app.route('/admin/testimonials/<int:testimonial_id>/approve', methods=['POST'])
@admin_required
def approve_testimonial(testimonial_id):
    cursor = db_api.connection.cursor()
    
    try:
        cursor.execute("""
            UPDATE testimonials 
            SET status = 'approved' 
            WHERE testimonial_id = %s
        """, (testimonial_id,))
        
        db_api.connection.commit()
        flash('Testimonial approved successfully!', 'success')
    except Exception as e:
        db_api.connection.rollback()
        flash('Error approving testimonial!', 'error')
    finally:
        cursor.close()
    
    return redirect(url_for('admin_testimonials'))

@app.route('/admin/testimonials/<int:testimonial_id>/reject', methods=['POST'])
@admin_required
def reject_testimonial(testimonial_id):
    cursor = db_api.connection.cursor()
    
    try:
        cursor.execute("""
            UPDATE testimonials 
            SET status = 'rejected' 
            WHERE testimonial_id = %s
        """, (testimonial_id,))
        
        db_api.connection.commit()
        flash('Testimonial rejected successfully!', 'success')
    except Exception as e:
        db_api.connection.rollback()
        flash('Error rejecting testimonial!', 'error')
    finally:
        cursor.close()
    
    return redirect(url_for('admin_testimonials'))

@app.route('/admin/testimonials/<int:testimonial_id>/delete', methods=['POST'])
@admin_required
def delete_testimonial(testimonial_id):
    cursor = db_api.connection.cursor()
    
    try:
        cursor.execute("DELETE FROM testimonials WHERE testimonial_id = %s", (testimonial_id,))
        db_api.connection.commit()
        flash('Testimonial deleted successfully!', 'success')
    except Exception as e:
        db_api.connection.rollback()
        flash('Error deleting testimonial!', 'error')
    finally:
        cursor.close()
    
    return redirect(url_for('admin_testimonials'))

# View testimonial details
@app.route('/admin/testimonials/<int:testimonial_id>/view')
@admin_required
def view_testimonial(testimonial_id):
    try:
        cursor = db_api.connection.cursor()
        
        cursor.execute("""
            SELECT t.*, t.content as testimonial, s.full_name, s.department, s.year_level, e.title as event_title, e.event_date
            FROM testimonials t
            JOIN students s ON t.student_id = s.student_id
            LEFT JOIN events e ON t.event_id = e.event_id
            WHERE t.testimonial_id = %s
        """, (testimonial_id,))
        
        testimonial = cursor.fetchone()
        
        if not testimonial:
            flash('Testimonial not found!', 'error')
            return redirect(url_for('admin_testimonials'))
        
        cursor.close()
        return render_template('admin/view_testimonial.html', testimonial=testimonial)
        
    except Exception as e:
        print(f"Error in view_testimonial: {e}")
        flash('Error loading testimonial!', 'error')
        return redirect(url_for('admin_testimonials'))
    finally:
        if 'cursor' in locals():
            cursor.close()

# Edit testimonial
@app.route('/admin/testimonials/<int:testimonial_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_testimonial(testimonial_id):
    try:
        cursor = db_api.connection.cursor()
        
        if request.method == 'POST':
            try:
                rating = request.form.get('rating')
                testimonial_text = request.form.get('testimonial')
                status = request.form.get('status')
                
                cursor.execute("""
                    UPDATE testimonials 
                    SET rating = %s, content = %s, status = %s
                    WHERE testimonial_id = %s
                """, (rating, testimonial_text, status, testimonial_id))
                
                db_api.connection.commit()
                flash('Testimonial updated successfully!', 'success')
                return redirect(url_for('admin_testimonials'))
                
            except Exception as e:
                db_api.connection.rollback()
                print(f"Error updating testimonial: {e}")
                flash('Error updating testimonial!', 'error')
            finally:
                cursor.close()
        
        # GET request - show edit form
        try:
            cursor.execute("""
                SELECT t.*, t.content as testimonial, s.full_name, s.department, s.year_level, e.title as event_title, e.event_date
                FROM testimonials t
                JOIN students s ON t.student_id = s.student_id
                LEFT JOIN events e ON t.event_id = e.event_id
                WHERE t.testimonial_id = %s
            """, (testimonial_id,))
            
            testimonial = cursor.fetchone()
            
            if not testimonial:
                flash('Testimonial not found!', 'error')
                return redirect(url_for('admin_testimonials'))
            
            cursor.close()
            return render_template('admin/edit_testimonial.html', testimonial=testimonial)
            
        except Exception as e:
            print(f"Error loading testimonial: {e}")
            flash('Error loading testimonial!', 'error')
            return redirect(url_for('admin_testimonials'))
        finally:
            if 'cursor' in locals():
                cursor.close()
                
    except Exception as e:
        print(f"Error in edit_testimonial: {e}")
        flash('Database connection error!', 'error')
        return redirect(url_for('admin_testimonials'))

# Approve all pending testimonials
@app.route('/admin/testimonials/approve-all', methods=['POST'])
@admin_required
def approve_all_testimonials():
    cursor = db_api.connection.cursor()
    
    try:
        cursor.execute("""
            UPDATE testimonials 
            SET status = 'approved' 
            WHERE status = 'pending'
        """)
        
        affected_rows = cursor.rowcount
        db_api.connection.commit()
        flash(f'Successfully approved {affected_rows} testimonials!', 'success')
    except Exception as e:
        db_api.connection.rollback()
        flash('Error approving testimonials!', 'error')
    finally:
        cursor.close()
    
    return redirect(url_for('admin_testimonials'))

# API endpoint for testimonial data (for modal)
@app.route('/admin/testimonials/<int:testimonial_id>/data')
@admin_required
def get_testimonial_data(testimonial_id):
    try:
        cursor = db_api.connection.cursor()
        
        print(f"Fetching testimonial ID: {testimonial_id}")  # Debug
        
        # First, get the basic testimonial data
        cursor.execute("SELECT * FROM testimonials WHERE testimonial_id = %s", (testimonial_id,))
        testimonial = cursor.fetchone()
        print(f"Testimonial found: {testimonial is not None}")  # Debug
        
        if not testimonial:
            cursor.close()
            return jsonify({'error': 'Testimonial not found'}), 404
        
        # Get student data - handle missing student gracefully
        student = None
        try:
            cursor.execute("SELECT full_name, department, year_level FROM students WHERE student_id = %s", (testimonial['student_id'],))
            student = cursor.fetchone()
            print(f"Student data: {student}")  # Debug
        except Exception as e:
            print(f"Student query error: {e}")  # Debug
            student = None
        
        # Get event data if event_id exists
        event_data = None
        if testimonial.get('event_id'):  # event_id column
            try:
                cursor.execute("SELECT title, event_date FROM events WHERE event_id = %s", (testimonial['event_id'],))
                event_data = cursor.fetchone()
                print(f"Event data: {event_data}")  # Debug
            except Exception as e:
                print(f"Event query error: {e}")  # Debug
        
        # Build response data with safe null handling
        testimonial_dict = {
            'testimonial_id': testimonial['testimonial_id'],
            'student_id': testimonial['student_id'],
            'testimonial': testimonial['content'],  # Note: column name is 'content' in DB
            'rating': testimonial['rating'],
            'created_at': testimonial['created_at'],
            'status': testimonial['status'],
            'event_id': testimonial.get('event_id'),
            'full_name': student['full_name'] if student and student.get('full_name') else f'Student #{testimonial["student_id"]}',
            'department': student['department'] if student and student.get('department') else 'Unknown Department',
            'year_level': student['year_level'] if student and student.get('year_level') else 'Unknown Year',
            'event_title': event_data['title'] if event_data and event_data.get('title') else 'General Feedback',
            'event_date': event_data['event_date'] if event_data and event_data.get('event_date') else None
        }
        
        # Format dates safely
        try:
            if testimonial_dict.get('created_at'):
                testimonial_dict['created_at'] = testimonial_dict['created_at'].strftime('%B %d, %Y at %I:%M %p')
            if testimonial_dict.get('event_date'):
                testimonial_dict['event_date'] = testimonial_dict['event_date'].strftime('%B %d, %Y')
        except Exception as date_error:
            print(f"Date formatting error: {date_error}")
            # If date formatting fails, use string representation
            pass
        
        cursor.close()
        return jsonify(testimonial_dict)
        
    except Exception as e:
        print(f"Error in get_testimonial_data: {e}")  # Debug print
        return jsonify({'error': f'Database error: {str(e)}'}), 500

# Messages Management
@app.route('/admin/messages')
@admin_required
def admin_messages():
    cursor = db_api.connection.cursor()
    
    # Get all contact messages
    cursor.execute("""
        SELECT * FROM contacts
        ORDER BY submitted_at DESC
    """)
    messages = cursor.fetchall()
    
    cursor.close()
    return render_template('admin/messages.html', messages=messages)

@app.route('/admin/messages/<int:message_id>/data')
@admin_required
def get_message_data(message_id):
    try:
        cursor = db_api.connection.cursor()
        
        cursor.execute("""
            SELECT contact_id, name, email, subject, message, submitted_at, status
            FROM contacts 
            WHERE contact_id = %s
        """, (message_id,))
        
        message = cursor.fetchone()
        cursor.close()
        
        if not message:
            return jsonify({'success': False, 'error': 'Message not found'}), 404
            
        # Format the date for display
        formatted_message = dict(message)
        formatted_message['submitted_at'] = message['submitted_at'].strftime('%B %d, %Y at %I:%M %p')
        
        return jsonify({'success': True, 'data': formatted_message})
        
    except Exception as e:
        print(f"Error in get_message_data: {e}")
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500

# Database connection test route
@app.route('/admin/test-db')
@admin_required
def test_database():
    try:
        # Test database connection
        if not db_api.connection.conn or not db_api.connection.conn.is_connected():
            print("Database connection lost, attempting to reconnect...")
            db_api.connection.connect()
        
        cursor = db_api.connection.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM contacts")
        result = cursor.fetchone()
        cursor.close()
        
        return jsonify({
            'success': True, 
            'message': 'Database connection successful',
            'contact_count': result['count'] if result else 0
        })
    except Exception as e:
        print(f"Database test failed: {e}")
        return jsonify({'success': False, 'error': f'Database connection failed: {str(e)}'}), 500

# Message Operations
@app.route('/admin/messages/view/<int:message_id>')
@admin_required
def view_message(message_id):
    try:
        cursor = db_api.connection.cursor()
        
        cursor.execute("""
            SELECT contact_id, name, email, subject, message, status, submitted_at 
            FROM contacts WHERE contact_id = %s
        """, (message_id,))
        message = cursor.fetchone()
        
        cursor.close()
        
        if message:
            return jsonify({
                'success': True,
                'message': {
                    'id': message['contact_id'],
                    'name': message['name'],
                    'email': message['email'],
                    'subject': message['subject'],
                    'message': message['message'],
                    'status': message['status'],
                    'submitted_at': message['submitted_at'].strftime('%Y-%m-%d %H:%M:%S') if message['submitted_at'] else 'Unknown'
                }
            })
        else:
            return jsonify({'success': False, 'error': 'Message not found'}), 404
    except Exception as e:
        print(f"Error in view_message: {e}")
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500

@app.route('/admin/messages/mark-read/<int:message_id>', methods=['POST'])
@admin_required
def mark_message_read(message_id):
    try:
        cursor = db_api.connection.cursor()
        
        cursor.execute("""
            UPDATE contacts SET status = 'read' WHERE contact_id = %s
        """, (message_id,))
        db_api.connection.commit()
        cursor.close()
        
        return jsonify({'success': True, 'message': 'Message marked as read'})
    except Exception as e:
        print(f"Error in mark_message_read: {e}")
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500

@app.route('/admin/messages/mark-all-read', methods=['POST'])
@admin_required
def mark_all_messages_read():
    try:
        cursor = db_api.connection.cursor()
        
        cursor.execute("""
            UPDATE contacts SET status = 'read' WHERE status = 'unread'
        """)
        db_api.connection.commit()
        cursor.close()
        
        return jsonify({'success': True, 'message': 'All messages marked as read'})
    except Exception as e:
        print(f"Error in mark_all_messages_read: {e}")
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500

@app.route('/admin/messages/reply/<int:message_id>', methods=['POST'])
@admin_required
def reply_to_message(message_id):
    try:
        data = request.get_json()
        reply_message = data.get('reply_message', '')
        
        if not reply_message:
            return jsonify({'success': False, 'error': 'Reply message is required'}), 400
        
        cursor = db_api.connection.cursor()
        
        # Get the original message
        cursor.execute("""
            SELECT name, email, subject FROM contacts WHERE contact_id = %s
        """, (message_id,))
        original_message = cursor.fetchone()
        
        if not original_message:
            cursor.close()
            return jsonify({'success': False, 'error': 'Original message not found'}), 404
        
        # Insert the reply into the replies table
        cursor.execute("""
            INSERT INTO replies (contact_id, admin_user_id, reply_message)
            VALUES (%s, %s, %s)
        """, (message_id, session['user_id'], reply_message))
        
        # Update status to replied
        cursor.execute("""
            UPDATE contacts SET status = 'replied' WHERE contact_id = %s
        """, (message_id,))
        
        db_api.connection.commit()
        cursor.close()
        
        print(f"Reply saved to database - To: {original_message['email']} ({original_message['name']}): {reply_message}")
        
        return jsonify({'success': True, 'message': 'Reply sent successfully'})
    except Exception as e:
        print(f"Error in reply_to_message: {e}")
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500

@app.route('/admin/messages/delete/<int:message_id>', methods=['POST'])
@admin_required
def delete_message(message_id):
    try:
        cursor = db_api.connection.cursor()
        
        cursor.execute("""
            DELETE FROM contacts WHERE contact_id = %s
        """, (message_id,))
        db_api.connection.commit()
        cursor.close()
        
        return jsonify({'success': True, 'message': 'Message deleted successfully'})
    except Exception as e:
        print(f"Error in delete_message: {e}")
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500

@app.route('/admin/messages/export')
@admin_required
def export_messages():
    try:
        cursor = db_api.connection.cursor()
        
        cursor.execute("""
            SELECT contact_id, name, email, subject, message, status, submitted_at 
            FROM contacts ORDER BY submitted_at DESC
        """)
        messages = cursor.fetchall()
        cursor.close()
        
        # Create CSV content
        csv_content = "ID,Name,Email,Subject,Message,Status,Submitted At\n"
        for message in messages:
            # Escape commas and quotes in CSV
            def escape_csv_field(field):
                if field is None:
                    return ""
                field_str = str(field)
                if ',' in field_str or '"' in field_str or '\n' in field_str:
                    return '"' + field_str.replace('"', '""') + '"'
                return field_str
            
            # Access dictionary keys instead of tuple indices
            csv_content += f"{message['contact_id']},{escape_csv_field(message['name'])},{escape_csv_field(message['email'])},{escape_csv_field(message['subject'])},{escape_csv_field(message['message'])},{message['status']},{message['submitted_at']}\n"
        
        # Create response
        response = make_response(csv_content)
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = f'attachment; filename=messages_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        return response
    except Exception as e:
        print(f"Error in export_messages: {e}")
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500


# Enhanced Events CRUD operations
@app.route('/admin/events/<int:event_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_event(event_id):
    cursor = db_api.connection.cursor()
    
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        event_date = request.form['event_date']
        event_time = request.form['event_time']
        venue = request.form['venue']
        registration_deadline = request.form['registration_deadline']
        capacity = request.form['capacity'] or None
        category = request.form['category']
        
        cursor.execute("""
            UPDATE events 
            SET title=%s, description=%s, event_date=%s, event_time=%s, venue=%s,
                registration_deadline=%s, capacity=%s, category=%s
            WHERE event_id=%s
        """, (title, description, event_date, event_time, venue, registration_deadline, 
              capacity, category, event_id))
        
        db_api.connection.commit()
        cursor.close()
        
        flash('Event updated successfully!', 'success')
        return redirect(url_for('admin_events'))
    
    # Get event details for editing
    cursor.execute("SELECT * FROM events WHERE event_id = %s", (event_id,))
    event = cursor.fetchone()
    cursor.close()
    
    if not event:
        flash('Event not found!', 'error')
        return redirect(url_for('admin_events'))
    
    return render_template('admin/edit_event.html', event=event)

@app.route('/admin/events/<int:event_id>/delete', methods=['POST'])
@admin_required
def delete_event(event_id):
    cursor = db_api.connection.cursor()
    
    try:
        # Check if event has registrations
        cursor.execute("SELECT COUNT(*) as count FROM registrations WHERE event_id = %s", (event_id,))
        registration_count = cursor.fetchone()['count']
        
        if registration_count > 0:
            flash('Cannot delete event with existing registrations!', 'error')
            return redirect(url_for('admin_events'))
        
        # Delete the event
        cursor.execute("DELETE FROM events WHERE event_id = %s", (event_id,))
        db_api.connection.commit()
        
        flash('Event deleted successfully!', 'success')
    except Exception as e:
        db_api.connection.rollback()
        flash('Error deleting event!', 'error')
    finally:
        cursor.close()
    
    return redirect(url_for('admin_events'))

# View Event Registrations
@app.route('/admin/events/<int:event_id>/registrations')
@admin_required
def view_event_registrations(event_id):
    cursor = db_api.connection.cursor()
    
    # Get event details
    cursor.execute("SELECT * FROM events WHERE event_id = %s", (event_id,))
    event = cursor.fetchone()
    
    if not event:
        flash('Event not found!', 'error')
        return redirect(url_for('admin_events'))
    
    # Get all registrations for this event with student details
    cursor.execute("""
        SELECT r.*, s.full_name, s.student_number, s.department, s.year_level, u.email
        FROM registrations r
        JOIN students s ON r.student_id = s.student_id
        JOIN users u ON s.user_id = u.user_id
        WHERE r.event_id = %s
        ORDER BY r.registered_at DESC
    """, (event_id,))
    registrations = cursor.fetchall()
    
    # Get registration statistics
    cursor.execute("""
        SELECT 
            COUNT(*) as total_registrations,
            COUNT(CASE WHEN status = 'registered' THEN 1 END) as confirmed_registrations,
            COUNT(CASE WHEN status = 'waitlist' THEN 1 END) as waitlist_registrations,
            COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled_registrations
        FROM registrations 
        WHERE event_id = %s
    """, (event_id,))
    stats = cursor.fetchone()
    
    cursor.close()
    return render_template('admin/event_registrations.html', 
                         event=event, 
                         registrations=registrations, 
                         stats=stats)

# Registration Management Routes
@app.route('/admin/registrations/<int:registration_id>/status', methods=['POST'])
@admin_required
def update_registration_status(registration_id):
    try:
        data = request.get_json()
        new_status = data.get('status')
        
        if new_status not in ['registered', 'waitlist', 'cancelled']:
            return jsonify({'success': False, 'error': 'Invalid status'}), 400
        
        cursor = db_api.connection.cursor()
        
        # Update registration status
        cursor.execute("""
            UPDATE registrations 
            SET status = %s 
            WHERE registration_id = %s
        """, (new_status, registration_id))
        
        if cursor.rowcount == 0:
            cursor.close()
            return jsonify({'success': False, 'error': 'Registration not found'}), 404
        
        db_api.connection.commit()
        cursor.close()
        
        return jsonify({'success': True, 'message': f'Registration status updated to {new_status}'})
    except Exception as e:
        print(f"Error in update_registration_status: {e}")
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500

@app.route('/admin/registrations/<int:registration_id>/delete', methods=['POST'])
@admin_required
def delete_registration(registration_id):
    try:
        cursor = db_api.connection.cursor()
        
        # Delete registration
        cursor.execute("DELETE FROM registrations WHERE registration_id = %s", (registration_id,))
        
        if cursor.rowcount == 0:
            cursor.close()
            return jsonify({'success': False, 'error': 'Registration not found'}), 404
        
        db_api.connection.commit()
        cursor.close()
        
        return jsonify({'success': True, 'message': 'Registration deleted successfully'})
    except Exception as e:
        print(f"Error in delete_registration: {e}")
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500

@app.route('/admin/events/<int:event_id>/export')
@admin_required
def export_event_registrations(event_id):
    try:
        cursor = db_api.connection.cursor()
        
        # Get event details
        cursor.execute("SELECT * FROM events WHERE event_id = %s", (event_id,))
        event = cursor.fetchone()
        
        if not event:
            flash('Event not found!', 'error')
            return redirect(url_for('admin_events'))
        
        # Get all registrations for this event with student details
        cursor.execute("""
            SELECT r.*, s.full_name, s.student_number, s.department, s.year_level, u.email
            FROM registrations r
            JOIN students s ON r.student_id = s.student_id
            JOIN users u ON s.user_id = u.user_id
            WHERE r.event_id = %s
            ORDER BY r.registered_at DESC
        """, (event_id,))
        registrations = cursor.fetchall()
        
        cursor.close()
        
        # Create CSV content
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['Student Name', 'Student Number', 'Department', 'Year Level', 'Email', 'Status', 'Registered At'])
        
        # Write data
        for reg in registrations:
            writer.writerow([
                reg['full_name'],
                reg['student_number'],
                reg['department'],
                reg['year_level'],
                reg['email'],
                reg['status'],
                reg['registered_at'].strftime('%Y-%m-%d %H:%M:%S') if reg['registered_at'] else ''
            ])
        
        # Prepare response
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = f'attachment; filename=event_{event_id}_registrations.csv'
        
        return response
        
    except Exception as e:
        print(f"Error in export_event_registrations: {e}")
        flash('Error exporting registrations!', 'error')
        return redirect(url_for('admin_events'))

# Student Management Routes
@app.route('/admin/students/export')
@admin_required
def export_students():
    import csv
    import io
    from flask import make_response
    
    cursor = db_api.connection.cursor()
    
    # Get all students with registration counts
    cursor.execute("""
        SELECT s.*, u.email, u.role, u.created_at,
               COUNT(r.registration_id) as registration_count,
               'active' as status
        FROM students s
        JOIN users u ON s.user_id = u.user_id
        LEFT JOIN registrations r ON s.student_id = r.student_id AND r.status = 'registered'
        GROUP BY s.student_id
        ORDER BY u.created_at DESC
    """)
    students = cursor.fetchall()
    cursor.close()
    
    # Create CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Student ID', 'Full Name', 'Student Number', 'Email', 'Department', 
                    'Year Level', 'Phone', 'Registration Count', 'Joined Date', 'Status'])
    
    # Write data
    for student in students:
        writer.writerow([
            student['student_id'],
            student['full_name'],
            student['student_number'],
            student['email'],
            student['department'],
            student['year_level'],
            student['phone'],
            student['registration_count'],
            student['created_at'].strftime('%Y-%m-%d %H:%M:%S'),
            student['status']
        ])
    
    # Create response
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=students_export.csv'
    
    return response

@app.route('/admin/students/<int:student_id>')
@admin_required
def view_student(student_id):
    cursor = db_api.connection.cursor()
    
    # Get student details
    cursor.execute("""
        SELECT s.*, u.email, u.role, u.created_at
        FROM students s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.student_id = %s
    """, (student_id,))
    student = cursor.fetchone()
    
    if not student:
        flash('Student not found!', 'error')
        return redirect(url_for('admin_students'))
    
    # Get student's registrations
    cursor.execute("""
        SELECT r.*, e.title, e.event_date, e.venue
        FROM registrations r
        JOIN events e ON r.event_id = e.event_id
        WHERE r.student_id = %s
        ORDER BY r.registered_at DESC
    """, (student_id,))
    registrations = cursor.fetchall()
    
    cursor.close()
    return render_template('admin/view_student.html', student=student, registrations=registrations)

@app.route('/admin/students/<int:student_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_student(student_id):
    cursor = db_api.connection.cursor()
    
    if request.method == 'POST':
        full_name = request.form['full_name']
        student_number = request.form['student_number']
        department = request.form['department']
        phone = request.form['phone']
        year_level = request.form['year_level']
        email = request.form['email']
        
        try:
            # Update student info
            cursor.execute("""
                UPDATE students 
                SET full_name=%s, student_number=%s, department=%s, phone=%s, year_level=%s
                WHERE student_id=%s
            """, (full_name, student_number, department, phone, year_level, student_id))
            
            # Update user email
            cursor.execute("""
                UPDATE users u
                JOIN students s ON u.user_id = s.user_id
                SET u.email = %s
                WHERE s.student_id = %s
            """, (email, student_id))
            
            db_api.connection.commit()
            flash('Student updated successfully!', 'success')
            return redirect(url_for('admin_students'))
            
        except Exception as e:
            db_api.connection.rollback()
            flash('Error updating student!', 'error')
        finally:
            cursor.close()
    
    # Get student details for editing
    cursor.execute("""
        SELECT s.*, u.email
        FROM students s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.student_id = %s
    """, (student_id,))
    student = cursor.fetchone()
    cursor.close()
    
    if not student:
        flash('Student not found!', 'error')
        return redirect(url_for('admin_students'))
    
    return render_template('admin/edit_student.html', student=student)

@app.route('/admin/students/<int:student_id>/registrations')
@admin_required
def view_student_registrations(student_id):
    cursor = db_api.connection.cursor()
    
    # Get student details
    cursor.execute("""
        SELECT s.*, u.email
        FROM students s
        JOIN users u ON s.user_id = u.user_id
        WHERE s.student_id = %s
    """, (student_id,))
    student = cursor.fetchone()
    
    if not student:
        flash('Student not found!', 'error')
        return redirect(url_for('admin_students'))
    
    # Get student's registrations
    cursor.execute("""
        SELECT r.*, e.title, e.event_date, e.venue, e.category
        FROM registrations r
        JOIN events e ON r.event_id = e.event_id
        WHERE r.student_id = %s
        ORDER BY r.registered_at DESC
    """, (student_id,))
    registrations = cursor.fetchall()
    
    # Get registration statistics
    cursor.execute("""
        SELECT 
            COUNT(*) as total_registrations,
            COUNT(CASE WHEN status = 'registered' THEN 1 END) as confirmed_registrations,
            COUNT(CASE WHEN status = 'waitlist' THEN 1 END) as waitlist_registrations,
            COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled_registrations
        FROM registrations 
        WHERE student_id = %s
    """, (student_id,))
    stats = cursor.fetchone()
    
    cursor.close()
    return render_template('admin/student_registrations.html', 
                         student=student, 
                         registrations=registrations, 
                         stats=stats)

@app.route('/admin/students/<int:student_id>/delete', methods=['POST'])
@admin_required
def delete_student(student_id):
    cursor = db_api.connection.cursor()
    
    try:
        # Check if student has registrations
        cursor.execute("SELECT COUNT(*) as count FROM registrations WHERE student_id = %s", (student_id,))
        registration_count = cursor.fetchone()['count']
        
        if registration_count > 0:
            flash('Cannot delete student with existing registrations!', 'error')
            return redirect(url_for('admin_students'))
        
        # Get user_id for deletion
        cursor.execute("SELECT user_id FROM students WHERE student_id = %s", (student_id,))
        student = cursor.fetchone()
        
        if not student:
            flash('Student not found!', 'error')
            return redirect(url_for('admin_students'))
        
        # Delete student record
        cursor.execute("DELETE FROM students WHERE student_id = %s", (student_id,))
        
        # Delete user record
        cursor.execute("DELETE FROM users WHERE user_id = %s", (student['user_id'],))
        
        db_api.connection.commit()
        flash('Student deleted successfully!', 'success')
        
    except Exception as e:
        db_api.connection.rollback()
        flash('Error deleting student!', 'error')
    finally:
        cursor.close()
    
    return redirect(url_for('admin_students'))

if __name__ == '__main__':
    # Create upload directories if they don't exist
    os.makedirs('uploads/events', exist_ok=True)
    os.makedirs('uploads/gallery', exist_ok=True)
    
    app.run(debug=True, host='0.0.0.0', port=5000)