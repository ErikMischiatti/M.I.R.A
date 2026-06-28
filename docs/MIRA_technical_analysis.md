# Analisi tecnica repository M.I.R.A.

Data analisi: 2026-06-28  
Repository: `/home/erik/Desktop/Projects/M.I.R.A.`

Questa analisi descrive lo stato corrente del repository M.I.R.A. sulla base dei file presenti. I nomi N.E.R.O. e H.A.R.O. sono trattati come provvisori.

Nota di stato: questo documento rappresenta lo stato tecnico del repository alla data 2026-06-28. Le sezioni descrittive riportano lo stato implementato; roadmap, branch plan e sezioni di rischio contengono raccomandazioni.

## 1. Repository Map

| Percorso | Scopo |
|---|---|
| `README.md` | Documentazione principale: idea del progetto, architettura, setup, comandi di avvio, roadmap. |
| `mira/AGENTS.md` | Istruzioni interne di sviluppo per agenti: vincoli architetturali, regole async, GUI, LLM, validazione. |
| `requirements.txt` | Dipendenze: `PySide6`, `requests`, `pytest`. |
| `LICENSE` | Licenza MIT. |
| `assets/mira_record.gif` | GIF demo usata nel README. |
| `mira/main.py` | Entry point applicativo. Crea `QApplication`, istanzia `MainWindow`, avvia il loop Qt. |
| `mira/core/` | Orchestrazione runtime: `Brain`, `EventBus`, `StateManager`, `InteractionManager`, `EmbodiedBehavior`, `SessionMemory`, modelli condivisi. |
| `mira/cognition/` | Layer cognitivo: astrazione intent engine, rule engine, LLM engine, client Ollama, schema LLM, response builder. |
| `mira/actions/` | Sistema azioni/tool: modelli, contratti, registry, executor, azioni built-in e desktop. |
| `mira/ui/` | UI PySide6: finestra principale, chat, debug panel, face UI. |
| `mira/ui/face/` | Stato espressivo, rendering occhi, controller, profili espressione e persistence JSON. |
| `mira/config/expression_profiles.json` | Profili espressivi runtime caricati dal face controller. |
| `tests/` | Test unitari per cognition, azioni, LLM, response builder e contratto async del brain. |
| `venv/` | Virtualenv locale già presente. Usato solo per eseguire i test. |

Alla data dell'analisi non erano presenti `pyproject.toml`, `setup.cfg`, `setup.py`, `requirements.lock`, file Qt `.ui` o `.qss`.

## 2. Architettura corrente

L'applicazione parte da `mira/main.py`. Il runtime viene composto direttamente in `mira/ui/main_window.py`.

```text
MainWindow
├── EventBus
├── StateManager
├── Brain
│   ├── SessionMemory
│   ├── IntentEngine
│   │   ├── RuleIntentEngine
│   │   └── LLMIntentEngine
│   ├── ResponseBuilder
│   ├── ActionRegistry
│   └── ActionExecutor
├── InteractionManager
├── EmbodiedBehavior
├── FaceWidget
│   └── FaceController
├── ChatPanel
└── DebugPanel
```

Flusso architetturale:

```text
User Input
  ↓
ChatPanel
  ↓
MainWindow
  ↓
Brain
  ├── SessionMemory
  ├── IntentEngine
  ├── ActionRegistry / ActionExecutor
  └── ResponseBuilder
  ↓
EventBus / StateManager
  ↓
InteractionManager / EmbodiedBehavior
  ↓
FaceWidget + ChatPanel + DebugPanel
```

La separazione concettuale è buona: cognition, tools, memoria e UI sono in package distinti. Il punto più accoppiato è `MainWindow`, che oggi costruisce direttamente tutto il grafo applicativo.

## 3. Main Runtime Flow

Flusso di un messaggio utente:

```text
Utente scrive nella chat
→ ChatPanel.submit_message()
→ MainWindow.on_user_message_submitted()
→ ChatPanel.add_user_message()
→ Brain.process_text_async()
→ SessionMemory.add_user_input()
→ EventBus: user_input_received
→ StateManager: LISTENING
→ QTimer listening delay
→ EventBus: processing_started
→ StateManager: THINKING
→ QThreadPool worker
→ IntentEngine.infer()
→ build_action_request()
→ ritorno su thread Qt principale
→ controllo request stale
→ memory.last_intent aggiornato
→ EventBus: intent_inferred
→ ActionExecutor.execute(), se esiste un'azione
→ ResponseBuilder.build()
→ SessionMemory.add_response()
→ EventBus: response_ready
→ StateManager: response.face_state
→ callback UI: ChatPanel.add_mira_message()
→ FaceWidget aggiorna espressione
→ EmbodiedBehavior decade verso IDLE/LISTENING
```

Confini sync/async:

| Area | Stato corrente |
|---|---|
| `Brain.process_text()` | Sincrono; utile per test o uso non-UI, ma bloccherebbe la UI con LLM lento. |
| `Brain.process_text_async()` | Percorso UI-safe basato su `QTimer`, `QRunnable`, `QThreadPool` e signal Qt. |
| Worker thread | Inferisce intent e costruisce un eventuale `ActionRequest`. |
| Main thread | Esegue azioni, muta memoria, emette eventi, aggiorna stato, chiama callback UI. |

Rischio thread-safety: `EventBus` e `StateManager` sono sincroni e senza lock. Il design è sicuro solo perché il worker non emette eventi e non muta memoria.

## 4. Cognitive Layer

| Componente | File | Comportamento |
|---|---|---|
| `Brain` | `mira/core/brain.py` | Orchestrazione: memoria, intent, azioni, response, eventi, stati. |
| `IntentEngine` | `mira/cognition/intent_engine.py` | Contratto astratto `infer(UserInput) -> IntentResult`. |
| `RuleIntentEngine` | `mira/cognition/rule_intent_engine.py` | Regole deterministiche per greeting, stato, identità, ora/data, memoria, desktop, path progetto. |
| `LLMIntentEngine` | `mira/cognition/llm_intent_engine.py` | Prompt strutturato con contesto recente sanitizzato, chiamata Ollama, validazione azioni, fallback rule con diagnostica. |
| `OllamaClient` | `mira/cognition/llm_client.py` | POST verso `/api/generate`, JSON schema in `format`, parsing risposta. |
| `LLM schema` | `mira/cognition/llm_schema.py` | Intent consentiti, schema JSON, validazione compatibilità intent/action. |
| `SessionContextBuilder` | `mira/cognition/session_context_builder.py` | Snapshot bounded e sanitizzato per prompt LLM, con safe session facts. |
| `user_facts` | `mira/cognition/user_facts.py` | Estrazione deterministica conservativa del nome utente session-local. |
| `ResponseBuilder` | `mira/cognition/response_builder.py` | Genera `BrainResponse` per UI e face state. |

Cosa funziona:

- Il rule engine copre bene il set attuale di intent.
- Il backend LLM è opzionale.
- Su errore Ollama, timeout o risposta non valida, il sistema torna al rule engine.
- Le azioni proposte dall'LLM sono validate contro contratti espliciti.
- Le azioni proposte dall'LLM sono bloccate sotto la soglia `MIRA_LLM_ACTION_MIN_CONFIDENCE`.
- Le emozioni LLM sono mappate a `FaceState` tramite allowlist.
- Le risposte da azione hanno priorità sul testo conversazionale LLM.
- Il prompt LLM include un contesto recente bounded e sanitizzato tramite `SessionContextBuilder`.
- Il messaggio utente corrente resta separato dal contesto dei turni precedenti.
- La memoria di sessione conserva safe facts espliciti come `user_name`, senza persistenza su disco.
- I fallback LLM espongono metadata diagnostici stabili e sicuri: `llm_fallback_used` e `llm_fallback_reason`.
- `ResponseBuilder` gestisce risposte cognitive deterministiche per identità assistente, contesto progetto, set/ask nome utente.

Fragilità:

- `RuleIntentEngine` usa euristiche string-based.
- `ResponseBuilder` è una catena crescente di `if`.
- `Brain.build_action_request()` duplica informazioni già presenti nei contratti azione.
- `requires_confirmation` esiste nei modelli ma non è applicato.
- Non c'è ancora memoria persistente multi-sessione.
- Non viene ancora usata una dipendenza completa per validazione JSON Schema.

## 5. Memory e Context

File principale: `mira/core/session_memory.py`.

| Aspetto | Stato corrente |
|---|---|
| Tipo memoria | Solo in-memory/session-local. |
| Persistenza | Assente. |
| Bound messaggi | `max_history=20`. |
| Bound caratteri | Assente. |
| Messaggi | `MemoryMessage(role, text, metadata)`. |
| Last intent | `SessionMemory.last_intent`. |
| Context generico | `SessionMemory.context`, usato per safe facts session-local come `user_name`. |
| Context helpers | `set_context_value`, `get_context_value`, `clear_context_value`; alias legacy `set_context`/`get_context`. |
| Recent history | `get_recent_history(limit)`. |
| Prompt context | `SessionContextBuilder` include safe facts e recent history sanitizzata, escludendo il current input. |

Nota importante: `Brain.process_text_async()` salva il messaggio utente prima dell'inferenza. Quindi un'azione come `get_last_user_message` tende a restituire il messaggio corrente, non quello precedente. Per il prompt LLM, `SessionContextBuilder.build(current_input=...)` rimuove il messaggio corrente dal blocco di contesto e lo lascia nella sezione separata `Current user input`.

Nel percorso LLM, `SessionContextBuilder.build(current_input=...)` esclude il messaggio utente corrente dal blocco "Recent conversation context". Il prompt mantiene quel blocco separato dalla sezione "Current user input", evitando che il turno corrente venga duplicato come history precedente.

Possibili leak/staleness:

- `IntentResult.entities` può contenere `llm_raw`.
- `get_last_intent` restituisce anche entities nei dati dell'action result.
- I metadata delle risposte assistant vengono salvati in memoria.
- `clear_session_memory` cancella memoria e poi la risposta finale viene aggiunta di nuovo alla history.

## 6. Sistema Action / Tool

| Componente | File | Ruolo |
|---|---|---|
| `ActionRequest`, `ActionResult`, `ActionContract` | `mira/actions/action_models.py` | Modelli dati. |
| `ActionRegistry` | `mira/actions/action_registry.py` | Registry handler e contratti. |
| `ActionExecutor` | `mira/actions/action_executor.py` | Valida forma richiesta, emette eventi, esegue handler, normalizza risultato. |
| `ACTION_CONTRACTS` | `mira/actions/action_contracts.py` | Elenco azioni consentite, intent compatibili, parametri richiesti. |
| Built-in actions | `mira/actions/builtin_actions.py` | Ora/data, echo, memoria, introspezione. |
| Desktop actions | `mira/actions/desktop_actions.py` | URL, app, notifiche, directory, sistema, path progetto. |

| Action | File | Parametri | Side effect | Rischio | Note |
|---|---|---|---|---|---|
| `get_time` | `mira/actions/builtin_actions.py` | Nessuno | Legge ora locale | Basso | Usa `datetime.now()`. |
| `get_date` | `mira/actions/builtin_actions.py` | Nessuno | Legge data locale | Basso | Formato `YYYY-MM-DD`. |
| `echo_text` | `mira/actions/builtin_actions.py` | `text: str` | Nessuno | Basso | Fallisce se testo vuoto. |
| `get_last_intent` | `mira/actions/builtin_actions.py` | Nessuno | Legge memoria | Medio-basso | I dati includono entities. |
| `get_session_summary` | `mira/actions/builtin_actions.py` | Nessuno | Legge memoria | Basso | Riassunto grezzo degli ultimi 10 messaggi. |
| `clear_session_memory` | `mira/actions/builtin_actions.py` | Nessuno | Cancella memoria sessione | Medio | Nessuna conferma. |
| `list_available_actions` | `mira/actions/builtin_actions.py` | Nessuno | Legge registry | Basso | Introspezione. |
| `get_memory_size` | `mira/actions/builtin_actions.py` | Nessuno | Legge memoria | Basso | Conta messaggi in history. |
| `get_last_user_message` | `mira/actions/builtin_actions.py` | Nessuno | Legge memoria | Basso | Oggi può restituire il messaggio corrente. |
| `open_url` | `mira/actions/desktop_actions.py` | `url: str` | Apre browser | Medio | Solo `http/https`; normalizza schema mancante. |
| `open_app` | `mira/actions/desktop_actions.py` | `app_name: str` | Avvia app allowlisted | Medio | Usa `subprocess.Popen`; Linux-oriented. |
| `show_notification` | `mira/actions/desktop_actions.py` | `text: str`, opzionale `title` | Esegue `notify-send` | Medio | Linux desktop-specific. |
| `open_directory` | `mira/actions/desktop_actions.py` | `directory: str` | Esegue `xdg-open` | Medio | Solo sotto home o project cwd. |
| `get_system_info` | `mira/actions/desktop_actions.py` | Nessuno | Legge info sistema | Medio-basso | Espone hostname/piattaforma. |
| `get_project_path` | `mira/actions/desktop_actions.py` | Nessuno | Legge cwd | Medio-basso | Espone path locale. |

Boundary di sicurezza già presenti:

- Azioni LLM limitate da `ACTION_CONTRACTS`.
- App desktop limitate da `APP_COMMANDS`.
- Directory limitate a home e current project.
- URL limitati a HTTP/HTTPS.

Limiti:

- `ActionExecutor` non valida i contratti, solo la forma della richiesta.
- La conferma utente non è implementata.
- Le azioni desktop sono Linux-specific.

## 7. UI ed Embodiment

| Area | File | Stato corrente |
|---|---|---|
| Main window | `mira/ui/main_window.py` | Layout compatto, debug drawer, wiring eventi/core. |
| Chat | `mira/ui/chat_panel.py` | Storia conversazione, input, bottone send, action status. |
| Debug panel | `mira/ui/debug_panel.py` | Slider/checkbox per profili espressivi, save/reload/reset. |
| Face state | `mira/ui/face/face_state.py` | Enum: `IDLE`, `LISTENING`, `THINKING`, `SPEAKING`, `HAPPY`, `TIRED`, `ANGRY`, `CONFUSED`. |
| Face widget | `mira/ui/face/face_widget.py` | Rendering occhi con `QPainter`, timer 30 ms, mouse gaze. |
| Face controller | `mira/ui/face/face_controller.py` | Blink, idle motion, speaking pulse, thinking drift, interpolazioni. |
| Expression library | `mira/ui/face/expression_library.py` | Default profile per ogni stato. |
| Expression store | `mira/ui/face/expression_store.py` | Load/save JSON da `mira/config/expression_profiles.json`. |

UI-only:

- Rendering PySide6.
- Debug sliders.
- Storia chat HTML.
- Shortcut numerici del face widget.

Riutilizzabile per hardware futuro:

- `FaceState`.
- `EmbodiedBehavior`.
- Eventi semantici di input, processing, intent, action, response.

Da mantenere indipendente dal futuro hardware:

- `Brain`.
- `IntentEngine`.
- `ActionRegistry`.
- `ActionExecutor`.
- `SessionMemory`.

## 8. State Management ed Eventi

| Componente | File | Comportamento |
|---|---|---|
| `EventBus` | `mira/core/events.py` | Pub/sub sincrono con stringhe evento. |
| `StateManager` | `mira/core/state_manager.py` | Stato corrente `FaceState`, emette `state_changed`. |
| `InteractionManager` | `mira/core/interaction_manager.py` | Gestisce focus/input/processing e stati LISTENING/THINKING/IDLE. |
| `EmbodiedBehavior` | `mira/core/embodied_behavior.py` | Micro-reazioni e decay degli stati espressivi. |

Eventi osservati:

- `state_changed`
- `input_focused`
- `input_unfocused`
- `input_text_changed`
- `user_input_received`
- `processing_started`
- `intent_inferred`
- `response_ready`
- `action_started`
- `action_completed`
- `action_failed`

Rischi:

- Eventi come stringhe libere.
- Payload non tipizzati.
- Nessun unsubscribe.
- Più componenti possono impostare lo stato facciale.
- Nessuna tabella formale delle transizioni.

## 9. LLM Integration

| Aspetto | Stato corrente |
|---|---|
| Selezione engine | `MIRA_INTENT_ENGINE=llm`; default `rule`. |
| Model env | `MIRA_OLLAMA_MODEL`, default `llama3.2:3b`. |
| Base URL env | `MIRA_OLLAMA_BASE_URL`, default `http://localhost:11434`. |
| Timeout env | `MIRA_OLLAMA_TIMEOUT_S`, default `10.0`. |
| Action confidence env | `MIRA_LLM_ACTION_MIN_CONFIDENCE`, default `0.65`; valori invalidi tornano al default, valori numerici clamped a `0.0..1.0`. |
| Action confidence env | `MIRA_LLM_ACTION_MIN_CONFIDENCE`, default `0.65`; valori invalidi tornano al default, valori numerici clamped a `0.0..1.0`. |
| API | Ollama `/api/generate`, `stream: False`, `format` schema. |
| Prompt | Intent parser per N.E.R.O. (nome provvisorio); allowed intents/actions; safe session facts; recent context bounded/sanitizzato; current input separato. |
| Output | `intent`, `confidence`, `emotion`, `action_name`, `parameters`, `response_text`. |
| Fallback | Rule engine su errore client, timeout, JSON invalido o output non-dict; diagnostica stabile per validazioni e action suppression. |

Le azioni proposte dall'LLM sono eseguibili solo se superano `MIRA_LLM_ACTION_MIN_CONFIDENCE`. Se sono sotto soglia, vengono rimosse dai metadata eseguibili e l'intent espone `action_suppressed_reason = "low_confidence"` e `action_min_confidence = <threshold>`.

I fallback LLM espongono `llm_fallback_used` e `llm_fallback_reason`. Ragioni implementate: `client_error`, `invalid_response`, `invalid_json`, `unsupported_intent`, `unknown_action`, `intent_action_mismatch`, `invalid_parameters`, `low_confidence_action`, `invalid_schema`.

Limitazioni:

- Nessuna astrazione provider oltre Ollama.
- Nessun retry/backoff.
- Nessun check disponibilità modello.
- `llm_raw` viene conservato negli entities per backward compatibility.
- Nessuna memoria persistente.
- Nessuna UI confirmation flow.
- Nessuna dipendenza completa per validazione JSON Schema.

## 10. Tests e Validation

Comandi eseguiti:

```bash
python3 -m pytest
```

Risultato: fallito in collection perché il Python di sistema non ha `PySide6`.

```bash
venv/bin/python -m pytest
```

Risultato:

```text
103 passed in 0.11s
```

Copertura attuale:

| File test | Copertura |
|---|---|
| `tests/test_action_contract_consistency.py` | Coerenza contratti/azioni e metadata prompt LLM con contesto quando la memoria è fornita. |
| `tests/test_action_executor.py` | Success/failure executor, azione sconosciuta, request invalida, eccezioni handler, registry contracts. |
| `tests/test_brain_async_contract.py` | Separazione worker/main thread, stale result, action fallback. |
| `tests/test_desktop_actions.py` | URL, app allowlist, directory allowlist, project path. |
| `tests/test_llm_intent_engine.py` | Conversione LLM, fallback, validazione azioni, confidence clamp, prompt. |
| `tests/test_response_builder.py` | Priorità azioni, testo LLM, emotion mapping, fallback deterministic. |
| `tests/test_rule_intent_engine.py` | Regole directory, project path, frasi distruttive, schemi URL, identità, contesto progetto e nome utente. |
| `tests/test_session_context_builder.py` | Context builder LLM: safe facts, bound, sanitizzazione, esclusione metadata e current input separato. |
| `tests/test_session_memory.py` | Helper context session-local e clear memoria. |
| `tests/test_brain_cognitive_flow.py` | Flusso sincrono completo per set/ask/update nome e contesto progetto senza azioni desktop. |

Mancano:

- Test GUI reali con `QApplication`.
- Test visuali/rendering.
- Test event ordering.
- Test memory persistence.
- Test prompt injection.
- Test conferma azioni.
- Test cross-platform desktop.

## 11. Configurazione e Setup

Documentato in `README.md`:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m mira.main
```

Rule mode:

```bash
MIRA_INTENT_ENGINE=rule python3 -m mira.main
```

LLM mode:

```bash
MIRA_INTENT_ENGINE=llm python3 -m mira.main
```

Ollama:

```bash
MIRA_OLLAMA_MODEL=llama3.2:3b
MIRA_OLLAMA_BASE_URL=http://localhost:11434
MIRA_OLLAMA_TIMEOUT_S=10
```

Assunzioni piattaforma:

- Ambiente Linux desktop.
- `xdg-open` per directory.
- `notify-send` per notifiche.
- `x-terminal-emulator`, `gnome-calculator`, `firefox`, `google-chrome` per app allowlisted.

Mancanze:

- Nessun packaging standard.
- Nessun lockfile.
- Nessun `.env.example`.
- Nessuna matrice piattaforme.
- Nessuna documentazione hardware abstraction.

## 12. Punti di forza

- Struttura package chiara.
- Brain async ben separato tra worker e main thread.
- Fallback rule robusto quando LLM non è disponibile.
- Sistema action registry/executor/contracts già utile.
- Azioni desktop ristrette e allowlisted.
- Session memory semplice e bounded.
- `FaceState` utile come base hardware-neutral.
- Debug panel efficace per tuning espressioni.
- Test suite ampia per lo stato attuale e passante nel virtualenv.

## 13. Debolezze e rischi

| Rischio | File | Dettaglio |
|---|---|---|
| Runtime assembly dentro UI | `mira/ui/main_window.py` | `MainWindow` crea tutto il core. |
| Eventi non tipizzati | `mira/core/events.py` | Stringhe libere e payload generici. |
| Stato facciale scritto da più componenti | `mira/core/brain.py`, `mira/core/interaction_manager.py`, `mira/core/embodied_behavior.py` | Possibili conflitti d'ordine. |
| Mapping intent/action duplicato | `mira/core/brain.py`, `mira/actions/action_contracts.py` | Contratti non sono single source of truth. |
| Conferma non implementata | `mira/actions/action_models.py` | `requires_confirmation` esiste ma non viene usato. |
| `llm_raw` legacy | `mira/cognition/llm_intent_engine.py` | Ancora conservato negli entities per backward compatibility. |
| Memoria non persistente | `mira/core/session_memory.py` | Solo sessione corrente; `user_name` non sopravvive al riavvio. |
| UI confirmation assente | `mira/actions/action_models.py`, `mira/ui/*` | `requires_confirmation` esiste ma non viene usato da un flusso UI. |
| JSON Schema validator assente | `mira/cognition/llm_schema.py` | Validazione lightweight custom, senza dipendenza completa dedicata. |
| Desktop Linux-specific | `mira/actions/desktop_actions.py` | `xdg-open`, `notify-send`, comandi Linux. |
| ResponseBuilder poco scalabile | `mira/cognition/response_builder.py` | Crescita tramite catena `if`. |

## 14. Missing pieces per il prossimo milestone

Per far sentire M.I.R.A. come un assistente locale embodied coerente mancano soprattutto:

- Confirmation flow per azioni con side effect.
- Permission/risk model per tool.
- Memoria persistente e preferenze utente.
- Desktop actions più ricche ma sicure.
- UI feedback per pending, error, action progress, cancellation.
- Event model tipizzato.
- Assembly applicativo fuori da `MainWindow`.
- Adapter hardware-neutral per futuro H.A.R.O.

## 15. Roadmap raccomandata

Immediate cleanup:

- Aggiungere `pyproject.toml` o metadata minimi.
- Documentare comando test standard.
- Centralizzare nomi eventi.
- Sostituire `print` con `logging`.
- Aggiungere `.env.example`.

Short-term:

- Confirmation UI per azioni rischiose.
- Pending state nel `ChatPanel`.
- Migliorare risposta per memory actions.
- Valutare validator JSON Schema completo per il payload LLM.

Medium-term:

- App factory/container fuori da `MainWindow`.
- Contratti azioni come single source per mapping intent/action.
- Response handlers per azioni invece di chain `if`.
- Backend memoria persistente.
- Desktop capability abstraction.

Future H.A.R.O. readiness:

- Interfaccia `EmbodimentAdapter`.
- Eventi sensori/attuatori.
- Mapping `FaceState` verso hardware.
- Mock/simulator hardware.
- Test hardware-neutral.

## 16. Branch plan suggerito

| Branch | Goal | File coinvolti | Rischio | Validazione |
|---|---|---|---|---|
| `docs/architecture_overview` | Documentare architettura e runtime flow. | `README.md`, `docs/*` | Basso | Review docs. |
| `chore/project_packaging` | Packaging e test metadata. | `pyproject.toml`, `README.md` | Basso | `venv/bin/python -m pytest`. |
| `ftr/llm_integration_hardening` | Context, retry, thresholds, redaction. | `mira/cognition/*`, `mira/core/session_memory.py` | Medio | Test LLM/fallback/prompt. |
| `ftr/task_tool_execution` | Confirmation e permission levels. | `mira/actions/*`, `mira/core/brain.py`, `mira/ui/chat_panel.py` | Medio-alto | Test executor + manual UI. |
| `ftr/desktop_interaction` | Azioni desktop più ricche e sicure. | `mira/actions/desktop_actions.py`, contracts, tests | Alto | Mock subprocess/webbrowser + manual Linux. |
| `ftr/memory_persistence` | Memoria persistente locale. | `mira/core/session_memory.py`, nuovo storage module | Medio | Test persistence/clear/migration. |
| `ftr/embodiment_state_model` | Abstraction hardware-neutral. | `mira/core/embodied_behavior.py`, `mira/ui/face/*` | Medio | Unit test + manual UI. |
| `ftr/event_model_typing` | Eventi e payload tipizzati. | `mira/core/events.py`, chiamanti | Medio | Full suite + test ordering. |

## 17. Domande e ambiguità

- L'identità user-facing deve dire M.I.R.A., N.E.R.O. o un nome provvisorio diverso?
- Le memory action devono riferirsi al messaggio corrente o al turno precedente?
- Quali azioni devono richiedere conferma?
- La memoria persistente deve partire da JSON, SQLite o astrazione generica?
- L'LLM deve solo classificare intent o anche generare risposte conversazionali?
- Target OS: Linux-only per ora o cross-platform a breve?
- Il futuro hardware deve essere modellato come azioni, eventi o layer attuatore separato?

## 18. Executive summary

M.I.R.A. è oggi un prototipo desktop Python/PySide6 di assistente embodied software-first. L'app parte da `mira/main.py`, costruisce `MainWindow` e compone direttamente UI, cognition, memoria, azioni, eventi, stati ed embodied behavior.

La struttura modulare è già buona: `core` orchestra runtime e stato, `cognition` gestisce rule/LLM intent parsing e response building, `actions` implementa registry/executor/contracts e tool locali, `ui` fornisce chat, debug panel e volto animato.

Il flusso principale è asincrono e UI-safe: il worker inferisce intent e prepara action request, mentre il main thread esegue azioni, muta memoria, emette eventi e aggiorna UI. Questo è uno dei punti tecnici più solidi.

Il sistema azioni è già utile e relativamente sicuro: azioni registrate esplicitamente, LLM limitato da contratti, desktop actions allowlisted. I rischi principali sono la mancanza di conferma, la validazione contratti non centralizzata nell'executor e l'assunzione Linux.

La memoria è solo session-local, bounded a 20 messaggi e senza persistenza. Il prompt LLM usa il messaggio corrente separato da un contesto recente bounded e sanitizzato costruito da `SessionContextBuilder`; safe facts come `user_name` sono disponibili solo nella sessione corrente.

Le prossime priorità consigliate sono: documentazione architetturale, packaging minimo, event model tipizzato, confirmation flow per tool, app composition fuori da `MainWindow`, memoria persistente, eventuale validator JSON Schema completo e adapter hardware-neutral per preparare la futura piattaforma H.A.R.O. (nome provvisorio).
