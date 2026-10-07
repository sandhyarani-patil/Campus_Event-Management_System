# 🎓 Campus Event Management System (CEMS)

A comprehensive web-based application designed to streamline the organization, management, and participation in campus events. Built with Python and Flask, CEMS bridges the gap between students and event administrators by providing a seamless platform for event registration, tracking, and gallery showcases.



##  Features

### For Students / Users:
* **User Authentication:** Secure registration and login system.
* **Event Exploration:** Browse upcoming campus events, view details, and register seamlessly.
* **Profile Management:** Update personal information and view registered events.
* **Messaging & Support:** Send inquiries or feedback to administrators.
* **Testimonials:** Read and share experiences regarding past events.

###  For Administrators:
* **Admin Dashboard:** Centralized control panel to monitor platform activity.
* **Event Management:** Create, edit, and delete campus events easily.
* **Student Oversight:** Manage registered users and track event participation.
* **Gallery & Content Control:** Upload and manage event photos and highlights.

---

## Tech Stack

* **Backend:** Python, Flask
* **Database:** SQLite / MySQL (`init_db.py`)
* **Frontend:** HTML5, CSS3, JavaScript, Jinja2 Templates
* **Styling:** Responsive UI design with modern CSS assets



##Project Structure

CEMS-SHARE/
│
├── static/             # CSS stylesheets, JavaScript files, and images
├── templates/          # HTML templates (User & Admin views)
├── uploads/            # User-uploaded files and event media
├── .env                # Environment configuration file
├── app.py              # Main Flask application entry point
├── config.py           # Configuration settings
├── init_db.py          # Database initialization script
└── requirements.txt    # Project dependencies
