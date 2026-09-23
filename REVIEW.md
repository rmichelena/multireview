# Multireview — Ronda 3 (fresh-eyes, post-fixes 26523be)

> Roster: openai/gpt-5.6-terra · zai/glm-5.3 · openrouter/deepseek/deepseek-v4.1-flash (3/3 — sin sustituciones)
> Target: `main@26523be` · Fecha: 2026-09-23 · Raw: 14 findings → 10 únicos tras dedup

**Tally: 2 High · 4 Medium · 4 Low**

## High

### H1. Watchdog consulta solo el agente por defecto — reviews de otro agente son invisibles (2/3 reviewers: openai/gpt-5.6-terra, openrouter/deepseek/deepseek-v4.1-flash)
`scripts/review-babysitter.py:89` — `query_sessions()` corre `openclaw sessions --json --limit all` sin `--agent`; con un orquestador no-default, los session keys de los revisores nunca aparecen: revisor caído sin artefacto queda `unknown` hasta deadline en vez de `lost`. Fix: derivar/declarar `agentId` desde `orchestratorSessionKey` y pasarlo a la query y al fallback de transcript; test de regresión con agente no-default.

### H2. `internal_errors` nunca se resetea — "3 consecutivos" es en realidad "3 totales" (2/3 reviewers: zai/glm-5.3, openrouter/deepseek/deepseek-v4.1-flash)
`scripts/review-babysitter.py:401` — los otros contadores se resetean en cada ciclo exitoso, pero `internal_errors` es acumulativo de por vida. Tres fallos transitorios separados por horas (p.ej. el race del stat, M2) matan una sesión sana con un falso `monitor-error` + wake. Contradice docstring y SKILL.md ("bounded consecutive"). Fix: resetear al final de cada poll exitoso + test.

## Medium

### M1. Validator acepta bloques vacíos y `NO FINDINGS` + texto arbitrario como artefacto válido (1/3 reviewers: openai/gpt-5.6-terra)
`scripts/review-babysitter.py:72` — `===FINDING===\n\n===END_FINDING===` pasa como válido; revisor caído a mitad de escritura cuenta como `done` y el panel cierra "completo". Fix: exigir campos obligatorios no vacíos por bloque (severity/title/file/line/reasoning/fix/trace) y sentinela estricto para cero findings.

### M2. `path.stat()` TOCTOU fuera de handler — FileNotFoundError alimenta `internal_errors` (1/3 reviewers: zai/glm-5.3)
`scripts/review-babysitter.py:62` — `is_file()` y `stat()` son llamadas separadas y solo `read_bytes` está protegida; el unlink entre medias (el revisor reescribiendo su artefacto) propaga excepción al guard externo. Amplifica H2. Fix: envolver toda la observación en `try/except OSError → ("missing", None)`.

### M3. Validator rechaza resúmenes válidos que mencionan el token `===FINDING===` (1/3 reviewers: openrouter/deepseek/deepseek-v4.1-flash)
`scripts/review-babysitter.py:75` — el chequeo de "stray markers" es substring sobre el remainder, que incluye el quality summary permitido. Un resumen que parafrasee el formato (p.ej. "wrote 1 ===FINDING=== block") marca el artefacto `invalid` → revisor `lost` → falsos `complete-with-losses`. Fix: buscar solo líneas marcador-exclusivas (`^\s*===(FINDING|END_FINDING)===\s*$`).

### M4. Config irrecuperable sin wake key → exit 1 → restart-loop silencioso de systemd (1/3 reviewers: openrouter/deepseek/deepseek-v4.1-flash)
`scripts/review-babysitter.py:373` — con config corrupta desde el arranque, `last_good_key=None` → wake no intenta nada → exit 1 → `Restart=on-failure` reinicia indefinidamente sin avisar a nadie, contradiciendo la garantía documentada. Fix: exit 0 (o no-restart) tras escribir `monitor-error` cuando no hay key, o presupuesto acotado de reinicios + log estridente.

## Low

### L1. Test H1b: override de `OPENCLAW_BIN` vía env es código muerto; el test puede invocar el CLI real (2/3 reviewers: zai/glm-5.3, openrouter/deepseek/deepseek-v4.1-flash)
`scripts/test-review-babysitter.py:205` — `OPENCLAW` se captura al importar; mutar `os.environ` después no hace nada. El rc==1 pasa por otra razón y el comentario miente. Fix: parchar `bs.OPENCLAW` directamente (con restore en finally) y asertar que el subprocess falló.

### L2. Sentinela `__duplicate__` nunca se consulta; el último duplicado gana silenciosamente (1/3 reviewers: zai/glm-5.3)
`scripts/review-babysitter.py:100` — un key duplicado (reciclado) puede clasificarse contra el registro stale; además colisión posible con sesiones llamadas `__duplicate__`. Fix: enrutar duplicados al camino de session-error o preferir el registro con timestamp más nuevo.

### L3. SKILL.md: fence de código roto en la sección Debugging (2/3 reviewers: zai/glm-5.3, openrouter/deepseek/deepseek-v4.1-flash)
`SKILL.md:~586-592` — comandos `ls -la`/`cat` sin fence y un ``` huérfano que absorbe la prosa siguiente. Fix: re-envolver en ```bash y borrar el fence sobrante.

### L4. Doc/code divergence: SKILL.md define `monitor-error` solo como "3 fallos de session-query o state-I/O" (1/3 reviewers: zai/glm-5.3)
`SKILL.md:~556` — en el código también disparan monitor-error los config-errors, write-errors y excepciones internas; documentar las cuatro fuentes y los campos `last*Error` de diagnóstico.

## Quality summaries del panel
- **Terra:** suite no cubre lookup multi-agente ni validez a nivel de schema del artefacto.
- **GLM:** la state machine core (two-poll terminality, locking, wake retries) es sólida y bien testeada en happy paths; el issue de mayor impacto es H2 amplificado por M2.
- **DeepSeek:** el falso-negativo del validator (M3) es el más probable en la práctica — degrada silenciosamente un revisor bueno a `lost`. Verificó contra el CLI real que las claims del SKILL sobre flags de `sessions`/`system event` son correctas.

## Observación operativa
El babysitter (`multireview-babysitter-edf61fff3d6d`) corrió ~5 min vía `systemd-run --user` y llegó a terminal (`round=done`, 3/3 models done) sin intervención — primer ciclo detached completo. El wake explícito sigue sin observarse como entrega de evento (los models muestran `wakeDelivered` ausente en runtime): la consolidación fue disparada otra vez por completion events. Pendiente de capturar un wake real.
