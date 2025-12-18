#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r pc_app/server_requirements.txt

# Set FLASK_APP environment variable
export FLASK_APP=pc_app/server.py

# Run database migrations
flask db upgrade