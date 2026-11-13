# PostgreSQL en gegevensintegriteit

Deze handleiding beschrijft de databasecomponent van de **ontwikkelversie**. Ze maakt de applicatie als geheel nog niet productierijp: de installatie in Proxmox, TLS, back-up/herstel en overige functies moeten nog worden afgemaakt en getest.

## Uitgangspunten

De doelversie is PostgreSQL 17 op Debian 13 (Trixie). De integratietests zijn uitgevoerd tegen PostgreSQL 17.6; Debian 13 en Proxmox LXC zelf zijn hierbij niet getest. PostgreSQL en de backend moeten voor een uiteindelijke installatie alleen via localhost bereikbaar zijn. Gebruik geen superuser voor de applicatie. De applicatierol is eigenaar van uitsluitend haar eigen database; daarmee mag die rol ook Alembic-migraties uitvoeren. Voor verdere beperking kun je later migratie- en runtime-rollen scheiden.

Voor lokaal ontwikkelen kan SQLite nog gebruikt worden. SQLite heeft andere vergrendelingssemantiek en bewijst niet dat gelijktijdige toewijzingen veilig zijn. De PostgreSQL-tests draaien expliciet tegen PostgreSQL.

## PostgreSQL voorbereiden

Voorbeeld voor een bestaande Debian-machine met PostgreSQL 17. Voer de systeemcommando's uit als een bevoegde beheerder; controleer de configuratie van je omgeving vooraf. De volledige installatie binnen LXC is nog niet geautomatiseerd.

```bash
sudo -u postgres createuser --no-superuser --no-createdb --no-createrole licentiekluis
sudo -u postgres createdb --owner=licentiekluis licentiekluis
sudo -u postgres psql -c '\password licentiekluis'
```

Voer een uniek, sterk databasewachtwoord interactief in. Deze commando's zijn voor een **nieuwe** rol en database; als ze al bestaan, controleer dan eerst eigenaar, rechten en inhoud. Configureer PostgreSQL om alleen op `127.0.0.1` te luisteren en localhostverbindingen met wachtwoord toe te staan, en controleer dit met je systeembeheerder. Publiceer PostgreSQL niet op het netwerk.

Gebruik bij voorkeur een beperkt leesbaar PostgreSQL-wachtwoordbestand in plaats van het wachtwoord in een URL, opdrachtregel of repository. Maak op de applicatiemachine een bestand buiten de repository met alleen voor de servicegebruiker leesrechten:

```bash
install -d -m 700 "$HOME/.config/licentiekluis"
umask 077
${EDITOR:-vi} "$HOME/.config/licentiekluis/pgpass"
chmod 600 "$HOME/.config/licentiekluis/pgpass"
export PGPASSFILE="$HOME/.config/licentiekluis/pgpass"
export DATABASE_URL='postgresql+psycopg://licentiekluis@127.0.0.1:5432/licentiekluis'
```

Zet één regel in het bestand in de vorm `127.0.0.1:5432:licentiekluis:licentiekluis:WACHTWOORD`. Vervang de laatste waarde zelf; escape een dubbele punt of backslash in het wachtwoord met een backslash, volgens de PostgreSQL-documentatie. Geef dit bestand nooit mee met de broncode. De variabelen moeten ook beschikbaar zijn voor de backend, het migratiecommando en de import; configureer dit uiteindelijk in de serviceomgeving, zonder geheimen in de repository.

## Nieuwe database en migraties

Installeer de Python-afhankelijkheden uit `requirements.txt`, stel `DATABASE_URL` en `PGPASSFILE` in en voer vanuit de repository uit:

```bash
cd backend
../.venv/bin/python cli.py init
```

Dit voert `alembic upgrade head` uit en maakt geen standaardaccount aan. De lokale ontwikkelinstaller voert dezelfde migratie uit voordat hij zo nodig de eerste beheerder vraagt. Je kunt vanuit de repository ook rechtstreeks `.venv/bin/alembic upgrade head` gebruiken. Bestaande, niet door Alembic beheerde tabellen worden door `cli.py init` geweigerd: er wordt niet automatisch gestempeld of overschreven.

Controleer bij toekomstige updates eerst de release-instructies en maak een consistente back-up. Een terugzetactie voor de applicatiecode draait een databasemigratie niet automatisch terug. De eerste migratie heeft bewust geen automatische downgrade die tabellen verwijdert.

## Bestaande SQLite-ontwikkelgegevens overzetten

1. Stop de oude backend en alle andere schrijvers. Maak met een geschikt SQLite-back-upproces een consistente kopie van de database, inclusief eventuele WAL-inhoud. Test die kopie en bewaar ook de oorspronkelijke encryptiesleutel afzonderlijk. Een volledige geautomatiseerde back-up- en herstelprocedure is nog niet geïmplementeerd.
   Een lokaal voorbeeld om een **nieuwe** consistente SQLite-kopie te maken (pas beide paden aan):

   ```bash
   .venv/bin/python - /pad/naar/bron.db /veilig/pad/naar/kopie.db <<'PYBACKUP'
   import os, sqlite3, sys
   from pathlib import Path

   source = Path(sys.argv[1]).resolve(strict=True)
   target = Path(sys.argv[2]).resolve()
   # O_EXCL voorkomt het overschrijven van een bestaand bestand.
   fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
   os.close(fd)
   try:
       with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
           with sqlite3.connect(target) as dst:
               src.backup(dst)
               if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                   raise RuntimeError('Integriteitscontrole mislukt')
   except BaseException:
       target.unlink(missing_ok=True)
       raise
   print('SQLite-kopie en integriteitscontrole voltooid.')
   PYBACKUP
   ```

   Bewaar de kopie en encryptiesleutel beschermd en afzonderlijk. Dit is uitsluitend een SQLite-bronkopie, geen volledig applicatieback-upsysteem.

2. Maak een **nieuwe, lege PostgreSQL-database** zoals hierboven. Stel `DATABASE_URL`, `PGPASSFILE` en de oorspronkelijke `LICENTIEKLUIS_KEY` in de terminal in. Gebruik dezelfde sleutel als bij de SQLite-bron: ook ingestelde TOTP-geheimen zijn daarmee versleuteld.
3. Voer de Alembic-migratie uit met `cd backend && ../.venv/bin/python cli.py init`, en ga terug naar de repositoryhoofdmap met `cd ..`. Maak vóór de import nog geen beheerder of andere gegevens aan in de doeldatabase.
4. Controleer dat bronbestand en doel correct zijn, en voer uit:

   ```bash
   .venv/bin/python scripts/import_sqlite.py --source /pad/naar/veilige-kopie.db --confirm
   ```

5. Controleer de aantallen die het script toont, meld je aan en controleer personen, licenties en toewijzingen. Bewaar de oorspronkelijke bron en back-up tot de controle is afgerond.

Het script opent de bron alleen-lezen en schrijft de gegevens in één transactie naar de lege doeldatabase. Het controleert versleutelde velden met de opgegeven sleutel, constraints en seatbezetting. Bij een fout worden de ingevoegde gegevens teruggedraaid. Bij een niet-lege doeldatabase wordt de import geweigerd; het script overschrijft nooit bestaande gegevens. IDs en herstelcodes blijven behouden; sessies, resetcodes, inlogpogingen en nog niet bevestigde TOTP-instellingen worden niet overgenomen. Alle gebruikers moeten opnieuw inloggen. Er worden geen sleutels of wachtwoorden in het resultaatrapport getoond.

Een oude SQLite-database wordt **niet** rechtstreeks door Alembic bijgewerkt of automatisch naar PostgreSQL omgezet. De vorige `migrate-auth`-procedure is vervangen door deze expliciete import.

## Integriteitsregels

De database controleert unieke gebruikersnamen, e-mailadressen en licentiereferenties, geldige rollen, positieve aantallen seats, verwijzingen en unieke actieve toewijzingen per licentie en persoon. De API vergrendelt in PostgreSQL de licentie en persoon binnen dezelfde transactie, controleert geldigheid en beschikbare seats en legt daarna de toewijzing vast. Alle toekomstige code die seats wijzigt of toewijzingen invoegt, moet dezelfde vergrendelingsvolgorde gebruiken. Rechtstreekse SQL-invoegingen omzeilen de API-seatcontrole en zijn niet bedoeld als beheerroute.

## Tests met PostgreSQL

Gebruik uitsluitend een afzonderlijke **wegwerpdatabase** waarvan de naam op `_test` eindigt. De opgegeven rol moet daarin tijdelijke schema's kunnen maken en verwijderen. De tests maken een willekeurig testschema en verwijderen dat na afloop. Zet nooit een productie-URL in deze variabele.

```bash
export LICENTIEKLUIS_TEST_DATABASE_URL='postgresql+psycopg://testrol@127.0.0.1:5432/licentiekluis_test'
.venv/bin/pytest -q
```

Configureer een passend `PGPASSFILE` voor de testrol. Zonder testdatabase worden de PostgreSQL-tests overgeslagen. De tests controleren migraties, databasebeperkingen, rollback, een gelijktijdige race om de laatste seat, import, verkeerde encryptiesleutel en herhaalde import. De tests vervangen geen LXC-installatie- of hersteltest.
