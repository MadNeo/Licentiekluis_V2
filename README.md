# LicentieKluis — ontwikkelversie 0.1.0

LicentieKluis is bedoeld voor persoonlijk en intern gebruik. Er is geen openbare zelfregistratie, prijspagina of commerciële aanmeldstroom; accounts worden lokaal door een beheerder aangemaakt.

**Niet geschikt voor productie of echte licenties.** Dit is een gedeeltelijk werkende ontwikkelbasis; de bijgevoegde specificatie is nog niet volledig geïmplementeerd. Geen installatie op een netwerk of in een productie-LXC uitvoeren.

## Lokaal starten

Volg de [stapsgewijze instructie voor de eerste beheerder](docs/eerste-beheerder.md) voor afhankelijkheden, een blijvend opgeslagen encryptiesleutel, het aanmaken van het account en het instellen van TOTP. De instructie gebruikt uitsluitend lokale ontwikkelservers.

Een gebruikersaccount kan lokaal worden aangemaakt met `cli.py account --username NAAM --role user --person-id ID` nadat een persoon is toegevoegd.

PostgreSQL 17 is de doelomgeving; het schema wordt beheerd met Alembic. Lees [PostgreSQL en gegevensintegriteit](docs/postgresql.md) voor databaseconfiguratie, migraties, het overzetten van SQLite en tests. SQLite blijft uitsluitend beschikbaar voor lokale ontwikkeling en biedt niet dezelfde garantie bij gelijktijdige toewijzingen.

## Aanwezig

Lokale sessie met HttpOnly-cookie en CSRF-token, Argon2-wachtwoordhash, gescheiden accounts en personen, licenties en persoonsgebonden toewijzingen, versleutelde productsleutels met Fernet, en een eenvoudige Nederlandstalige interface. Sleutels worden alleen expliciet opgehaald en na 15 seconden verborgen. Dit is geen bewijs van licentieactivatie of compliance.

## Ontbreekt vóór productie

Onder meer: productievalidatie van authenticatie en SMTP, licenties wijzigen/archiveren, toewijzingen vrijgeven, apparaten, gedetailleerde rechten en auditinzage, bestanden, import/export, meldingen, volledige upgrade- en terugzetprocedures, back-up/herstel, veilige installatie en update, TLS/Nginx/systemd, beveiligingsheaders/CSP, volledige validatie en uitgebreide acceptatietests. Er is geen LXC-test uitgevoerd. De gekozen visuele richting is een beeldreferentie; er is nog geen bewerkbaar Figma-bestand. Railway en Website Builder zijn niet gebruikt voor een deployment of frontendexport.

## Accounts en beveiliging (ontwikkelversie)

Beheerders en licentiebeheerders moeten bij hun eerste aanmelding TOTP instellen. De sleutel wordt versleuteld opgeslagen met `LICENTIEKLUIS_KEY`. Bewaar de tien eenmalig getoonde herstelcodes op een veilige plaats. Gewone gebruikers kunnen via Beveiliging zelf TOTP inschakelen. Accounts worden uitsluitend door een beheerder of lokaal via de CLI aangemaakt. Een persoon kan zonder account bestaan.

| Functie | Beheerder | Licentiebeheerder | Gebruiker |
|---|---|---|---|
| Accounts en rollen beheren | Ja | Nee | Nee |
| Personen en licenties beheren | Ja | Ja | Nee |
| Licenties en sleutels zien | Alle | Alle | Alleen toegewezen |
| Eigen wachtwoord wijzigen | Ja | Ja | Ja |

Een rolwijziging, deactivering of wachtwoordwijziging trekt alle sessies van het betreffende account in. Bij verlies van wachtwoord of tweede factor kan de lokale systeembeheerder in de backendmap `../.venv/bin/python cli.py reset-password --username NAAM` of `../.venv/bin/python cli.py reset-mfa --username NAAM` uitvoeren. De commando's registreren de actie en trekken sessies in. Bij herstel van de tweede factor moet de betrokkene deze opnieuw instellen. Beperk de toegang tot de container en configuratie tot bevoegde beheerders.

Optioneel kan wachtwoordherstel via e-mail worden ingericht met `SMTP_HOST`, `SMTP_PORT` (standaard 587), `SMTP_FROM` en eventueel `SMTP_USER` en `SMTP_PASSWORD`. SMTP gebruikt STARTTLS. Het account moet gekoppeld zijn aan een actieve persoon met e-mailadres. De eenmalige code is 15 minuten geldig; de gebruiker voert die zelf in op de herstelpagina. Er staan geen codes in URL's. Zonder SMTP blijft de lokale herstelprocedure beschikbaar. Gebruik voor productie geen omgeving waarin onbevoegden de SMTP-instellingen of de encryptiesleutel kunnen lezen.

Voor een bestaande SQLite-database: maak eerst een back-up en volg de [expliciete overzetprocedure](docs/postgresql.md#bestaande-sqlite-ontwikkelgegevens-overzetten). De oude `migrate-auth`-opdracht is vervangen.
