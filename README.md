# TurfPro — Turf Management System

## Setup (Windows)

```
cd turfpro2
python -m venv venv --without-pip
venv\Scripts\activate
python -m ensurepip
python -m pip install --upgrade pip
pip install -r requirements.txt
python manage.py makemigrations bookings
python manage.py migrate
python manage.py runserver
```

Open: http://127.0.0.1:8000

Register → Add Turf → Start Booking

## Setup (Mac/Linux)
```
cd turfpro2
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py makemigrations bookings
python manage.py migrate
python manage.py runserver
```

## Features
- Multi-user login (each owner sees only their data)
- Click-to-select time slot grid when creating bookings
- Editable price (auto-suggested from pricing rules)
- Per-booking activity log (every action recorded)
- PDF invoice download (no seed data — you create everything)
- Date range filter on every page (presets: today, week, month, custom)
- Light/Dark mode toggle
- Mobile bottom navigation bar
- Dynamic pricing rules (by day, hour, specific date)
- Analytics with charts
