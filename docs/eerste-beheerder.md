# Eerste beheerder aanmaken

Deze instructie geldt voor de huidige **lokale ontwikkelversie** van LicentieKluis. De applicatie is nog niet geschikt voor echte licenties, productiegebruik of installatie op een netwerk. Er is nog geen geteste Proxmox-installatieprocedure.

De eerste beheerder wordt tijdens de lokale installatie via een terminal aangemaakt. Er is geen standaardwachtwoord en geen openbare installatiewizard. Het wachtwoord wordt interactief gevraagd en is tijdens het typen niet zichtbaar.

## Voorbereiding

- Python 3.12 en Node.js 20 of nieuwer zijn geïnstalleerd.
- De repository is lokaal beschikbaar en je kunt een terminal openen in de hoofdmap `Licentiekluis_V2`.
- Je hebt een authenticator-app die TOTP ondersteunt en een veilige plek voor de herstelcodes.
- Gebruik voor deze stappen uitsluitend een lokale ontwikkelomgeving met fictieve gegevens.

Alle onderstaande commando's gaan ervan uit dat de terminal in de hoofdmap van de repository staat. Start de lokale installatie:

```bash
./install.sh
```

Het script installeert de ontwikkelafhankelijkheden, initialiseert de database en vraagt bij de eerste installatie om een beheerdersnaam en wachtwoord. Voer het interactief uit; het wachtwoord wordt niet op het scherm getoond. Bij opnieuw uitvoeren blijft een bestaand beheerdersaccount ongewijzigd. Als de afhankelijkheden al zijn geïnstalleerd, kun je `./install.sh --zonder-afhankelijkheden` gebruiken.

## 1. Encryptiesleutel voorbereiden

LicentieKluis heeft een encryptiesleutel nodig voor onder meer de tweede factor. Maak deze bij de eerste installatie één keer aan **buiten de repository**. Het volgende voorbeeld slaat de sleutel op in een bestand dat alleen de huidige Linux-gebruiker kan lezen:

```bash
mkdir -p "$HOME/.config/licentiekluis"
chmod 700 "$HOME/.config/licentiekluis"
(umask 077; set -o noclobber; .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' > "$HOME/.config/licentiekluis/sleutel")
```

Als het bestand al bestaat, stopt het commando met een melding. **Overschrijf of vervang de bestaande sleutel niet**: anders kunnen bestaande versleutelde gegevens en TOTP-instellingen onleesbaar worden. Bewaar een veilige aparte kopie van de sleutel; een databaseback-up alleen is onvoldoende voor herstel.

Laad de bestaande sleutel in de huidige terminal:

```bash
export LICENTIEKLUIS_KEY="$(cat "$HOME/.config/licentiekluis/sleutel")"
```

## 2. Beheerder controleren

Tijdens `./install.sh` heb je de gebruikersnaam en een uniek wachtwoord van minimaal 12 tekens opgegeven en het wachtwoord herhaald. Er verschijnen geen tekens tijdens het typen; dat is normaal. Bij succes meldt het script dat de eerste beheerder is aangemaakt. De beheerder hoeft niet aan een persoon gekoppeld te zijn.

Het script gebruikt de database waarnaar de huidige configuratie verwijst. Als `DATABASE_URL` niet is ingesteld, wordt het SQLite-bestand `backend/licentiekluis.db` gebruikt. Als er al een beheerder bestaat, ook als die is gedeactiveerd, maakt het script geen nieuwe aan en laat het de bestaande accounts ongemoeid. Voor herstel gebruik je de lokale commando's onder **Bij problemen**.

## 3. Applicatie lokaal starten

Ga in dezelfde terminal naar de backendmap en start de server:

```bash
cd backend
export COOKIE_SECURE=false
../.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

`COOKIE_SECURE=false` is alleen bedoeld voor deze lokale HTTP-ontwikkelomgeving. Gebruik deze instelling niet voor productie of een netwerktoegankelijke installatie. Houd deze terminal open.

Open een **tweede terminal** in de hoofdmap van de repository en start de frontend:

```bash
npm run dev --prefix frontend
```

Open het lokale adres dat Vite in de terminal toont, meestal `http://127.0.0.1:5173/`. Houd ook deze terminal open. Open het adres alleen op de machine waarop de ontwikkelserver draait.

## 4. Tweede factor instellen

1. Log in met de gebruikersnaam en het wachtwoord uit stap 2.
2. Kies **Instellen** op het scherm voor tweefactorauthenticatie.
3. Voeg de getoonde geheime sleutel handmatig toe aan je authenticator-app. De app genereert een zescijferige TOTP-code.
4. Voer de actuele code in en kies **Bevestigen**.
5. Bewaar de tien getoonde herstelcodes op een veilige plaats. Elke code is één keer bruikbaar en de lijst wordt niet opnieuw getoond.
6. Kies **Ik heb de codes bewaard**. Het dashboard verschijnt.

Vanaf nu is bij het aanmelden naast het wachtwoord een authenticatorcode of een ongebruikte herstelcode nodig.

## Bij problemen

- **Account bestaat al:** gebruik een andere naam of herstel het bestaande account; maak geen tweede account aan als dat niet nodig is.
- **Wachtwoord vergeten:** voer vanuit de map `backend` lokaal `../.venv/bin/python cli.py reset-password --username beheerder` uit. Vervang de naam indien nodig. Alle sessies van dit account worden ingetrokken.
- **Authenticator en herstelcodes kwijt:** voer vanuit de map `backend` lokaal `../.venv/bin/python cli.py reset-mfa --username beheerder` uit. De bestaande tweede factor, herstelcodes en sessies worden ongeldig; stel TOTP opnieuw in bij de volgende aanmelding.
- **Encryptiesleutel kwijt:** herstel de oorspronkelijke sleutel uit een veilige kopie. Maak niet zomaar een nieuwe sleutel aan voor een bestaande database: daarmee kun je de eerder versleutelde gegevens niet ontsleutelen.
- **Bestaande oudere ontwikkelingsdatabase:** maak eerst een back-up en volg de instructie voor `migrate-auth` in de README. Draai geen migratie zonder de juiste database en sleutel te controleren.

De lokale herstelcommando's zijn alleen bedoeld voor bevoegde systeembeheerders. Beperk daarom de toegang tot de machine, de database en het sleutelbestand.
