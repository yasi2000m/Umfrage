# Streamlit-Umfrage-App

Moderne Umfrageplattform mit öffentlicher Benutzeransicht und passwortgeschütztem Adminbereich.

## Funktionen

- Einfachauswahl, Mehrfachauswahl und freie Textantworten
- Umfragen erstellen, bearbeiten, aktivieren, pausieren und löschen
- Auswertung mit Diagrammen, Tabellen und einer Liste geschriebener Antworten
- responsive Benutzeroberfläche

## Lokal starten

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Das voreingestellte Admin-Passwort ist `7777`. Für die Veröffentlichung sollte es über Streamlit Secrets geändert werden:

```toml
ADMIN_PASSWORD = "dein-sicheres-passwort"
```

SQLite ist für lokale Tests und kleinere Demos geeignet. Für eine langfristige Nutzung auf Streamlit Community Cloud empfiehlt sich später eine externe PostgreSQL-Datenbank.
