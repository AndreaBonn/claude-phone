# Tasks 001: ricezione file

| id | dipende da | requisito |
|---|---|---|
| T1 | - | `safe_filename`: basename, NFC, niente Cc/Cf/NUL/punto iniziale/vuoto/`..`, ≤200 byte con estensione |
| T2 | T1 | `write_unique`: `uploads/` al bisogno, Sandbox.resolve su destinazione finale, O_EXCL+O_NOFOLLOW 0o644 con suffisso ` (n)`, mai sovrascrivere |
| T3 | - | `describe_attachment`: document e photo (PhotoSize più grande), nome foto deterministico; `photo[-1]` |
| T4 | - | FakeBot.get_file + FakeFile con errori simulabili |
| T5 | T2,T3,T4 | `handle_attachment`: NO_PROJECT, limite 20 MB, download in memoria con retry, scrittura solo a download completo, mai loggare l'URL, conferma con path |
| T6 | T5 | registrazione MessageHandler document/photo, routing testato |
| T7 | T6 | smoke reale: PDF, foto, omonimi in album |
| T8 | T6 | didascalia → turno con annuncio + caption; senza didascalia nessun turno; coda su busy |
| T9 | T8 | smoke reale: PDF + didascalia, Claude legge il file |
| T10 | - | risposta "tipo non supportato" per video/audio/voice/video_note |
