#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r pc_app/requirements.txt

# No database migrations needed for this simple schema, 
# but if you had them (e.g., with Flask-Migrate), you would run them here:
# flask db upgrade
