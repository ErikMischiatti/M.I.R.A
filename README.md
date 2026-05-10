# M.I.R.A. — Modular Interactive Robotic Agent

M.I.R.A. is a **software-first embodied AI assistant prototype** built in Python and PySide6.

The project explores how an assistant can feel more present by combining:
- an expressive animated face,
- text-based interaction,
- modular cognition,
- session memory,
- local desktop actions,
- and a scalable architecture designed to later evolve into a physical robotic platform.

Rather than starting from motors and sensors, M.I.R.A. first develops the **interaction and behavioral core** of the future system: how the agent receives input, interprets requests, selects actions, produces responses, and visually expresses its internal state.

---

## Demo

<p align="center">
  <img src="assets/mira_record.gif" alt="M.I.R.A. desktop prototype in action" width="850"/>
</p>

---

## Project Idea

M.I.R.A. is designed around the idea that an intelligent interface should not only answer, but also appear:

- **attentive** while the user interacts,
- **reactive** during processing,
- **expressive** in its responses,
- and **embodied** through motion and visible state changes.

The current implementation is a desktop application with:
- animated eyes,
- a chat interface,
- a live debug panel,
- intent recognition,
- session memory,
- action execution,
- and an optional local LLM-backed cognitive path through Ollama.

The long-term goal is to reuse this software core as the interaction layer of a future physical robotic assistant.

---

## Current Status

**Active software prototype**

The project currently focuses on the desktop software layer:
- interaction,
- cognition,
- expressive behavior,
- and local action execution.

Hardware integration is intentionally planned for a later stage, after the software architecture has matured.

---

## Main Features

### Expressive Face
Animated eyes with:
- blinking,
- idle motion,
- smooth state transitions,
- eyelid deformation,
- asymmetry,
- thinking drift,
- speaking pulse,
- and cursor-aware gaze behavior.

Supported expressive states currently include:
- `IDLE`
- `LISTENING`
- `THINKING`
- `SPEAKING`
- `HAPPY`
- `TIRED`
- `ANGRY`
- `CONFUSED`

### Event-Driven Interaction
A central event bus coordinates:
- user input,
- focus changes,
- processing state,
- inferred intents,
- action execution,
- response delivery,
- and visual feedback.

### Modular Cognitive Layer
The system supports interchangeable intent engines:
- a deterministic **rule-based engine**,
- and an optional **local LLM-backed engine** through Ollama.

Both produce the same normalized intent format, allowing the rest of the architecture to remain unchanged.

### Session Memory
The assistant stores:
- recent user messages,
- recent assistant responses,
- the last inferred intent,
- and lightweight contextual data for the current session.

### Local Action System
M.I.R.A. already supports a registry/executor pattern for local actions such as:
- retrieving current time and date,
- repeating text,
- inspecting session memory,
- opening URLs,
- launching selected desktop applications,
- showing system notifications,
- retrieving basic system information.

### Live Debug Panel
A dedicated runtime tuning panel allows direct editing of:
- expression profiles,
- eye geometry,
- animation flags,
- blink timing,
- idle motion,
- asymmetry parameters.

Profiles can be saved and reloaded from configuration files without modifying the application logic.

---

## Architecture Overview

```text
User Input
   ↓
Chat / Interaction Layer
   ↓
Event Bus
   ↓
Brain
   ├── Intent Engine
   │     ├── RuleIntentEngine
   │     └── LLMIntentEngine
   ├── Session Memory
   ├── Action Registry
   ├── Action Executor
   └── Response Builder
   ↓
State Manager / Embodied Behavior
   ↓
Face UI + Chat Response
```

The project is organized around a modular separation of responsibilities:

- `actions/`  
  Action models, registry, executor, and concrete system actions.

- `cognition/`  
  Intent engines, local LLM integration, schemas, and response construction.

- `core/`  
  Brain orchestration, event bus, interaction manager, state manager, embodied behavior, and memory.

- `ui/`  
  Chat panel, debug panel, main window, expressive face rendering, and animation control.

- `config/`  
  Runtime-configurable expression profiles and related configuration data.

---

## Current Interaction Flow

A typical interaction currently follows this pipeline:

```text
User message
   ↓
Input stored in session memory
   ↓
LISTENING state
   ↓
Intent inference
   ↓
Optional action request
   ↓
Action execution
   ↓
Response building
   ↓
Response stored in memory
   ↓
Face state update + chat output
```

This creates a tight link between cognition and embodiment: the system does not only process requests internally, but exposes its current interaction phase through visible expressive states.

---

## Local LLM Integration

M.I.R.A. includes an optional LLM-based intent engine using a local Ollama model.

The LLM path currently:
- receives natural language input,
- converts it into a structured JSON result,
- selects a normalized intent,
- optionally proposes an available action and parameters,
- preserves compatibility with the same backend contract used by the rule-based engine.

The LLM integration is still under active development. Current work focuses on:
- reducing response latency,
- making inference non-blocking,
- extending the use of LLM-generated responses,
- incorporating session context,
- and linking LLM-derived emotional output more directly to embodied behavior.

To start the application with the LLM-backed intent engine:

```bash
MIRA_INTENT_ENGINE=llm python -m mira.main
```

Without the environment variable, the system defaults to the rule-based engine.

---

## Technologies

- Python
- PySide6 / Qt
- Event-driven architecture
- Rule-based intent inference
- Local LLM integration with Ollama
- Action registry / executor pattern
- Session memory
- Procedural animation
- Desktop automation
- Human-machine interaction

---

## Project Structure

```text
mira/
├── actions/
│   ├── action_executor.py
│   ├── action_models.py
│   ├── action_registry.py
│   ├── builtin_actions.py
│   └── desktop_actions.py
│
├── cognition/
│   ├── intent_engine.py
│   ├── llm_client.py
│   ├── llm_intent_engine.py
│   ├── llm_schema.py
│   ├── response_builder.py
│   └── rule_intent_engine.py
│
├── config/
│   └── expression_profiles.json
│
├── core/
│   ├── brain.py
│   ├── embodied_behavior.py
│   ├── events.py
│   ├── interaction_manager.py
│   ├── models.py
│   ├── session_memory.py
│   └── state_manager.py
│
├── ui/
│   ├── chat_panel.py
│   ├── debug_panel.py
│   ├── main_window.py
│   └── face/
│       ├── expression_library.py
│       ├── expression_profile.py
│       ├── expression_store.py
│       ├── eye.py
│       ├── face_controller.py
│       ├── face_state.py
│       └── face_widget.py
│
└── main.py
```

---

## Running the Project

### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install PySide6
```

### 3. Run the default rule-based version

```bash
python -m mira.main
```

### 4. Run with the local LLM-backed intent engine

Make sure Ollama is running and the configured model is available, then launch:

```bash
MIRA_INTENT_ENGINE=llm python -m mira.main
```

---

## Roadmap

### Current
- PySide6 desktop application
- Expressive animated face
- Chat interface
- Debug panel
- Event bus and state manager
- Brain orchestration layer
- Session memory
- Rule-based intent engine
- Local action registry and executor
- Optional Ollama-backed LLM intent engine

### In Progress
- Improve LLM response latency
- Make LLM inference non-blocking
- Expand use of LLM-generated responses
- Better integration between cognitive output and expressive behavior

### Next
- Voice input and speech output
- Wake interaction
- Richer contextual memory
- Multimodal interaction management
- Webcam-based presence awareness

### Future
- Physical embodiment
- Sensors and actuators
- Audio hardware
- Mechanical expression
- Integration with a robotic platform

---

## Author

**Erik Mischiatti**  
M.Sc. Mechatronics Engineering
