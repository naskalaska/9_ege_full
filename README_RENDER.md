# Deploy on Render

This folder contains the full web app, not the Streamlit backup.

Render settings:

- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `python app/server.py 0.0.0.0 $PORT`

The app uses only the Python standard library. User progress is stored in
`data/ege_app.db`; on Render free instances, local SQLite storage may be reset
when the service is redeployed or restarted.

Demo accounts:

- Teacher: `teacher` / `teacher123`
- Student: `student` / `student123`

Teacher code for demo students: `TEACHER-2026`
