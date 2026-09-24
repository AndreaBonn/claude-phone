# Telegram ⇄ Claude Code Bridge

Un bot Telegram che ti fa usare dal telefono il Claude Code installato sul tuo PC Linux. Quello che scrivi al bot arriva a Claude Code; quello che Claude Code fa e risponde torna in chat. Quando Claude vuole eseguire un comando o modificare un file, il bot ti chiede il permesso con dei bottoni.

Il bot non parte mai da solo: lo accendi tu con `./start.sh` quando vuoi essere raggiungibile e lo spegni con `./stop.sh`.

Progetto ispirato, per l'architettura, a [RichardAtCT/claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram), ma scritto da zero con un perimetro più ridotto.

## Requisiti

- Linux, Python 3.11 o successivo, [uv](https://docs.astral.sh/uv/)
- Claude Code installato e già autenticato: `claude auth status` deve rispondere senza errori
- Un account Telegram

## Setup iniziale (una volta sola)

### 1. Crea il bot su Telegram

1. Apri una chat con [@BotFather](https://t.me/botfather).
2. Invia `/newbot`, scegli un nome e uno username che finisca in `bot` (per esempio `mario_claudecode_bot`).
3. BotFather risponde con un token del tipo `123456789:AA...`: è il valore di `TELEGRAM_BOT_TOKEN`. Non condividerlo e non committarlo.
4. Scrivi a [@userinfobot](https://t.me/userinfobot): risponde con il tuo user ID numerico, che va in `ALLOWED_USERS`.
5. Consigliato: in BotFather invia `/setjoingroups`, scegli il bot e poi `Disable`, così nessuno può aggiungerlo a un gruppo.
6. Apri la chat con il tuo bot e premi **Avvia**: finché non lo fai, il bot non può scriverti per primo.

### 2. Configura il progetto

```bash
cp .env.example .env
```

Compila `.env`:

| Variabile | Cosa contiene |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Il token di BotFather |
| `ALLOWED_USERS` | Il tuo user ID Telegram (più ID separati da virgola) |
| `APPROVED_DIRECTORY` | Una o più cartelle radice separate da virgola. Ogni sotto-cartella diretta di una radice è un progetto, chiamato `<radice>/<cartella>` (per esempio `ProgettiPersonali/claude-phone`). Le radici devono avere nomi diversi e non essere una dentro l'altra. Se una radice contiene questo repository, il bridge viene escluso in automatico |
| `CLAUDE_ALLOWED_TOOLS` | Strumenti che Claude può usare (default `Read,Grep,Glob,Bash,Edit,Write`) |
| `CLAUDE_AUTO_APPROVE_TOOLS` | Strumenti approvati senza chiedere (default, sola lettura: `Read,Grep,Glob,LS`) |
| `CLAUDE_TIMEOUT_SECONDS` | Secondi di silenzio di Claude dopo cui il turno viene chiuso. L'attesa di una tua approvazione non conta |
| `APPROVAL_TIMEOUT_SECONDS` | Secondi per rispondere a una richiesta di approvazione, poi l'azione è negata |
| `VERBOSE_LEVEL` | Dettaglio di default del progresso: 0, 1 o 2 |
| `DB_PATH` | Database SQLite con sessioni e audit log |
| `CLAUDE_BIN` | Comando di Claude Code, se non è `claude` nel PATH |
| `CLAUDE_PROFILE` | Profilo Claude all'avvio, per esempio `sales`: una sotto-cartella di `CLAUDE_PROFILES_DIR`. Vuoto = `~/.claude` |
| `CLAUDE_PROFILES_DIR` | Cartella dei profili, di default `~/.cloak/profiles` (quella di cloak) |
| `ANTHROPIC_API_KEY` | Facoltativa: solo se vuoi la fatturazione API a consumo invece dell'abbonamento |

## Avvio e arresto

```bash
./start.sh                # in primo piano: Ctrl+C per fermare
./start.sh --background   # staccato dal terminale
./stop.sh                 # arresto pulito
```

`start.sh` controlla il login di Claude Code, rifiuta un secondo avvio se il bot è già acceso e stampa `Bot attivo, in ascolto` quando è pronto. All'accensione ricevi su Telegram `🟢 Bot online`, allo spegnimento `🔴 Bot disattivato`.

I messaggi che mandi mentre il bot è spento vengono scartati all'avvio: niente viene eseguito in ritardo.

### Profili (cloak)

Un profilo cloak è una cartella in `~/.cloak/profiles` con il suo login. Il bot avvia Claude Code con `CLAUDE_CONFIG_DIR` puntato al profilo scelto, senza passare da `cloak`. Da Telegram `/profile` mostra `default` e i profili trovati come bottoni; `/profile sales` cambia direttamente. La scelta resta salvata anche dopo un riavvio e prevale su `CLAUDE_PROFILE`. Ogni profilo ha le sue sessioni: tornando a un profilo riprendi la sua conversazione.

`claude auth status` risponde "loggato" anche quando il token è scaduto, quindi `start.sh` non se ne accorge. In quel caso il bot risponde con un errore 401 e ti dice cosa fare: sul PC apri Claude Code con quel profilo (`claude -a <nome>`, oppure `claude` per il default) e rifai `/login`.

### Systemd (facoltativo, solo avvio manuale)

In `systemd/telegram-claude-bridge.service` c'è una unit utente senza sezione `[Install]`: `systemctl --user enable` la rifiuta, quindi non può partire al login. Per usarla, sostituisci `/path/to/telegram-claude-bridge` con il percorso del repository, controlla il `PATH` (deve contenere `uv` e `claude`), poi:

```bash
systemctl --user link "$PWD/systemd/telegram-claude-bridge.service"
systemctl --user start telegram-claude-bridge
systemctl --user stop telegram-claude-bridge
```

## Uso da Telegram

| Comando | Effetto |
|---|---|
| `/start` | Benvenuto e lista dei progetti, con un bottone per ciascuno |
| `/projects` | Progetti a pagine da 20 con le frecce ⬅️ ➡️, quello attivo è segnato con ▶️ |
| `/switch <radice>/<nome>` | Cambia progetto. Se esiste una sessione salvata la riprende, altrimenti ne apre una nuova |
| `/new` | Chiude la sessione del progetto attivo e ne apre una pulita al prossimo messaggio |
| `/status` | Progetto, sessione, stato di Claude, verbosità, approvazioni in attesa |
| `/profile [nome]` | Profilo Claude (cloak) da usare: bottoni, oppure cambio diretto con il nome |
| `/verbose 0\|1\|2` | 0 solo la risposta finale, 1 strumenti usati in tempo reale, 2 strumenti con input completo |

Tutto il resto che scrivi va a Claude Code nel progetto attivo. Mentre Claude lavora vedi un messaggio `⏳ sto lavorando…` che si aggiorna; a fine turno la risposta arriva come messaggio nuovo, così il telefono ti avvisa. Se scrivi mentre Claude sta ancora lavorando, il messaggio viene messo in coda.

### Approvazioni

| Strumento | Comportamento |
|---|---|
| `Read`, `Grep`, `Glob`, `LS` | Approvati in automatico |
| `TodoWrite`, `Task`, `Agent`, `ExitPlanMode`, `Skill`, `ToolSearch` | Approvati in automatico: non toccano file né sistema, e gli strumenti usati dai sub-agent passano comunque dal gate |
| `Bash`, `Edit`, `Write` e qualunque altro strumento (strumenti MCP, `NotebookEdit`, `WebFetch`, …) | Messaggio con i bottoni `✅ Approva`, `❌ Nega`, `🚫 Nega e stop sessione` |
| Qualunque percorso fuori dalle radici di `APPROVED_DIRECTORY`, o dentro la cartella del bridge | Bloccato sempre, anche se approveresti |

Se non rispondi entro `APPROVAL_TIMEOUT_SECONDS` l'azione è negata. Se il bot si riavvia con richieste aperte, alla ripartenza le trovi marcate come annullate.

### Scelte con i bottoni

In modalità headless Claude Code non ha lo strumento `AskUserQuestion`. Il bridge insegna a Claude, con un prompt di sistema (`prompts/telegram-bridge-system-v1.md`), a chiudere il messaggio con righe `[[option: ...]]` quando deve farti scegliere: il bot le mostra come bottoni e ti basta toccarne uno. Puoi sempre rispondere anche a parole.

## Sicurezza

- **Whitelist**: ogni update Telegram viene controllato contro `ALLOWED_USERS` prima di qualsiasi handler.
- **Sandbox**: i percorsi di `Read`, `Grep`, `Glob`, `Edit`, `Write` e `NotebookEdit` vengono risolti, symlink compresi, e confrontati con le radici di `APPROVED_DIRECTORY`. La cartella del bridge è sempre esclusa, così Claude non può leggere né modificare il gate che lo controlla. Claude può lavorare in tutte le radici, non solo nel progetto attivo.
- **Bash**: il bridge cerca nei comandi i token che sembrano percorsi (`/…`, `~`, `..`) e blocca quelli fuori sandbox. È un controllo di superficie, non un sandbox vero: un comando può raggiungere file esterni in modi che una scansione dei token non vede. La protezione reale per `Bash` è la tua approvazione, quindi leggi il comando prima di premere ✅.
- **Gate fail-closed**: se il bot non risponde, se l'hook va in errore o se Telegram non è raggiungibile, l'azione è negata.
- **Segreti**: il token del bot non viene passato a Claude Code (che potrebbe leggerlo con `env`) e viene oscurato nei log.
- **Audit log**: ogni messaggio inviato a Claude e ogni decisione sugli strumenti finiscono nella tabella `audit_log` di `DB_PATH`.
- **Nessuna porta aperta**: il bot usa il long polling, solo connessioni in uscita.
- `--dangerously-skip-permissions` non compare in nessun percorso del codice.

- **Stessa configurazione del PC**: le sessioni caricano rule, `CLAUDE.md`, skill, hook, plugin e server MCP del profilo scelto e della cartella del progetto. Ogni chiamata a uno strumento MCP chiede l'approvazione; per quelli di sola consultazione (per esempio `mcp__context7__query-docs`) aggiungi il nome a `CLAUDE_AUTO_APPROVE_TOOLS`. Le cartelle `skills`, `rules`, `rules-detail`, `agents`, `commands` e `plugins` di `~/.claude` e dei profili sono leggibili ma non modificabili; credenziali e `settings.json` restano fuori.
- **File privati**: database, log, lock e socket vengono creati leggibili solo dal tuo utente.

Claude Code carica comunque le tue impostazioni utente (`~/.claude/settings.json`), hook compresi: valgono anche per le sessioni aperte dal bot.

## Test

```bash
uv run pytest
```

I test usano un finto binario `claude` (`tests/fake_claude.py`) che parla lo stesso protocollo stream-json, e fanno girare il vero hook contro il broker su un socket Unix.

Test end-to-end manuale, da fare con un bot vero:

1. `./start.sh` e attendi `🟢 Bot online` su Telegram.
2. `/start`, scegli un progetto, chiedi a Claude di leggere un file: nessun bottone, risposta in chat.
3. Chiedi di eseguire `ls` con Bash e premi ✅: il comando parte.
4. Chiedi di creare un file con Bash e premi ❌: il file non deve esistere.
5. `/switch` su un altro progetto e ritorno: la conversazione precedente riprende.
6. `./stop.sh` e attendi `🔴 Bot disattivato`.

## Struttura

```
src/
  bot.py                 avvio, cablaggio degli handler, ciclo di vita
  config.py              caricamento e validazione di .env
  auth.py                whitelist utenti
  claude_session.py      processo `claude -p` in stream-json, uno per progetto
  session_manager.py     sessioni per progetto, resume, persistenza dei session_id
  stream_parser.py       eventi stream-json di Claude Code
  permission_policy.py   regole auto / chiedi / blocca e controllo sandbox
  permission_gate.py     broker delle approvazioni su socket Unix
  permission_hook.py     hook PreToolUse lanciato da Claude Code (solo libreria standard)
  telegram_presenter.py  messaggi di approvazione con i bottoni
  turn_runner.py         un turno: progresso, risposta, bottoni di scelta
  message_formatter.py   righe dei tool, testo delle approvazioni, scelte
  telegram_text.py       split a 4096 caratteri e conversione Markdown → HTML
  telegram_io.py         invio con retry e backoff, messaggio di progresso
  project_manager.py     progetti e controllo dei percorsi
  session_store.py       SQLite: sessioni, stato utente, audit log, approvazioni aperte
  logging_setup.py       log su file a rotazione con oscuramento dei segreti
  handlers/              comandi, messaggi di testo, bottoni
prompts/                 prompt di sistema versionato
start.sh, stop.sh        avvio e arresto manuali
systemd/                 unit facoltativa, non abilitabile
```
