# Piano 001: ricezione file dall'utente Telegram

## Obiettivo
Un documento o una foto inviati al bot finiscono in `<progetto attivo>/uploads/`, mai sovrascritti né fuori sandbox; l'utente riceve il path, e se ha scritto una didascalia parte un turno Claude che annuncia il file.

## Definition of Done
- [ ] Given progetto `alpha` attivo, When invio `report.pdf` (1 MB) senza didascalia, Then esiste `alpha/uploads/report.pdf` identico byte-per-byte, ricevo "📎 Salvato in uploads/report.pdf" e nessun turno Claude parte (fake_claude non riceve input).
- [ ] Given `uploads/report.pdf` esistente, When invio di nuovo `report.pdf`, Then nasce `uploads/report (1).pdf` e l'originale resta invariato; un terzo invio produce `report (2).pdf`.
- [ ] Given due documenti omonimi elaborati in concorrenza (album), Then finiscono in due file distinti (prenotazione del nome con create esclusivo, non check-then-write).
- [ ] Given `file_name` = `../../etc/x`, `a/b\c.txt`, `.envrc`, `""`, `".."`, nome da 400 caratteri o con caratteri di controllo, Then il file salvato sta sempre direttamente in `uploads/`, senza punto iniziale, con nome non vuoto, ≤ 200 byte UTF-8 (NFC) con estensione preservata, senza caratteri Unicode Cc/Cf (RTL override, zero-width) né NUL.
- [ ] Given `uploads` è un symlink verso fuori sandbox (o dentro `PROJECT_ROOT`), Then nessun byte scritto e messaggio "🚫 ... fuori dalle cartelle consentite".
- [ ] Given una foto (lista di PhotoSize), Then viene scaricata la risoluzione più grande come `photo_<YYYYMMDD_HHMMSS>_<file_unique_id>.jpg`.
- [ ] Given `file_size` > 20 MB, Then nessuna chiamata `get_file`, messaggio con il limite; given `file_size` assente e Telegram risponde "file is too big", stesso messaggio.
- [ ] Given download fallito (errore rete persistente o `OSError`), Then messaggio "⚠️ <nome>: download non riuscito (...)" e `uploads/` resta invariato (nessun file creato: si scrive solo a download completo).
- [ ] Given didascalia "riassumilo", Then parte un turno con testo `📎 File ricevuto: uploads/report.pdf\n\nriassumilo`; se il progetto è occupato vale la coda esistente (QUEUED_NOTICE).
- [ ] Given nessun progetto attivo, Then niente salvataggio, messaggio NO_PROJECT + tastiera progetti.
- [ ] Given video/audio/vocale, Then risposta "tipo non supportato" invece del silenzio attuale.
- [ ] `uv run pytest`, `uv run ruff check .`, `uv run mypy src tests` verdi; copertura branch dei nuovi moduli ≥ 80%; test di smoke manuale con il bot reale (1 PDF, 1 foto, 1 con didascalia).

## Assunzioni
- python-telegram-bot: `get_file()` → `File.download_as_bytearray()`, entrambi async (verificato via context7, sorgente `src/telegram/_files/file.py`). `photo[-1]` è la taglia più grande (wiki PTB). `file_size` opzionale: UNVERIFIED, da training data.
- Limite 20 MB del getFile sul server pubblico: da documentazione, non misurato. Nessun Local Bot API server.
- `uploads/` nella root del progetto, creata al bisogno; `.gitignore` del progetto non si tocca (gli upload possono finire in un commit dell'utente: accettato).
- Nome col punto iniziale: il punto viene rimosso (`.envrc` → `envrc`). Motivo concreto: direnv caricherebbe `uploads/.envrc` entrando nella cartella, e `permission_policy.SELF_EXECUTING_FILES` tratta già questi nomi come eseguibili.
- Il prompt di sistema v3 non cambia (`--resume` ignora un nuovo append-system-prompt): l'annuncio viaggia nel testo del turno.
- Il file è salvato dal bridge, non da Claude: nessuna richiesta di approvazione (l'utente che invia è già l'autorizzazione; auth guard group -1 filtra gli sconosciuti). Claude poi legge con `Read`, già auto-approvato in sandbox.

## Disambiguazione: quando partire con un turno
- A: turno solo se c'è didascalia, altrimenti conferma e basta.
- B: turno sempre, con testo di annuncio anche senza didascalia.
- C: mai turno, l'utente scrive dopo.
- D: raccolta per `media_group_id` con attesa breve, poi un turno unico per l'album.
- Implicazioni: B lancia N turni su un album da N foto e un turno senza istruzione; C costringe a un secondo messaggio anche quando l'istruzione era nella didascalia; D richiede stato e timer (debounce) nel bridge, con race su busy/stop; A copre il caso comune a costo zero ma su un album con didascalia (Telegram la mette sul primo elemento) il turno può partire prima che gli altri file siano salvati e ne annuncia uno solo.
- **Raccomandata: A**, con il limite dell'album dichiarato. D resta follow-up se l'uso reale lo chiede (non pianificato qui).

## Approccio
Functional core in `src/file_intake.py` (nome sicuro, estrazione allegato da Message, `write_unique(project_dir, name, data, sandbox)`, messaggi in costanti), shell I/O in `handlers/messages.handle_attachment` (progetto attivo ri-risolto con `resolve_project` al momento della scrittura, download in memoria con `download_as_bytearray` + `with_retry`, conferma, eventuale `run_user_turn`). Scrittura solo a download completo: `Sandbox.resolve` sulla destinazione finale (post-suffisso), poi `os.open(O_CREAT|O_EXCL|O_NOFOLLOW|O_WRONLY, 0o644)` in loop sui suffissi. Niente segnaposto, niente file parziali, nessun check-then-open. L'URL di download (contiene il token) non si costruisce né si logga mai. Nuovo `MessageHandler((filters.Document.ALL | filters.PHOTO) & filters.ChatType.PRIVATE)` (parentesi obbligatorie: `&` lega più di `|`) dopo quello testuale (i messaggi con didascalia non hanno `text`, nessuna sovrapposizione).

## Sub-task
Fase 1: salvataggio + conferma (mergiabile da sola)
1. [30m] `safe_filename(raw, fallback)` puro + test tabellari (traversal, assoluto, backslash, NUL, Cc/Cf incl. U+202E e U+200B, NFC, dot-leading, vuoto, `..`, troncamento a 200 byte con estensione e multibyte) — `src/file_intake.py`, `tests/test_file_intake.py` — rischio basso → verify: `uv run pytest tests/test_file_intake.py -k safe_filename` verde, visto rosso prima.
2. [30m] `write_unique(project_dir, name, data, sandbox)`: crea `uploads/`, Sandbox.resolve sulla destinazione finale, `os.open` O_EXCL|O_NOFOLLOW 0o644 con suffissi ` (n)`, `SandboxError` se symlink esce, stesso file, rischio medio (TOCTOU) → verify: test collisione ×3, scritture concorrenti in thread danno path distinti, symlink `uploads` fuori sandbox e file-symlink omonimo non seguiti, permessi 0o644.
3. [20m] `describe_attachment(message)` → (file_id, size, nome) per document e photo (PhotoSize più grande, nome `photo_...`), None altrimenti; conferma API PTB via context7 — rischio basso → verify: test con `SimpleNamespace` per doc, foto a 3 taglie, messaggio senza allegati.
4. [20m] `FakeBot.get_file` + `FakeFile.download_as_bytearray` (ritorna bytes configurabili, può sollevare `NetworkError`/`BadRequest("File is too big")`) — `tests/fakes.py` — rischio basso → verify: usato dai test del sub-task 5, suite esistente invariata.
5. [30m] `handle_attachment`: progetto attivo o NO_PROJECT, limite 20 MB prima di `get_file`, download in memoria con `with_retry`, poi `write_unique`, conferma con path relativo; log INFO (progetto, nome, byte), mai l'URL — `src/handlers/messages.py`, `tests/test_commands.py` (o `tests/test_uploads.py`) — rischio medio → verify: test DoD 1, 7, 8, 10 verdi; dopo errore di download `uploads/` non esiste o è vuota.
6. [15m] Registrazione handler in `register_handlers` — `src/bot.py`, `tests/test_telegram_flow.py` — rischio basso → verify: `first_handler_for` su update con document e con photo ritorna `handle_attachment`; `test_plain_text_still_goes_to_claude` ancora verde.
7. [20m] Smoke reale: PDF, foto, due PDF omonimi in album — rischio basso → verify: file presenti in `uploads/`, nomi `(1)`, messaggi ricevuti sul telefono.

Fase 2: didascalia avvia il turno (dipende da Fase 1)
8. [25m] Con caption: `TurnRequest(text=ANNOUNCE.format(path=...) + caption)` → `run_user_turn`; senza caption nessun turno — rischio basso → verify: test con `fake_claude` echo: il testo del turno contiene `uploads/report.pdf` e la didascalia; senza didascalia fake_claude non viene avviato; progetto busy → QUEUED_NOTICE.
9. [15m] Smoke reale: PDF + "riassumilo" → Claude legge il file con Read → verify: risposta che cita il contenuto.

Fase 3: feedback su media non supportati (indipendente)
10. [15m] Handler `VIDEO | AUDIO | VOICE | VIDEO_NOTE` → UNSUPPORTED — rischio basso → verify: test di routing + testo della risposta.

Totale: ~3h50 di lavoro + buffer 20% (primo handler di media, API PTB non verificata) ≈ 4h30-5h.

## File impattati
| File | Tipo | Scopo |
|---|---|---|
| src/file_intake.py | nuovo | nome sicuro, estrazione allegato, prenotazione path, costanti messaggi (~130 righe) |
| src/handlers/messages.py | modifica | `handle_attachment`, `handle_unsupported_media` |
| src/bot.py | modifica | due MessageHandler (bot.py 250 righe: resta < 300) |
| tests/test_file_intake.py | nuovo | test puri e su tmp_path |
| tests/fakes.py | modifica | `get_file`, `FakeFile` |
| tests/test_uploads.py | nuovo | handler end-to-end con FakeBot e fake_claude |
| tests/test_telegram_flow.py | modifica | routing |
| CLAUDE.md (progetto) | modifica | riga Architecture "Uploads" (autonomia consentita su CLAUDE.md) |

## Rischi
- Poiché PTB processa gli update in concorrenza, due file omonimi di un album possono calcolare lo stesso suffisso → sovrascrittura. Mitigazione: create esclusivo `open("xb")` in loop, test concorrente (sub-task 2).
- Poiché `file_size` è opzionale, un file > 20 MB può arrivare a `get_file` → BadRequest. Mitigazione: catch mirato del BadRequest "too big" mappato sullo stesso messaggio; gli altri BadRequest → "download non riuscito".
- Download in memoria: picco RAM ≤ 20 MB per upload concorrente. Accettato (single-user, limite Bot API).
- Nome file e didascalia finiscono nel testo del turno e poi nelle richieste di approvazione: i caratteri Cf (bidi, zero-width) nel nome sono tolti a monte (security-reviewer P1); la didascalia la scrive l'utente stesso e passa com'è.
- Nessuna quota disco aggregata (security-reviewer P1, declassato): chi carica è solo l'utente in whitelist, il rischio è un errore suo, non un attaccante. Parcheggiato; `OSError` (ENOSPC, ENAMETOOLONG) intercettato e riportato come messaggio.
- Album con didascalia: turno avviato prima degli altri file (limite accettato di opzione A, dichiarato in CLAUDE.md).
- `uploads/` può finire in un commit dell'utente: fuori scope per scelta (repo dell'utente).

## Criteri di successo
DoD tutta spuntata; `uv run pytest --cov=src --cov-branch` verde con `src/file_intake.py` ≥ 80% branch; ruff e mypy puliti; smoke reale dei sub-task 7 e 9 riportato con l'esito osservato.
