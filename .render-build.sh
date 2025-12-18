#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r pc_app/server_requirements.txt

# Initialize the database
python -c "from pc_app.server import app, db; with app.app_context(): db.create_all()"

# No database migrations needed for this simple schema, 
# but if you had them (e.g., with Flask-Migrate), you would run them here:
# flask db upgrade