# Indicaciones generales del usuario (obligatorias)

Se cargan desde `CLAUDE.md`. Si el usuario cambia algo, se actualiza aquí.

## Cómo hablar
- Poco y conciso. Respuestas cortas, al grano, en español.
- Cuando el usuario da una idea o un cambio, hacer **un par de preguntas** antes de ponerse (lo que no esté claro).

## Cómo trabajar
- **Siempre con todos los agentes** disponibles (`.claude/agents/`), con el flujo de `CLAUDE.md`: ronda de ideas, discusión y el Capitán decide.
- **Usar siempre las skills** que apliquen (lista abajo). Revisarlas al empezar cada tarea.

## Palabras clave
| El usuario dice | Qué hacer |
|---|---|
| «guardado en idea» / «guárdalo como idea» | Solo guardarlo en `docs/ideas.md` (con fecha). No programar. |
| «desarrollo difícil» | Desarrollar con **Opus** (agentes con `model: opus`). |
| «desarrollo fácil» | Desarrollar con un modelo más ligero: **Sonnet** (`model: sonnet`); Haiku para tareas mínimas. |

## Skills disponibles y cuándo usarlas
| Grupo | Skills | Cuándo |
|---|---|---|
| Ponytail | `ponytail:ponytail` (modo por defecto), `ponytail-review`, `ponytail-audit`, `ponytail-debt` | Siempre: el cambio más pequeño que funcione. Review al terminar cada cambio; audit para revisar todo el repo. |
| Everything Claude Code | `everything-claude-code:plan`, `tdd`, `e2e`, `coding-standards`, `security-review`, `backend-patterns`, `frontend-patterns`, `strategic-compact`, `continuous-learning` | Planificar cambios grandes, pruebas primero, pruebas de punta a punta (Playwright), seguridad (mando del móvil, puente, extensión). |
| Superpowers | `brainstorming`, `systematic-debugging`, `writing-plans`, `executing-plans`, `test-driven-development`, `verification-before-completion`, `requesting-code-review`, `dispatching-parallel-agents`, `subagent-driven-development` | Ideas nuevas (brainstorming), errores (systematic-debugging), comprobar antes de decir «hecho», repartir trabajo entre agentes. |
| Agent skills | `agent-skills:spec`, `plan`, `build`, `test`, `review`, `ship`, `code-simplify`, `debugging-and-error-recovery`, `performance-optimization`, `security-and-hardening`, `idea-refine`, `interview-me`, `documentation-and-adrs` | Especificar, construir por pasos, revisar en 5 ejes, rendimiento y tamaño del .exe, afinar ideas, registrar decisiones. |
| Diseño | `impeccable:impeccable`, `ui-ux-pro-max:*` (ui-ux-pro-max, design, design-system, brand, ui-styling, banner-design, slides) | Todo lo visual: página del móvil, ventanas, iconos, marca, presentación. |
| Entender el código | `understand-anything:*` (understand, understand-chat, understand-explain, understand-diff, understand-onboard) | Explicar partes del código o el impacto de un cambio. |
| Revisión y limpieza | `code-review`, `simplify`, `security-review` | Antes de entregar cambios importantes. |
| Otras | `run` (ver la app funcionando), `dataviz` (gráficas), `anthropic-skills:*` (docx, pdf, pptx, xlsx) | Cuando la tarea lo pida. |

Nunca ejecutar el build del .exe por el usuario: lo genera GitHub Actions.
