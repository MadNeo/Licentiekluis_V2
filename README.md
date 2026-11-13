# LicentieKluis — ontwikkelversie 0.1.0

LicentieKluis is bedoeld voor persoonlijk en intern gebruik. Er is geen openbare zelfregistratie, prijspagina of commerciële aanmeldstroom; accounts worden lokaal door een beheerder aangemaakt.

**Niet geschikt voor productie of echte licenties.** Dit is een gedeeltelijk werkende ontwikkelbasis; de bijgevoegde specificatie is nog niet volledig geïmplementeerd. Geen installatie op een netwerk of in een productie-LXC uitvoeren.

## Lokaal starten

Vereist: Python 3.12, Node.js 20 of hoger. Installeer de afhankelijkheden met `python3 -m venv .venv`, `.venv/bin/pip install -r requirements.txt` en `npm ci --prefix frontend`.

Genereer lokaal een eigen sleutel en bewaar deze veilig buiten de repository:

```bash
export LICENTIEKLUIS_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export COOKIE_SECURE=false
cd backend
../.venv/bin/python cli.py init
../.venv/bin/python cli.py account --username beheerder --role admin
../.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

Bewaar dezelfde sleutel voor volgende starts; zonder die sleutel zijn opgeslagen productsleutels niet te herstellen. `COOKIE_SECURE=false` is uitsluitend voor lokale ontwikkeling via HTTP. Start in een tweede terminal `npm run dev --prefix frontend` en open de getoonde lokale URL. Een gebruikersaccount kan lokaal worden aangemaakt met `cli.py account --username NAAM --role user --person-id ID` nadat een persoon is toegevoegd.

SQLite is uitsluitend een ontwikkelstandaard. Voor PostgreSQL kan `DATABASE_URL` worden ingesteld; de configuratie en concurrentie zijn nog niet getest met PostgreSQL. Toewijzingen worden met een rijvergrendeling uitgevoerd waar de database dat ondersteunt; SQLite biedt deze garantie niet.

## Aanwezig

Lokale sessie met HttpOnly-cookie en CSRF-token, Argon2-wachtwoordhash, gescheiden accounts en personen, licenties en persoonsgebonden toewijzingen, versleutelde productsleutels met Fernet, en een eenvoudige Nederlandstalige interface. Sleutels worden alleen expliciet opgehaald en na 15 seconden verborgen. Dit is geen bewijs van licentieactivatie of compliance.

## Ontbreekt vóór productie

Onder meer: TOTP, wachtwoordreset, rate limiting, accountbeheer in de interface, licenties wijzigen/archiveren, toewijzingen vrijgeven, apparaten, gedetailleerde rechten en auditinzage, bestanden, import/export, meldingen, migraties, back-up/herstel, veilige installatie en update, TLS/Nginx/systemd, beveiligingsheaders/CSP, volledige validatie, concurrentietest met PostgreSQL en uitgebreide acceptatietests. De database wordt nu met `create_all` aangemaakt, niet met migraties. Er is geen LXC-test uitgevoerd. De gekozen visuele richting is een beeldreferentie; er is nog geen bewerkbaar Figma-bestand. Railway en Website Builder zijn niet gebruikt voor een deployment of frontendexport.
