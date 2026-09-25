[English](./SECURITY.md) | **Italiano**

# Policy di sicurezza

## Versioni supportate

Il progetto è in sviluppo attivo. Gli aggiornamenti di sicurezza vengono applicati all'ultimo commit su `main`; le release con tag (da `v0.1.0`) non ricevono fix retroattivi.

## Segnalare una vulnerabilità

Per segnalare una vulnerabilità usa [GitHub Security Advisories](https://github.com/AndreaBonn/claude-phone/security/advisories/new). Non aprire una issue pubblica.

Includi:

- Descrizione della vulnerabilità
- Passi per riprodurla
- Comportamento atteso e comportamento osservato
- Valutazione dell'impatto (cosa potrebbe ottenere un attaccante)

Tempi di risposta:

- Presa in carico: entro 72 ore
- Fix per i problemi critici: entro 30 giorni
- Divulgazione pubblica coordinata dopo il rilascio del fix

## Modello delle minacce

Il bridge permette a un telefono di pilotare un processo Claude Code che può eseguire comandi sul tuo PC. Ciò che protegge sono i file e la shell di quel PC. I due confini di fiducia sono la chat Telegram (solo gli utenti in whitelist possono parlare con il bot) e le chiamate agli strumenti fatte da Claude (ognuna passa dal gate di permessi).

## Misure di sicurezza implementate

- **Whitelist utenti**: ogni update Telegram viene confrontato con `ALLOWED_USERS` in un gruppo di handler che gira prima di tutti gli altri; gli utenti sconosciuti vengono scartati e registrati nel log (`src/auth.py:29`, `src/bot.py:176`).
- **Gate di permessi su ogni chiamata**: Claude Code lancia un hook `PreToolUse` con matcher `*` che interroga il broker delle approvazioni del bot; gli strumenti fuori dagli insiemi ad approvazione automatica richiedono sempre l'approvazione, strumenti MCP compresi (`src/permission_policy.py:87`).
- **Hook fail-closed**: qualsiasi errore nel raggiungere il broker nega la chiamata (`src/permission_hook.py:64`). Un'approvazione senza risposta viene negata dopo `APPROVAL_TIMEOUT_SECONDS` (`src/permission_gate.py:201`).
- **Sandbox dei percorsi**: i percorsi degli strumenti vengono risolti, symlink compresi, e confrontati con le radici di `APPROVED_DIRECTORY`; la cartella del bridge è sempre esclusa, così Claude non può leggere né modificare il gate che lo controlla. Una violazione della sandbox viene bloccata prima dell'approvazione automatica e prima di chiedere all'utente (`src/project_manager.py:30`, `src/permission_policy.py:110`).
- **File di runtime privati**: il processo imposta umask 077, il socket del gate ha permessi 0600 e il database SQLite 0600 (`src/bot.py:222`, `src/permission_gate.py:118`, `src/session_store.py:81`).
- **Segreti tenuti lontani da Claude**: `TELEGRAM_BOT_TOKEN` viene tolto dall'ambiente del processo figlio `claude`, che altrimenti potrebbe leggerlo con `env` (`src/claude_command.py:88`).
- **Oscuramento dei segreti nei log**: un formatter maschera il token del bot e la chiave API, traceback compresi (`src/logging_setup.py:17`). Entrambi sono conservati come `SecretStr` di pydantic (`src/config.py:31`).
- **Audit log**: ogni messaggio inviato a Claude e ogni decisione del gate vengono registrati nella tabella `audit_log` (`src/session_store.py:158`, `src/turn_runner.py:145`, `src/bot.py:117`).
- **Nessuna porta in ascolto**: il bot usa il long polling di Telegram, solo connessioni in uscita, e scarta gli update ricevuti mentre era spento (`src/bot.py:237`).
- **Nessun bypass dei permessi**: `--dangerously-skip-permissions` e `bypassPermissions` non compaiono nel codice.
- **Dipendenze bloccate**: `uv.lock` è versionato e `start.sh` installa con `uv sync --frozen`.
- **Integrazione continua**: ogni push e pull request esegue ruff, mypy, la suite di test con coverage e `pip-audit` sulle dipendenze runtime bloccate (`.github/workflows/ci.yml`).

## Limiti noti

- **Il controllo dei percorsi in Bash è best-effort**: il bridge cerca nei comandi di shell i token che sembrano percorsi (`/...`, `~`, `..`) e blocca quelli fuori sandbox (`src/permission_policy.py:64`). Un comando può comunque raggiungere file esterni in modi che una scansione dei token non vede. Per Bash la protezione reale è la tua approvazione: leggi il comando prima di approvarlo.
- **Autorizzazioni "approva sempre"**: quando autorizzi un intero strumento non-Bash per la sessione, le chiamate successive a quello strumento non ti vengono più mostrate fino a `/new`, a un cambio di profilo o a un riavvio. La sandbox resta attiva, e con lei un'eccezione: `Write`, `Edit`, `MultiEdit` e `NotebookEdit` sui file che Claude Code o git eseguono da soli (tutto sotto `.claude/` o `.git/`, `.mcp.json`, `.envrc`) chiedono sempre e non si possono mai autorizzare in blocco (`src/permission_policy.py:127`). L'eccezione non copre Bash: un comando che scrive uno di quei file con una redirezione lo ferma solo la tua lettura.
- **La sandbox vale per root, non per progetto**: i tool di sola lettura (`Read`, `Grep`, `Glob`) sono auto-approvati su tutti i progetti sotto le root di `APPROVED_DIRECTORY`, non solo su quello attivo, `.env` compresi (`src/permission_policy.py:100`). Una root con contenuti scaricati o di terzi mette testo non fidato, un vettore di prompt injection, nello stesso perimetro dei tuoi progetti di lavoro: tieni quelle cartelle fuori dalle root.
- **Gli allegati partono senza approvazione**: una riga `[[file: percorso]]` nella risposta di Claude, o un deliverable scritto da un `Write` approvato, viene caricato nella chat senza chiedere. Può partire qualunque file dentro le root della sandbox, compresi i `.env` di altri progetti, e da quel momento resta sui server di Telegram come ogni messaggio della chat (`src/file_delivery.py:70`). La cartella del bridge e i percorsi fuori dalle root non vengono mai inviati.
- **La tua configurazione di Claude Code viene caricata**: rule, hook, skill, plugin e server MCP del profilo scelto girano dentro le sessioni del bridge. Un hook delle tue impostazioni utente viene eseguito come in una sessione da terminale.

## Best practice per gli utenti

- Tieni in `ALLOWED_USERS` solo il tuo ID e disattiva l'ingresso nei gruppi da BotFather (`/setjoingroups`).
- Tieni `CLAUDE_AUTO_APPROVE_TOOLS` in sola lettura; aggiungi strumenti MCP solo se sono di sola consultazione.
- Tieni `APPROVED_DIRECTORY` stretto quanto il tuo lavoro permette. Non puntarlo alla tua home.
- Non committare mai `.env`; è già in `.gitignore`.
- Ferma il bot con `./stop.sh` quando non ti serve.

## Fuori ambito

- Vulnerabilità di Claude Code, Telegram o delle dipendenze di terze parti (segnalale ai rispettivi maintainer)
- Attacchi che richiedono il controllo di un account Telegram in whitelist
- Azioni che hai approvato con i bottoni di approvazione
- Attacchi di social engineering

## Riconoscimenti

Qui verranno elencati i ricercatori che segnalano vulnerabilità in modo responsabile.

---

[Torna al README](./README.it.md)
